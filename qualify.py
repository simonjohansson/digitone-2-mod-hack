# SPDX-License-Identifier: GPL-2.0-or-later
"""Qualify one candidate firmware in the coupled emulator and on the unit.

Builds the candidate once, runs every case in DigiKit's coupled emulator,
flashes the same image to the unit and runs the cases again there. Both
targets are driven through the same DigiHealth keys, screens and RAM reads;
every check is an assertion on measured audio, pixels or RAM.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import wave
from contextlib import contextmanager
from pathlib import Path

import numpy as np

import build

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'out/qualify'
RATE = 48000
# OS 1.12: track 1's sound record, found by diffing emulator states and read back on the unit.
SOUND = 0x4210d0c0
SYNTH_TYPE, FILTER_TYPE = 0xde, 0xdf
# The MACHINE SEL lists, by type ID, in screen order. Neither list wraps.
LISTS = {'SYN': [0, 2, 1, 3, 6, 4], 'FLTR': [0, 1, 4, 3, 5, 2, 8]}
FM_TONE, SINE = 0, 6
MULTI_MODE, LOWPASS = 0, 8
NAMES = {('SYN', 0): 'FM TONE', ('SYN', 6): 'SINE', ('FLTR', 0): 'MULTI-MODE', ('FLTR', 8): 'LOWPASS'}
# Channel 1 CCs that open the stock filters, with the sound-record word each one sets:
# FREQ, RESO, TYPE (LP), ENV depth (neutral), key tracking, FLTR2 BASE and WIDTH.
OPEN = {16: (0x9a, 127), 17: (0x9c, 0), 18: (0x98, 0), 24: (0x9e, 64),
        26: (0xb2, 0), 27: (0xac, 0), 28: (0xae, 127)}
LOW, HIGH = 36, 84


def env_path(name):
    value = os.environ.get(name)
    if not value:
        raise SystemExit('%s is not set; see mise.local.example.toml' % name)
    return Path(value)


if os.environ.get('DIGIHEALTH'):  # the owner's DN2 DigiHealth checkout; the tests need only the measurements
    sys.path.insert(0, str(Path(os.environ['DIGIHEALTH']) / 'tools'))
    import digiusb


# ----- measurements ----------------------------------------------------------

def held(x):
    """The note's held part: the audible span without its first and last 50 ms."""
    loud = np.nonzero(np.abs(x) > 1e-4)[0]
    if len(loud) < RATE // 4:
        return x[:0]
    return x[loud[0] + RATE // 20:loud[-1] - RATE // 20]


def level_db(x):
    x = held(x)
    return 20 * np.log10(np.sqrt(np.mean(x ** 2))) if len(x) else -np.inf


def pitch_hz(x):
    x = held(x)
    rising = np.nonzero((x[:-1] < 0) & (x[1:] >= 0))[0]
    return RATE * (len(rising) - 1) / (rising[-1] - rising[0]) if len(rising) > 2 else 0.0


def pitch_of(synth, note):
    """FM TONE's default sound plays the note; SINE plays one octave above it."""
    return (2 if synth == SINE else 1) * 440 * 2 ** ((note - 69) / 12)


def lowpass_db(hz, a=1 / 16):
    """The example LOWPASS's gain: the one-pole y += a(x - y)."""
    w = 2 * np.pi * hz / RATE
    return 20 * np.log10(a / abs(1 - (1 - a) * np.exp(-1j * w)))


def labels_shown(screen):
    """Whether a page shows its control labels, in the two rows under the control cells."""
    return bool(screen[32:37, 22:125].any() or screen[59:64, 22:125].any())


def cells_empty(screen):
    """The lower four control cells hold only their dotted outlines (eight dots a row)."""
    return all(screen[y, 22:125].sum() <= 8 for y in range(41, 56))


def highlighted_row(screen):
    """The inverted list row (8 pixels high), without the scroll bar, or None."""
    for top in range(0, 64, 8):
        if screen[top:top + 8, :120].mean() > 0.5:
            return screen[top:top + 8, :120]
    return None


# ----- targets ---------------------------------------------------------------

class EmuPort:
    """DigiHealth's port interface over the emulator's USB MIDI, timed in emulated milliseconds."""

    def __init__(self, target):
        self.target = target

    def send(self, data):
        self.target.emu.usb_out()
        self.target.emu.usb_in(bytes(data))

    def receive(self, seconds=1.0, until=None):
        messages, pending = [], b''
        for _ in range(max(50, int(seconds * 100))):
            self.target.wait(10)
            pending += self.target.emu.usb_out()
            while b'\xf0' in pending and b'\xf7' in pending[pending.index(b'\xf0'):]:
                start = pending.index(b'\xf0')
                end = pending.index(b'\xf7', start) + 1
                messages.append(pending[start:end])
                pending = pending[end:]
            if until and until(messages):
                break
        return messages


class Target:
    """What a case needs: keys, screens, RAM, MIDI, time and audio."""

    def open(self):
        self.diag = digiusb.Diag(self.port)
        self.keys = digiusb.RemoteKeys(self.diag)

    def tap(self, *names):
        for key in names:
            self.keys.tap(key)
            self.wait(60)

    def chord(self, held_key, key):
        self.keys.press(held_key, 2000)
        self.keys.tap(key)
        self.keys.release(held_key)
        self.wait(100)

    def screen(self):
        """The OLED as a 64 x 128 array of 0/1, top row first."""
        _, pbm = digiusb.screen(self.diag)
        bits = pbm.split(b'\n', 2)[2].split()
        return np.array([b == b'1' for b in bits], dtype=np.uint8).reshape(64, 128)

    def peek(self, addr, length):
        return digiusb.peek(self.diag, addr, length)

    def machines(self):
        synth, filter_ = self.peek(SOUND + SYNTH_TYPE, 2)
        return synth, filter_

    def select(self, synth, filter_):
        """Select track 1's machines. Keys can be lost, so read the result back."""
        self.tap('STOP', 'STOP', 'NO', 'NO')
        self.chord('TRK', '1')
        for side, want, index in (('SYN', synth, 0), ('FLTR', filter_, 1)):
            order = LISTS[side]
            for attempt in range(6):
                have = self.machines()[index]
                if have == want:
                    break
                if attempt == 5:
                    raise AssertionError('%s %d not selected; have %d' % (side, want, have))
                # MACHINE SEL keeps its own cursor and does not wrap: park it at the top, then count down.
                self.tap('NO', 'NO')
                self.chord('FUNC', 'SYN')
                self.tap('LEFT' if side == 'SYN' else 'RIGHT', *['UP'] * (len(order) + 1))
                self.tap(*['DOWN'] * order.index(want), 'YES')
        self.tap('NO', 'NO')
        return self.machines() == (synth, filter_)

    def open_filters(self):
        """Set the stock filters wide open on track 1; True when the sound record agrees."""
        for cc, (_, value) in OPEN.items():
            self.midi(bytes([0xb0, cc, value]))
        record = self.peek(SOUND, 0x100)
        return all(record[offset] == value for offset, value in OPEN.values())

    def page(self, key):
        """A page's first screen: entering from another page lands on page 1."""
        self.tap('AMP' if key != 'AMP' else 'SYN', key)
        self.wait(1500)  # the title shows the pattern name and tempo first
        return self.screen()

    def note(self, note, ms=800, velocity=100):
        self.midi(bytes([0x90, note, velocity]))
        self.wait(ms)
        self.midi(bytes([0x80, note, 0]))
        self.wait(400)

    def close(self):
        try:
            self.midi(bytes([0xb0, 123, 0]))
            self.keys.cancel()
        finally:
            self.shutdown()


class Emulator(Target):
    name = 'emulator'
    INSTRUCTIONS_PER_MS = 132000

    def __init__(self, firmware, out):
        sys.path.insert(0, str(env_path('DIGIKIT')))
        from tools.control.digi_client import Emu
        self.out = out
        self.emu = Emu.spawn_coupled(env_path('DN2_STOCK'), env_path('DN2_EMU_READY'),
                                     binary=env_path('DIGI_EMUD'), timeout=300,
                                     run_dir=out / 'run', env={'SHARC_AOT': '0'})
        self.loaded = self.emu.call('firmware.load', syx=str(firmware), start='cold', force=True, auto_no=True)
        self.wait(300)
        self.port = EmuPort(self)
        self.open()

    def wait(self, ms):
        left = int(ms * self.INSTRUCTIONS_PER_MS)
        while left:
            step = min(left, self.INSTRUCTIONS_PER_MS * 500)
            self.emu.step(step, timeout_wall_s=300)
            left -= step

    def midi(self, data):
        self.emu.midi_in(data)
        self.wait(200)  # back-to-back DIN input is dropped

    def healthy(self):
        status = self.emu.status()
        return (not status['cf'].get('error') and status['cf']['phase'] == 'ready'
                and status['audio']['halted'] is None and status['audio']['missing_blocks'] == 0)

    @contextmanager
    def recording(self, name):
        path = self.out / (name + '.wav')
        self.emu.call('pcm.tap', action='capture', path=str(path))
        take = Take(path)
        try:
            yield take
        finally:
            self.emu.call('pcm.tap', action='capture_stop')
        take.samples = read_wav(path)

    def shutdown(self):
        try:
            self.emu.call('quit')
        finally:
            self.emu.close()


class Device(Target):
    name = 'device'

    def __init__(self, out):
        import mido
        import sounddevice
        from midimido import Port
        self.mido, self.sd, self.out = mido, sounddevice, out
        self.port = Port()
        self.audio_in = next(i for i, d in enumerate(sounddevice.query_devices())
                             if 'Digitone II' in d['name'] and d['max_input_channels'] >= 2)
        self.open()

    def wait(self, ms):
        time.sleep(ms / 1000)

    def midi(self, data):
        self.port.outgoing.send(self.mido.Message.from_bytes(data))
        self.wait(200)

    def healthy(self):
        return digiusb.decode_hello(self.diag.call(digiusb.req_hello))['name'].startswith('digihealth')

    def project_row(self):
        """The active project's row in LOAD PROJECT, which opens on it; the list is left open."""
        self.tap('NO', 'NO', 'NO', 'SETTINGS', *['UP'] * 8, 'YES', *['UP'] * 4, 'YES')
        self.wait(800)
        return highlighted_row(self.screen())

    def reload_project(self):
        """Load the active project again, discarding the run's unsaved changes."""
        row = self.project_row()
        self.tap('YES')
        self.wait(800)
        self.tap('YES')  # Any changes will be lost. Proceed with loading?
        self.wait(8000)
        self.tap('NO', 'NO', 'NO')
        return row

    @contextmanager
    def recording(self, name, seconds=8):
        audio = self.sd.rec(int(seconds * RATE), samplerate=RATE, channels=2,
                            device=self.audio_in, dtype='float32')
        started = time.monotonic()
        take = Take(self.out / (name + '.wav'))
        try:
            yield take
        finally:
            used = int((time.monotonic() - started + 0.3) * RATE)
            self.sd.wait()
        take.samples = audio[:used, 0].astype(np.float64)
        write_wav(take.path, audio[:used])

    def shutdown(self):
        self.port.close()


class Take:
    def __init__(self, path):
        self.path, self.samples = path, None


def read_wav(path):
    """The left channel of a float32 or 16-bit WAV."""
    raw = Path(path).read_bytes()
    fmt, p = None, 12
    while p + 8 <= len(raw):
        tag, size = raw[p:p + 4], int.from_bytes(raw[p + 4:p + 8], 'little')
        if tag == b'fmt ':
            fmt = np.frombuffer(raw[p + 8:p + 24], dtype='<u2')
        if tag == b'data':
            float32 = fmt[0] == 3
            x = np.frombuffer(raw[p + 8:p + 8 + size], dtype='<f4' if float32 else '<i2')
            x = x.reshape(-1, fmt[1])[:, 0].astype(np.float64)
            return x if float32 else x / 32768
        p += 8 + size + (size & 1)
    raise ValueError('%s has no PCM data' % path)


def write_wav(path, stereo):
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((np.clip(stereo, -1, 1) * 32767).astype('<i2').tobytes())


# ----- cases -----------------------------------------------------------------

class Checks:
    def __init__(self, target):
        self.target, self.results = target, []

    def __call__(self, name, ok, **details):
        self.results.append({'name': name, 'pass': bool(ok), **details})
        print('%-8s %s %s %s' % (self.target, 'PASS' if ok else 'FAIL', name,
                                 json.dumps(details) if details else ''), flush=True)
        return ok


def run_cases(t, check):
    """The four synth/filter combinations on track 1."""
    for synth in (SINE, FM_TONE):
        levels = {}
        for filter_ in (MULTI_MODE, LOWPASS):
            combo = '%s + %s' % (NAMES['SYN', synth], NAMES['FLTR', filter_])
            if not check(combo + ': selected', t.select(synth, filter_), machines=t.machines()):
                continue
            check(combo + ': stock filters open', t.open_filters())
            for note in (LOW, HIGH):
                name = '%s-%s-%d' % (NAMES['SYN', synth], NAMES['FLTR', filter_], note)
                with t.recording(name.replace(' ', '')) as take:
                    t.note(note)
                x = take.samples
                levels[filter_, note] = level_db(x)
                check('%s: note %d audible' % (combo, note), len(held(x)) and np.abs(x).max() > 5e-4,
                      peak=round(float(np.abs(x).max()), 5), level_db=round(levels[filter_, note], 2),
                      wav=take.path.name)
                hz, expected = pitch_hz(x), pitch_of(synth, note)
                check('%s: note %d pitch' % (combo, note), abs(hz / expected - 1) < 0.005,
                      hz=round(hz, 2), expected=round(expected, 2))
            screen = t.page('FLTR')
            custom = filter_ == LOWPASS
            check('%s: FLTR page %s' % (combo, 'has no controls' if custom else 'shows its controls'),
                  (not labels_shown(screen) and cells_empty(screen)) if custom else labels_shown(screen))
        # The stock filter is open, so its two notes differ only by the synth; LOWPASS takes
        # the high note down by its own response. Harmonics above the fundamental can add a little.
        drop = ((levels[LOWPASS, LOW] - levels[LOWPASS, HIGH])
                - (levels[MULTI_MODE, LOW] - levels[MULTI_MODE, HIGH]))
        expected = lowpass_db(pitch_of(synth, LOW)) - lowpass_db(pitch_of(synth, HIGH))
        check('%s: LOWPASS attenuates note %d against note %d by its response' % (NAMES['SYN', synth], HIGH, LOW),
              -2 <= drop - expected <= 3, extra_attenuation_db=round(float(drop), 2),
              expected_db=round(float(expected), 2))
    check('runs without faults', t.healthy())


# ----- the run ---------------------------------------------------------------

def label():
    """Four characters the unit shows for this build: Q and the minute, in base 36."""
    n, digits = int(time.time() // 60) % 36 ** 3, '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    return 'Q' + ''.join(digits[n // 36 ** i % 36] for i in (2, 1, 0))


def elektroid_index():
    listing = subprocess.run(['elektroid-cli', 'ld'], capture_output=True, text=True).stdout
    found = re.findall(r'(?m)^(\d+): id: [^\n]*Digitone II', listing)
    return found[0] if len(found) == 1 else None


def installed_version(index):
    info = subprocess.run(['elektroid-cli', 'info', index], capture_output=True, text=True).stdout
    match = re.search(r'Device version: (\S+)', info)
    return match and match.group(1)


def flash(firmware, version, index):
    """Transfer the candidate, confirm with the installed DigiHealth's remote YES, wait for the new version."""
    build.run(['elektroid-cli', 'upgrade', firmware, index])
    from midimido import Port
    port = Port()
    try:
        keys = digiusb.RemoteKeys(digiusb.Diag(port))
        keys.tap('YES')
        keys.cancel()
    finally:
        port.close()
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        time.sleep(5)
        index = elektroid_index() or index
        if installed_version(index) == version:
            time.sleep(5)  # DigiHealth answers shortly after USB
            return True
    return False


def scratch_file():
    return OUT / 'scratch-project.json'


def device_run(firmware, version, args, out):
    index = elektroid_index()
    if not index:
        return None
    check = Checks('device')
    if args.flash:
        check('candidate installed', flash(firmware, version, index), version=version)
    else:
        check('candidate installed', installed_version(index) == version, version=version)
    if not check.results[-1]['pass']:
        return check.results
    t = Device(out)
    try:
        row = t.project_row()
        t.tap('NO', 'NO', 'NO')
        remembered = scratch_file().exists() and json.loads(scratch_file().read_text())['row']
        if not check('the scratch project is loaded', row is not None and row.tolist() == remembered):
            return check.results
        before = t.peek(SOUND, 0x100)
        try:
            run_cases(t, check)
        finally:
            t.reload_project()
            check('the scratch project is restored', t.peek(SOUND, 0x100) == before)
    finally:
        t.close()
    return check.results


def emulator_run(firmware, out):
    check = Checks('emulator')
    t = Emulator(firmware, out)
    try:
        check('candidate loaded', t.loaded['syx_sha256'] == build.sha(firmware), start=t.loaded['start_used'])
        run_cases(t, check)
    finally:
        t.close()
    return check.results


def sources():
    def git(*args, cwd=ROOT):
        return subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True).stdout.strip()
    return {'hack': git('rev-parse', 'HEAD'), 'hack_dirty': bool(git('status', '--porcelain')),
            'elekloader': git('rev-parse', 'HEAD', cwd=build.VENDOR),
            'elekloader_dirty': bool(git('status', '--porcelain', cwd=build.VENDOR))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--targets', default='emulator,device')
    parser.add_argument('--no-build', dest='build', action='store_false',
                        help='use the existing out/c/firmware.syx')
    parser.add_argument('--no-flash', dest='flash', action='store_false',
                        help='the unit already runs this candidate')
    parser.add_argument('--remember-scratch', action='store_true',
                        help='record the loaded project as the scratch project the device run may change')
    args = parser.parse_args()
    env_path('DIGIHEALTH')
    OUT.mkdir(parents=True, exist_ok=True)
    if args.remember_scratch:
        t = Device(OUT)
        try:
            row = t.project_row()
            t.tap('NO', 'NO', 'NO')
        finally:
            t.close()
        build.require(row is not None, 'LOAD PROJECT shows no highlighted project')
        scratch_file().write_text(json.dumps({'row': row.tolist()}) + '\n')
        print('remembered the loaded project as the scratch project')
        return 0
    firmware = ROOT / 'out/c/firmware.syx'
    if args.build:
        build.run([sys.executable, ROOT / 'build.py', '--version', label()])
    version = json.loads((ROOT / 'out/c/firmware.syx.json').read_text())['version']
    report = {'firmware': str(firmware), 'sha256': build.sha(firmware), 'version': version,
              'sources': sources(), 'targets': {}}
    targets = args.targets.split(',')
    for name in targets:
        out = OUT / name
        out.mkdir(exist_ok=True)
        results = emulator_run(firmware, out) if name == 'emulator' else device_run(firmware, version, args, out)
        report['targets'][name] = results if results is not None else 'skipped: no unit'
        (OUT / 'report.json').write_text(json.dumps(report, indent=1) + '\n')
    failed = [r['name'] for results in report['targets'].values() if isinstance(results, list)
              for r in results if not r['pass']]
    skipped = [name for name, results in report['targets'].items() if not isinstance(results, list)]
    print('report:', OUT / 'report.json')
    print('firmware %s %s' % (version, report['sha256']))
    if failed:
        print('FAILED: %d checks' % len(failed))
        return 1
    if skipped or set(targets) != {'emulator', 'device'}:
        print('INCOMPLETE: %s not run' % ', '.join(skipped or sorted({'emulator', 'device'} - set(targets))))
        return 2
    print('PASSED in the emulator and on the device')
    return 0


if __name__ == '__main__':
    sys.exit(main())
