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
# Track n's follows at a stride of 0x48b bytes (found by each MIDI channel's FREQ).
SOUND, STRIDE = 0x4210d0c0, 0x48b
VOL = (0xc8, 90)  # AMP VOL: its sound-record byte and channel CC
SYNTH_TYPE, FILTER_TYPE = 0xde, 0xdf
# The MACHINE SEL lists, by type ID, in screen order. Neither list wraps.
LISTS = {'SYN': [0, 2, 1, 3, 6, 4], 'FLTR': [0, 1, 4, 3, 5, 2, 8]}
FM_TONE, SINE = 0, 6
MULTI_MODE, LOWPASS_4, LOWPASS = 0, 1, 8
NAMES = {('SYN', 0): 'FM TONE', ('SYN', 6): 'SINE', ('FLTR', 0): 'MULTI-MODE', ('FLTR', 8): 'LOWPASS'}
# Channel 1 CCs that open the stock filters, with the sound-record word each one sets:
# FREQ, RESO, TYPE (LP), ENV depth (neutral), key tracking, FLTR2 BASE and WIDTH.
OPEN = {16: (0x9a, 127), 17: (0x9c, 0), 18: (0x98, 0), 24: (0x9e, 64),
        26: (0xb2, 0), 27: (0xac, 0), 28: (0xae, 127)}
LOW, HIGH = 36, 84
CUTOFF = 72  # LOWPASS's FREQ in the combinations: 523 Hz
# LOWPASS 4's controls, which the custom filter's page shows: the sound-record word,
# channel 1 CC, NRPN 1:n and encoder 1..8 (A..H). Pre-delay is FUNC+A.
CONTROLS = {'ATK': (0xa2, 20, 16, 1), 'DEC': (0xa4, 21, 17, 2), 'SUS': (0xa6, 22, 18, 3),
            'REL': (0xa8, 23, 19, 4), 'FREQ': (0x9a, 16, 20, 5), 'RESO': (0x9c, 17, 21, 6),
            'ENV': (0x9e, 24, 26, 8), 'DELAY': (0xa0, 19, 23, None)}
GRAPH = np.s_[40:57, 21:72]  # FLTR page 1's response graph, with its frame
# Other channel-1 CCs the cases set: key tracking, and LFO 1 (its destination is a parameter slot:
# FREQ 67, RESO 68, ENV 69, pre-delay 70, ATK..REL 71..74). Key tracking at 127 adds 0.0826 to the
# cutoff (10.57 FREQ) per octave above note 48. WAVE 0 is a triangle and 2 a square; MODE 0 runs
# free and 1 restarts on each note; SPD 64 holds the LFO still and DEP 64 is no depth.
CCS = {**{name: cc for name, (_, cc, _, _) in CONTROLS.items()}, 'KT': 26,
       'SPD': 102, 'MULT': 103, 'DEST': 105, 'WAVE': 106, 'MODE': 108, 'DEP': 109}
KT_PER_OCTAVE = 0.0826 * 128
REST = {'FREQ': 127, 'RESO': 0, 'ENV': 64, 'ATK': 0, 'DEC': 64, 'SUS': 0, 'REL': 64, 'DELAY': 0,
        'KT': 0, 'DEST': 0, 'DEP': 64}
SAVE_SLOT = 128  # the last project slot, which the run saves into; the list does not wrap


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


def svf_db(hz, freq, reso=0):
    """The example LOWPASS's gain at HZ for FREQ and RESO (0..127), with the envelope at rest:
    its cutoff read like a MIDI note, its Pade tan and its 2-pole topology-preserving response."""
    cutoff = min(max(440 * 2 ** ((freq - 69) / 12), 20), 16000)
    x = np.pi * cutoff / RATE
    g = x * (15 - x * x) / (15 - 6 * x * x)
    k, t = 2 - 1.96 * reso / 128, np.tan(np.pi * hz / RATE) / g
    return -10 * np.log10((1 - t * t) ** 2 + (k * t) ** 2)


def tone_db(x, hz):
    """The level of one sinusoid in the held note, fitted by least squares."""
    x = held(x)
    if not len(x):
        return -np.inf
    n = 2 * np.pi * hz * np.arange(len(x)) / RATE
    fit = np.linalg.lstsq(np.stack([np.sin(n), np.cos(n)], 1), x, rcond=None)[0]
    return 20 * np.log10(np.hypot(*fit) / np.sqrt(2))


