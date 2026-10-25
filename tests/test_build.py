# SPDX-License-Identifier: GPL-2.0-or-later
"""Check the C voice artifact boundary and source state without firmware."""
import shutil
import struct
import sys
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch
import unittest

import build
artifact, check_toolchain = build.artifact, build.check_toolchain


def executable(code=bytes.fromhex('0001000100010001'), data_bytes=4):
    raw = bytearray(52)
    raw[:7] = b'\x7fELF\x01\x01\x01'
    struct.pack_into('<HHII', raw, 16, 2, 0x85, 1, 0xbc8400)
    raw += code + bytes(data_bytes)
    dump = (f"0001 progbits 00000007 00bc8400 00000034 {len(code):08x}:B 0 0 4 1 'probe_code'\n"
            f"0002 progbits 00000003 20094000 {52+len(code):08x} {data_bytes:08x}:B 0 0 4 0 'probe_data'\n"
            "0003 00000001 00bc8400 00000000 function global 00000000 'sharc_voice_render.'\n")
    return bytes(raw), dump


class ArtifactTests(unittest.TestCase):
    def test_packages_the_current_voice_and_pinned_adapter(self):
        doc = artifact(*executable())
        self.assertEqual(doc['abi'], 'dn2-voice-mono32-l2-v1')
        self.assertEqual(doc['machine_id'], 6)
        self.assertEqual(doc['entries'], {'render_offset_bytes': 2048})
        self.assertEqual(len(bytes.fromhex(doc['segments'][0]['hex'])), 2048)
        self.assertEqual(len(bytes.fromhex(doc['segments'][1]['hex'])), 14336)
        self.assertEqual(len(bytes.fromhex(doc['segments'][2]['hex'])), 4)
        self.assertEqual(doc['zero'][-1], {'offset_bytes': 31744, 'size_bytes': 1024})
        changed = artifact(*executable(bytes.fromhex('c001c00100010001')))
        self.assertNotEqual(changed['segments'][1], doc['segments'][1])

    def test_code_guard_and_data_bounds(self):
        for count, accepted in ((14272, True), (14274, False)):
            with self.subTest(code_bytes=count):
                if accepted:
                    artifact(*executable(bytes(count)))
                else:
                    with self.assertRaisesRegex(ValueError, '64-byte NOP read-ahead guard'):
                        artifact(*executable(bytes(count)))
        artifact(*executable(data_bytes=15360))
        with self.assertRaisesRegex(ValueError, 'data must fit its area'):
            artifact(*executable(data_bytes=15364))

    @unittest.skipUnless(shutil.which('git'), 'needs git')
    def test_toolchain_pin_and_clean_source(self):
        with tempfile.TemporaryDirectory() as work:
            repo = Path(work)
            subprocess.run(['git', 'init', '-q', work], check=True)
            source = repo / 'source.c'
            source.write_text('initial\n')
            subprocess.run(['git', 'add', 'source.c'], cwd=repo, check=True)
            subprocess.run(['git', '-c', 'commit.gpgsign=false', '-c', 'user.name=Build Test', '-c', 'user.email=test@example.invalid',
                            'commit', '-qm', 'Initialize compiler fixture'], cwd=repo, check=True)
            revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()
            with self.assertRaisesRegex(ValueError, 'Selache must be at'):
                check_toolchain(repo)
            with patch.object(build, 'SELACHE_PIN', revision):
                self.assertEqual(check_toolchain(repo), revision)
                source.write_text('modified\n')
                with self.assertRaisesRegex(ValueError, 'checkout must be clean'):
                    check_toolchain(repo)

    def test_rejects_malformed_or_mislinked_output(self):
        raw, dump = executable()
        cases = [
            (b'', dump, 'linked little-endian SHARC'),
            (raw[:-1], dump, 'truncated section'),
            (raw, dump.replace('progbits 00000007', 'progbits 00000003'), 'section attributes'),
            (raw, dump.replace('progbits', 'note', 1), 'allocated section must be progbits'),
            (raw, dump.replace('00000001 00bc8400', '00000001 00bc8410'), 'callback outside linked code'),
            (raw, dump.replace('20094000', '20098000'), 'data must fit its area'),
            (raw, dump.replace('00bc8400 00000034', '00bc8500 00000034'), '14336-byte area'),
            (raw, dump.replace('progbits', 'rela', 1), 'unresolved relocation'),
            (raw, dump + "0006 UNDEF 00000000 00000000 function global 00000000 'missing'\n", 'unresolved symbol'),
            (raw, dump + "0006 progbits 00000003 20094000 00000034 00000004:B 0 0 4 0 'extra'\n", 'only probe_code'),
            (*executable(bytes(14340)), '14336-byte area'),
        ]
        for data, inspector, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                artifact(data, inspector)

    def test_failed_compilation_removes_stale_firmware(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            stock = root / 'stock.syx'
            stock.touch()
            tools = root / 'target/release'
            tools.mkdir(parents=True)
            for name in ('selcc', 'selas', 'seld', 'seldump'):
                (tools / name).touch()
            firmware = root / 'out/c/firmware.syx'
            firmware.parent.mkdir(parents=True)
            firmware.write_bytes(b'stale')
            args = ['build.py', '--stock', str(stock), '--selache', str(root), '--digihealth', str(stock)]
            with patch.object(build, 'ROOT', root), patch.object(sys, 'argv', args), \
                    patch.object(build, 'check_toolchain', return_value=build.SELACHE_PIN), \
                    patch('elekloader.elemod.load_any', return_value=Mock(id='digihealth')), \
                    patch.object(build, 'run', side_effect=ValueError('compile failed')):
                with self.assertRaisesRegex(ValueError, 'compile failed'):
                    build.main()
            self.assertFalse(firmware.exists())


class VoiceStateTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('cc'), 'native C compiler unavailable')
    def test_phase_is_independent_and_retrigger_restarts_it(self):
        source = build.ROOT / 'examples/sharc-sine/sine.c'
        include = build.VENDOR / 'elekloader/sdk/include'
        with tempfile.TemporaryDirectory() as work:
            p = Path(work)
            (p/'test.c').write_text('#include <assert.h>\n#include <string.h>\n'
                + '#include "'+str(source)+'"\n'+'''
int main(void) {
 float a[32], b[32], first[32];
 struct dn2_voice_input in={a,0,60u<<8,DN2_VOICE_START};
 sharc_voice_render(&in); memcpy(first,a,sizeof a);
 uint32_t saved=phase[0];
 in.output=b; in.slot=1; in.pitch=64u<<8; sharc_voice_render(&in);
 assert(phase[0]==saved && phase[1]!=saved);
 uint32_t other=phase[1];
 in.output=a; in.slot=0; in.pitch=60u<<8; in.flags=0;
 sharc_voice_render(&in); assert(memcmp(first,a,sizeof a));
 in.flags=DN2_VOICE_START; sharc_voice_render(&in);
 assert(!memcmp(first,a,sizeof a) && phase[1]==other);
 in.slot=15; in.pitch=85u<<8; sharc_voice_render(&in);
 for(int i=0;i<32;++i) assert(a[i]==0.0f);
 return 0;
}
''')
            subprocess.run(['cc','-std=c99','-Wall','-Werror','-I',str(include),str(p/'test.c'),'-o',str(p/'test')],check=True)
            subprocess.run([str(p/'test')],check=True)


if __name__ == '__main__':
    unittest.main()