def onsets(x, gap_ms=50):
    """Where sound starts after at least GAP_MS of silence, in samples."""
    loud = np.nonzero(np.abs(x) > 1e-4)[0]
    gap = RATE * gap_ms // 1000
    return [int(b) for a, b in zip(np.r_[-gap - 1, loud[:-1]], loud) if b - a > gap]


def window_db(x, ms=20):
    """Levels of consecutive windows over the audible part, in dB."""
    loud = np.nonzero(np.abs(x) > 1e-4)[0]
    if len(loud) < RATE // 4:
        return np.array([])
    x, n = x[loud[0]:loud[-1]], RATE * ms // 1000
    return np.array([20 * np.log10(np.sqrt(np.mean(x[i:i + n] ** 2)) + 1e-12)
                     for i in range(0, len(x) - n, n)])


def labels_shown(screen):
    """Whether a page shows its control labels, in the two rows under the control cells."""
    return bool(screen[32:37, 22:125].any() or screen[59:64, 22:125].any())


PEAK_DB = (0, 1, 2, 4, 6, 8, 12, 17)  # as examples/sharc-filter/machine.c


def curve_dots(freq, reso):
    """The screen dots the example LOWPASS draws in the FLTR graph for the shown FREQ and RESO words."""
    cutoff, peak, dots = freq >> 8, PEAK_DB[(reso >> 8) >> 4], set()
    for x in range(51):
        d = x * 127 // 50 - cutoff
        db = (peak * (12 + d) // 12 if d > -12 else 0) if d <= 0 else -d + (peak * (12 - d) // 12 if d < 12 else 0)
        if db >= -36:
            dots.add((21 + x, 56 - min((db + 36) // 3, 16)))
    return dots


def graph_dots(screen):
    """The lit pixels in the FLTR graph, as screen (x, y)."""
    ys, xs = np.nonzero(screen[GRAPH])
    return {(int(x) + GRAPH[1].start, int(y) + GRAPH[0].start) for y, x in zip(ys, xs)}


def page_as_stock(custom, stock, dots):
    """The custom filter's page is the stock page, with its own curve's DOTS in the response graph."""
    outside = np.ones(custom.shape, dtype=bool)
    outside[GRAPH] = False
    return bool(stock[GRAPH].any() and graph_dots(custom) == dots
                and (custom[outside] == stock[outside]).all())


def empty_slot(row):
    """Whether a highlighted project row shows an empty slot: thirteen slashes for a name."""
    slashes = np.zeros((7, 90), dtype=np.uint8)
    for y in (2, 3, 4):
        slashes[y, 8 - y + 4 * np.arange(13)] = 1
    return bool(((1 - row[:7, 30:]) == slashes).all())


def same_slot(a, b):
    """Two project rows show the same slot number."""
    a, b = np.asarray(a), np.asarray(b)
    return a.shape == b.shape and bool((a[:, :30] == b[:, :30]).all())


def same_project(a, b):
    """Two project rows show the same slot and name, whichever is active (its marker is at x 33-34)."""
    return same_slot(a, b) and bool((np.asarray(a)[:, 36:] == np.asarray(b)[:, 36:]).all())


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

    def lock(self, step, encoder, detents, holds=3):
        """Hold a trig in grid recording and turn an encoder. In each hold the first turn opens the
        lock at its value and the second moves it by DETENTS; short holds stay inside DigiHealth's
        hold limit. The audio shows whether it took."""
        self.tap('RECORD')
        for _ in range(holds):
            self.keys.press(str(step), 2000)
            self.wait(100)
            for _ in range(2):
                self.keys.turn(encoder, detents)
                self.wait(150)
            self.keys.release(str(step))
            self.wait(200)
        self.tap('RECORD')

    def pattern(self, name, bars=2):
        """Record the pattern playing from its start, at 120 BPM."""
        with self.recording(name, seconds=2 * bars + 3) as recorded:
            self.tap('PLAY')
            self.wait(2000 * bars + 300)
            self.tap('STOP', 'STOP')
            self.wait(600)
        return recorded.samples

    def word(self, offset):
        return int.from_bytes(self.peek(SOUND + offset, 2), 'big')

    def nrpn(self, msb, lsb, value):
        for cc, v in ((99, msb), (98, lsb), (6, value >> 7), (38, value & 127)):
            self.midi(bytes([0xb0, cc, v]))

    def turn(self, encoder, detents, offset, func=False):
        """Turn an encoder, with FUNC held for its second function; True when the word moved that way."""
        for _ in range(3):  # a turn can be lost, like a key
            before = self.word(offset)
            if func:
                self.keys.press('FUNC', 2000)
            self.keys.turn(encoder, detents)
            if func:
                self.keys.release('FUNC')
            self.wait(300)
            after = self.word(offset)
            if after != before:
                return (after > before) == (detents > 0)
        return False

    def machines(self, track=1):
        synth, filter_ = self.peek(SOUND + STRIDE * (track - 1) + SYNTH_TYPE, 2)
        return synth, filter_

    def select(self, synth, filter_, track=1):
        """Select a track's machines. Keys can be lost, so read the result back."""
        self.tap('STOP', 'STOP', 'NO', 'NO')
        self.chord('TRK', str(track))
        for side, want, index in (('SYN', synth, 0), ('FLTR', filter_, 1)):
            order = LISTS[side]
            for attempt in range(6):
                have = self.machines(track)[index]
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
        return self.machines(track) == (synth, filter_)

    def project_list(self, item):
        """Open PROJECT's LOAD PROJECT (0) or SAVE PROJECT AS (1) list."""
        self.tap('NO', 'NO', 'NO', 'SETTINGS', *['UP'] * 8, 'YES', *['UP'] * 4, *['DOWN'] * item, 'YES')
        self.wait(800)

    def project_row(self):
        """The active project's row in LOAD PROJECT, which opens on it; the list is left open."""
        self.project_list(0)
        return highlighted_row(self.screen())

    def load_project(self, row=None):
        """Load the active project, or the one ROW shows, discarding unsaved changes; its row, or None."""
        here = self.project_row()
        if row is not None:
            for key in ['UP'] * SAVE_SLOT + ['DOWN'] * SAVE_SLOT:  # up to the first slot, then down
                if here is not None and same_project(here, row):
                    break
                self.tap(key)
                here = highlighted_row(self.screen())
            else:
                self.tap('NO', 'NO', 'NO')
                return None
        self.tap('YES')
        self.wait(800)
        self.tap('YES')  # Any changes will be lost. Proceed with loading?
        self.wait(8000)
        self.tap('NO', 'NO', 'NO')
        return here

    def save_project(self):
        """SAVE PROJECT AS into the save slot under the offered name; the slot's row before, or None."""
        self.project_list(1)
        self.tap(*['DOWN'] * (SAVE_SLOT + 12))
        row = highlighted_row(self.screen())
        self.tap('DOWN', 'DOWN')  # at the last slot these change nothing
        if row is None or not np.array_equal(row, highlighted_row(self.screen())) or not self.may_overwrite(row):
            self.tap('NO', 'NO', 'NO')
            return None
        for _ in range(3 if not empty_slot(row) else 2):  # overwrite 'NAME'? Y/N, the name editor, YES:SAVE
            self.tap('YES')
            self.wait(800)
        self.wait(15000)  # keys are not served while the project is written
        self.tap('NO', 'NO', 'NO')
        active = self.project_row()  # a saved project becomes the active one
        self.tap('NO', 'NO', 'NO')
        return row if active is not None and same_slot(active, row) else None

    def open_filters(self, track=1):
        """Set a track's stock filters wide open; True when its sound record agrees."""
        for cc, (_, value) in OPEN.items():
            self.midi(bytes([0xb0 | (track - 1), cc, value]))
        record = self.peek(SOUND + STRIDE * (track - 1), 0x100)
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

    def may_overwrite(self, row):
        return True  # the emulated drive holds only what runs saved

    def healthy(self):
        status = self.emu.status()
        return (not status['cf'].get('error') and status['cf']['phase'] == 'ready'
                and status['audio']['halted'] is None and status['audio']['missing_blocks'] == 0)

    @contextmanager
    def recording(self, name, seconds=None):  # the capture runs until stopped
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

    def may_overwrite(self, row):
        """Only an empty save slot, or the project an earlier run saved there."""
        saved = scratch().get('save_row')
        return empty_slot(row) or saved is not None and same_project(row, saved)

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


def run_cases(t, check, cases=None):
    """The named CASES (all by default), then the health check; the saved project's row, if saved."""
    saved = None
    for name, case in CASES.items():
        if cases is None or name in cases:
            result = case(t, check)
            if name == 'save':
                saved = result
    check('runs without faults', t.healthy())
    return saved


def run_combinations(t, check):
    """The four synth/filter combinations on track 1."""
    for synth in (SINE, FM_TONE):
        levels = {}
        for filter_ in (MULTI_MODE, LOWPASS):
            combo = '%s + %s' % (NAMES['SYN', synth], NAMES['FLTR', filter_])
            if not check(combo + ': selected', t.select(synth, filter_), machines=t.machines()):
                continue
            check(combo + ': stock filters open', t.open_filters())
            if filter_ == LOWPASS:
                t.midi(bytes([0xb0, 16, CUTOFF]))
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
            check('%s: FLTR page shows its controls' % combo, labels_shown(t.page('FLTR')))
        # The stock filter is open, so its two notes differ only by the synth; LOWPASS takes
        # the high note down by its own response. Harmonics above the fundamental can add a little.
        drop = ((levels[LOWPASS, LOW] - levels[LOWPASS, HIGH])
                - (levels[MULTI_MODE, LOW] - levels[MULTI_MODE, HIGH]))
        expected = svf_db(pitch_of(synth, LOW), CUTOFF) - svf_db(pitch_of(synth, HIGH), CUTOFF)
        check('%s: LOWPASS attenuates note %d against note %d by its response' % (NAMES['SYN', synth], HIGH, LOW),
              -2 <= drop - expected <= 3, extra_attenuation_db=round(float(drop), 2),
              expected_db=round(float(expected), 2))


def run_controls(t, check):
    """The custom filter's page and controls are LOWPASS 4's: pixels, knobs, CC and NRPN."""
    if not check('SINE + LOWPASS 4: selected', t.select(SINE, LOWPASS_4), machines=t.machines()):
        return
    for cc, value in ((16, 64), (17, 0)):  # FREQ, RESO
        t.midi(bytes([0xb0, cc, value]))
    stock = t.page('FLTR')
    if not check('SINE + LOWPASS: selected again', t.select(SINE, LOWPASS), machines=t.machines()):
        return
    check("LOWPASS: FLTR page is LOWPASS 4's with its own curve", page_as_stock(t.page('FLTR'), stock, curve_dots(64 << 8, 0)))
    for name, (offset, cc, nrpn, encoder) in CONTROLS.items():
        t.midi(bytes([0xb0, cc, 64]))
        knob = 'FUNC+A' if encoder is None else 'encoder ' + 'ABCDEFGH'[encoder - 1]
        check('LOWPASS %s: %s turns it up' % (name, knob), t.turn(encoder or 1, 10, offset, func=encoder is None),
              word=hex(t.word(offset)))
        t.midi(bytes([0xb0, cc, 33]))
        check('LOWPASS %s: CC %d sets it' % (name, cc), t.word(offset) >> 8 == 33, word=hex(t.word(offset)))
        t.nrpn(1, nrpn, 77 << 7)
        check('LOWPASS %s: NRPN 1:%d sets it' % (name, nrpn), t.word(offset) >> 8 == 77, word=hex(t.word(offset)))


def play(t, name, note=60, **values):
    """Record one held note on track 1 with the filter controls at REST except VALUES."""
    for control, value in {**REST, **values}.items():
        t.midi(bytes([0xb0, CCS[control], value]))
    with t.recording(name) as recorded:
        t.note(note)
    return recorded.samples


def run_response(t, check):
    """The kernel follows FREQ, RESO and the stock filter envelope (ENV, ATK, pre-delay)."""
    if not check('SINE + LOWPASS: selected for its response', t.select(SINE, LOWPASS), machines=t.machines()):
        return
    t.open_filters()
    hz = pitch_of(SINE, 60)

    def take(name, **values):
        return play(t, 'LOWPASS-' + name, **values)

    open_db = level_db(take('open'))
    for freq, reso in ((50, 0), (64, 0), (72, 0), (90, 0), (72, 100), (72, 120)):
        got = level_db(take('f%d-r%d' % (freq, reso), FREQ=freq, RESO=reso)) - open_db
        expected = svf_db(hz, freq, reso) - svf_db(hz, 127)
        check('LOWPASS FREQ %d RESO %d: note 60 at its response' % (freq, reso), abs(got - expected) <= 1.5,
              level_db=round(float(got), 2), expected_db=round(float(expected), 2))
    # The envelope moves the cutoff over a held note. Compare 20 ms windows from 60 to 760 ms
    # (the note ends at 800) with the same note through a fixed cutoff, which cancels SINE's drift.
    body = slice(3, 38)
    fixed = window_db(take('f110', FREQ=110))
    rest = window_db(take('env-0', FREQ=50))
    rise = window_db(take('env-up', FREQ=50, ENV=127))
    slow = window_db(take('env-up-atk', FREQ=50, ENV=127, ATK=40))
    late = window_db(take('env-up-delay', FREQ=50, ENV=127, DELAY=60))
    dip = window_db(take('env-down', FREQ=110, ENV=0))
    if not check('LOWPASS envelope notes audible', all(len(w) >= 40 for w in (fixed, rest, rise, slow, late, dip))):
        return
    fixed, rest, rise, slow, late, dip = (w[body] for w in (fixed, rest, rise, slow, late, dip))
    flat = np.ptp(rest - fixed)
    check('LOWPASS ENV 0: the cutoff stays put', flat <= 1, range_db=round(float(flat), 2))
    floor = np.median(rest - fixed)
    up = rise - fixed - floor
    check('LOWPASS ENV +63: starts open and closes', up[0] >= 10 and up[-1] <= up[0] - 6,
          start_db=round(float(up[0]), 2), end_db=round(float(up[-1]), 2))
    down = dip - fixed
    check('LOWPASS ENV -64: starts closed and opens', down[0] <= -10 and down[-1] >= down[0] + 6,
          start_db=round(float(down[0]), 2), end_db=round(float(down[-1]), 2))
    peak_ms = [20 * int(np.argmax(w - fixed)) for w in (rise, slow)]
    check('LOWPASS ATK 40: the envelope peaks later', peak_ms[1] >= peak_ms[0] + 100, peak_ms=peak_ms)
    opened_ms = [20 * int(np.argmax(w - fixed - floor >= 10)) for w in (rise, late)]
    check('LOWPASS pre-delay 60: the envelope starts later', opened_ms[1] >= opened_ms[0] + 40, opened_ms=opened_ms)


def run_tracks(t, check):
    """Two tracks with the custom filter, played together, each follow their own FREQ."""
    if not (check('tracks 1 and 2: SINE + LOWPASS selected', t.select(SINE, LOWPASS, 1) and t.select(SINE, LOWPASS, 2),
                  machines=[t.machines(1), t.machines(2)])
            and check('tracks 1 and 2: filters open', t.open_filters(1) and t.open_filters(2))):
        return
    notes = {1: 60, 2: 72}
    levels = {}
    for name, freqs in (('open', {1: 127, 2: 127}), ('set', {1: 50, 2: 110})):
        for track, freq in freqs.items():
            t.midi(bytes([0xb0 | (track - 1), 16, freq]))
        with t.recording('TRACKS-' + name) as recorded:
            for track, note in notes.items():
                t.midi(bytes([0x90 | (track - 1), note, 100]))
            t.wait(800)
            for track, note in notes.items():
                t.midi(bytes([0x80 | (track - 1), note, 0]))
            t.wait(400)
        levels[name] = {track: tone_db(recorded.samples, pitch_of(SINE, note)) for track, note in notes.items()}
    for track, freq in ((1, 50), (2, 110)):
        hz = pitch_of(SINE, notes[track])
        got = levels['set'][track] - levels['open'][track]
        expected = svf_db(hz, freq) - svf_db(hz, 127)
        check('track %d: its note follows its own FREQ %d' % (track, freq), abs(got - expected) <= 1.5,
              level_db=round(float(got), 2), expected_db=round(float(expected), 2))
    # An LFO on track 1's FREQ moves its note and leaves track 2's alone.
    for control, value in {'DEST': 67, 'WAVE': 0, 'MODE': 0, 'SPD': 127, 'MULT': 4, 'DEP': 127}.items():
        t.midi(bytes([0xb0, CCS[control], value]))
    with t.recording('TRACKS-lfo') as recorded:
        for track, note in notes.items():
            t.midi(bytes([0x90 | (track - 1), note, 100]))
        t.wait(800)
        for track, note in notes.items():
            t.midi(bytes([0x80 | (track - 1), note, 0]))
        t.wait(400)
    t.midi(bytes([0xb0, CCS['DEP'], 64]))
    moved = {track: tone_db(recorded.samples, pitch_of(SINE, note)) - levels['set'][track] for track, note in notes.items()}
    check("an LFO on track 1 moves its note and leaves track 2's", abs(moved[1]) >= 3 and abs(moved[2]) <= 0.5,
          track1_db=round(float(moved[1]), 2), track2_db=round(float(moved[2]), 2))


def run_downstream(t, check):
    """FLTR2 BASE and AMP VOL change LOWPASS's output as they change MULTI-MODE's."""
    vol = t.peek(SOUND + VOL[0], 1)[0]
    changes = {}
    for filter_ in (MULTI_MODE, LOWPASS):
        if not check('SINE + %s: selected for FLTR2 and AMP' % NAMES['FLTR', filter_], t.select(SINE, filter_),
                     machines=t.machines()):
            return
        t.open_filters()
        levels = []
        for name, cc, value in (('ref', 27, 0), ('base', 27, 64), ('vol', VOL[1], vol // 2)):
            t.midi(bytes([0xb0, 27, 0]))
            t.midi(bytes([0xb0, VOL[1], vol]))
            t.midi(bytes([0xb0, cc, value]))
            with t.recording('%s-%s' % (NAMES['FLTR', filter_], name)) as recorded:
                t.note(LOW)
            levels.append(level_db(recorded.samples))
        t.midi(bytes([0xb0, VOL[1], vol]))
        changes[filter_] = (levels[1] - levels[0], levels[2] - levels[0])
    for i, name in enumerate(('FLTR2 BASE 64', 'AMP VOL halved')):
        stock, custom = changes[MULTI_MODE][i], changes[LOWPASS][i]
        check('LOWPASS: %s changes it as it changes MULTI-MODE' % name,
              stock <= -3 and abs(custom - stock) <= 0.5, stock_db=round(float(stock), 2), lowpass_db=round(float(custom), 2))


def run_curve(t, check):
    """LOWPASS's graph follows FREQ and RESO edits: exactly the dots its curve gives."""
    if not check('SINE + LOWPASS: selected for its curve', t.select(SINE, LOWPASS), machines=t.machines()):
        return
    t.page('FLTR')
    for freq, reso in ((40, 0), (100, 0), (72, 120)):
        t.midi(bytes([0xb0, 16, freq]))
        t.midi(bytes([0xb0, 17, reso]))
        t.wait(300)
        got, want = graph_dots(t.screen()), curve_dots(freq << 8, reso << 8)
        check('LOWPASS FREQ %d RESO %d: the graph draws its curve' % (freq, reso), got == want,
              missing=len(want - got), extra=len(got - want))


def run_routes(t, check):
    """Key tracking and LFO 1 reach the custom filter through the stock cutoff."""
    if not check('SINE + LOWPASS: selected for its routes', t.select(SINE, LOWPASS), machines=t.machines()):
        return
    t.open_filters()
    for note in (60, 72):
        hz = pitch_of(SINE, note)
        got = level_db(play(t, 'KT127-%d' % note, note, FREQ=64, KT=127)) - level_db(play(t, 'KT0-%d' % note, note, FREQ=64))
        expected = svf_db(hz, 64 + KT_PER_OCTAVE * (note - 48) / 12) - svf_db(hz, 64)
        check('LOWPASS key tracking: note %d follows its response' % note, abs(got - expected) <= 1.5,
              level_db=round(float(got), 2), expected_db=round(float(expected), 2))
    body = slice(3, 38)
    still = window_db(play(t, 'LFO-none', FREQ=64))[body]
    moving = window_db(play(t, 'LFO-FREQ', FREQ=64, DEST=67, WAVE=0, MODE=0, SPD=127, MULT=4, DEP=127))[body]
    swing = np.ptp(moving - still) if len(moving) == len(still) else 0
    check('LOWPASS: a free LFO on FREQ swings the note', swing >= 10, swing_db=round(float(swing), 2))
    # A still square restarted on each note offsets its destination for the whole note. The attack
    # is instant except for ATK's own case, so that the note reaches decay and sustain.
    for name, slot in (('RESO', 68), ('ENV', 69), ('DELAY', 70), ('ATK', 71), ('DEC', 72), ('SUS', 73), ('REL', 74)):
        base = {'FREQ': 50, 'RESO': 64, 'ENV': 100, 'ATK': 30 if name == 'ATK' else 0, 'DEC': 50, 'SUS': 60,
                'REL': 50, 'DELAY': 20}
        plain = window_db(play(t, 'LFO-%s-none' % name, **base))
        w = window_db(play(t, 'LFO-' + name, **base, DEST=slot, WAVE=2, MODE=1, SPD=64, DEP=127))
        n = min(len(w), len(plain))
        change = float(np.max(np.abs(w[:n] - plain[:n]))) if n else 0
        check('LOWPASS: an LFO on %s changes the note' % ('pre-delay' if name == 'DELAY' else name), change >= 3,
              change_db=round(change, 2))


def run_locks(t, check):
    """Parameter locks on step 9 change that step's note and leave step 1's alone."""
    if t.name == 'device':
        # On the unit, DigiHealth's remote turns leave a held trig's lock unchanged; the emulator only, for now.
        print('device   SKIP parameter locks: checked in the emulator only', flush=True)
        return
    if not check('SINE + LOWPASS: selected for locks', t.select(SINE, LOWPASS), machines=t.machines()):
        return
    t.open_filters()
    t.page('FLTR')
    if not check("the scratch pattern has no trigs", not onsets(t.pattern('LOCKS-empty'))):
        return
    t.tap('RECORD', '1', '9', 'RECORD')
    x = t.pattern('LOCKS-trigs')
    starts = [round((s - onsets(x)[0]) / RATE, 3) for s in onsets(x)]
    # Two bars, and the next bar's step 1 may start before STOP.
    if not check('steps 1 and 9 play', len(starts) in (4, 5) and all(abs(a - b) < 0.02 for a, b in zip(starts, range(5))),
                 onsets_s=starts):
        return

    def steps(x):  # 20 ms windows over 600 ms from each of the two notes' starts
        n = RATE // 50
        return [20 * np.log10(np.sqrt(np.mean(x[s:s + 30 * n].reshape(30, n) ** 2, axis=1)) + 1e-9)
                for s in onsets(x)[:2] if s + 30 * n <= len(x)]

    def change(a, b):
        n = min(len(a), len(b))
        return float(np.max(np.abs(a[:n] - b[:n]))) if n else np.inf

    # Each control gets a setting where its lock is clearly audible, and its own before and after.
    # Locks are absolute, so earlier locks on step 9 stay; ATK goes last, as a slow attack would
    # keep the short note from reaching decay and sustain.
    sweep = {'FREQ': 40, 'RESO': 0, 'ENV': 127, 'ATK': 0, 'DEC': 5, 'SUS': 0, 'REL': 0}
    for name, encoder, setting in (('RESO', 6, {'FREQ': 72, 'RESO': 0, 'ENV': 64}),
                                   ('ENV', 8, {**sweep, 'ENV': 70}), ('DEC', 2, sweep), ('SUS', 3, sweep),
                                   ('REL', 4, {**sweep, 'DEC': 127, 'SUS': 127}), ('ATK', 1, sweep)):
        for control, value in {**REST, **setting}.items():
            t.midi(bytes([0xb0, CCS[control], value]))
        before = steps(t.pattern('LOCKS-%s-before' % name, bars=1))
        t.lock(9, encoder, 30)
        after = steps(t.pattern('LOCKS-' + name, bars=1))
        step1, step9 = change(before[0], after[0]), change(before[1], after[1])
        check('LOWPASS: %s locked on step 9 changes that step only' % name, step9 >= 1 and step1 <= 0.5,
              step9_db=round(step9, 2), step1_db=round(step1, 2))
        if name == 'RESO':  # holding the step shows its lock: the graph draws the locked RESO
            plain = graph_dots(t.page('FLTR'))
            t.tap('RECORD')
            t.keys.press('9', 2000)
            t.wait(300)
            held = graph_dots(t.screen())
            t.keys.release('9')
            t.wait(200)
            t.tap('RECORD')
            check("LOWPASS: holding step 9 draws its locked RESO's curve", held != plain and len(held) > 20,
                  plain=len(plain), held=len(held))


def run_switch(t, check):
    """Back on LOWPASS after MULTI-MODE, the first note starts clean: it matches a repeat of it."""
    settings = {'FREQ': 60, 'RESO': 110}
    if not check('SINE + LOWPASS: selected before switching', t.select(SINE, LOWPASS), machines=t.machines()):
        return
    t.open_filters()
    play(t, 'SWITCH-custom', **settings)
    if not check('SINE + MULTI-MODE: selected in between', t.select(SINE, MULTI_MODE), machines=t.machines()):
        return
    play(t, 'SWITCH-stock', **settings)
    if not check('SINE + LOWPASS: selected again after MULTI-MODE', t.select(SINE, LOWPASS), machines=t.machines()):
        return
    first, again = (window_db(play(t, 'SWITCH-' + name, **settings)) for name in ('back', 'again'))
    n = min(len(first), len(again), 38)  # the held note; its release follows the note-off's timing
    change = float(np.max(np.abs(first[:n] - again[:n]))) if n else np.inf
    check('LOWPASS after MULTI-MODE: the first note matches a repeat', change <= 1, change_db=round(change, 2))


def run_save(t, check):
    """LOWPASS and its values survive SAVE PROJECT AS and LOAD PROJECT; the saved project's row."""
    if not check('SINE + LOWPASS: selected for saving', t.select(SINE, LOWPASS), machines=t.machines()):
        return None
    for i, (_, _, nrpn, _) in enumerate(CONTROLS.values()):
        t.nrpn(1, nrpn, (20 + 9 * i) << 7)
    saved = [list(t.machines()), [t.word(offset) for offset, *_ in CONTROLS.values()]]
    with t.recording('SAVE-before') as before:
        t.note(60)
    if not check('the project saves into slot %d' % SAVE_SLOT, t.save_project() is not None):
        return None
    for _, _, nrpn, _ in CONTROLS.values():
        t.nrpn(1, nrpn, 100 << 7)  # unsaved, so loading must discard it
    row = t.load_project()
    loaded = [list(t.machines()), [t.word(offset) for offset, *_ in CONTROLS.values()]]
    check('LOWPASS and its values survive saving and loading the project',
          loaded == saved and saved[0] == [SINE, LOWPASS], saved=saved, loaded=loaded)
    with t.recording('SAVE-after') as after:
        t.note(60)
    change = level_db(after.samples) - level_db(before.samples)
    check('LOWPASS sounds the same after loading the project', abs(change) <= 0.5, change_db=round(float(change), 2))
    return row


CASES = {'combinations': run_combinations, 'controls': run_controls, 'response': run_response,
         'tracks': run_tracks, 'downstream': run_downstream, 'curve': run_curve, 'routes': run_routes,
         'locks': run_locks,
         'switch': run_switch, 'save': run_save}


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


def scratch():
    return json.loads(scratch_file().read_text()) if scratch_file().exists() else {}


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
        # Start from the scratch project as saved, whatever was loaded or changed on the unit.
        remembered = scratch().get('row')
        if not check('the scratch project is loaded', remembered is not None
                     and t.load_project(remembered) is not None):
            return check.results
        before = t.peek(SOUND, 0x100)
        try:
            saved = run_cases(t, check, args.cases)
            if saved is not None:
                scratch_file().write_text(json.dumps({**scratch(), 'save_row': saved.tolist()}) + '\n')
        finally:
            check('the scratch project is loaded again', t.load_project(remembered) is not None)
            check('the scratch project is restored', t.peek(SOUND, 0x100) == before)
    finally:
        t.close()
    return check.results


def emulator_run(firmware, out, cases=None):
    check = Checks('emulator')
    t = Emulator(firmware, out)
    try:
        check('candidate loaded', t.loaded['syx_sha256'] == build.sha(firmware), start=t.loaded['start_used'])
        run_cases(t, check, cases)
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
    parser.add_argument('--cases', type=lambda names: names.split(','),
                        help='only these cases, of: ' + ', '.join(CASES))
    parser.add_argument('--no-build', dest='build', action='store_false',
                        help='use the existing out/c/firmware.syx')
    parser.add_argument('--no-flash', dest='flash', action='store_false',
                        help='the unit already runs this candidate')
    parser.add_argument('--remember-scratch', action='store_true',
                        help='record the loaded project as the scratch project the device run may change')
    args = parser.parse_args()
    if args.cases and not set(args.cases) <= set(CASES):
        parser.error('unknown case: ' + ', '.join(sorted(set(args.cases) - set(CASES))))
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
        scratch_file().write_text(json.dumps({**scratch(), 'row': row.tolist()}) + '\n')
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
        results = emulator_run(firmware, out, args.cases) if name == 'emulator' else device_run(firmware, version, args, out)
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
    if skipped or set(targets) != {'emulator', 'device'} or args.cases:
        print('INCOMPLETE: %s not run' % ', '.join(skipped or sorted({'emulator', 'device'} - set(targets))
                                                   or sorted(set(CASES) - set(args.cases))))
        return 2
    print('PASSED in the emulator and on the device')
    return 0


if __name__ == '__main__':
    sys.exit(main())
