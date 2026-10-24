# SPDX-License-Identifier: GPL-2.0-or-later
"""The artifact boundary must reject malformed output before firmware builds."""
import struct
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch
import unittest

from build import artifact, check_toolchain


def executable(code=bytes.fromhex('0001000100010001')):
    raw = bytearray(52)
    raw[:7] = b'\x7fELF\x01\x01\x01'
    struct.pack_into('<HHII', raw, 16, 2, 0x85, 1, 0x16ed00)
    raw += code + bytes(204)
    dump = (f"0001 progbits 00000007 0016ed00 00000034 {len(code):08x}:B 0 0 4 1 'probe_code'\n"
            f"0002 progbits 00000003 002ddd00 {52+len(code):08x} 000000cc:B 0 0 4 0 'probe_data'\n"
            "0003 00000001 0016ed00 00000000 function global 00000000 'sharc_init.'\n"
            "0004 00000001 0016ed02 00000000 function global 00000000 'sharc_render.'\n"
            "0005 00000002 002ddd98 00000000 object global 00000000 'steps12.'\n")
    return bytes(raw), dump


class ArtifactTests(unittest.TestCase):
    def test_converts_code_and_reads_callback_symbols(self):
        app = artifact(*executable())
        self.assertEqual(app['entries'], {'init_offset_bytes': 0, 'render_offset_bytes': 4})
        self.assertEqual(bytes.fromhex(app['segments'][0]['hex']), bytes.fromhex('0100') * 384)
        self.assertEqual(app['zero'], [{'offset_bytes': 972, 'size_bytes': 52}])
        changed = artifact(*executable(bytes.fromhex('c001c00100010001')))
        self.assertNotEqual(changed['segments'][0], app['segments'][0])

    def test_l2_layout_and_guard_bound(self):
        raw, dump = executable()
        raw = bytearray(raw)
        struct.pack_into('<I', raw, 24, 0xbc8000)
        dump = dump.replace('0016ed00', '00bc8000').replace('0016ed02', '00bc8002')
        dump = dump.replace('002ddd00', '20094000').replace('002ddd98', '20094098')
        app = artifact(bytes(raw), dump, l2=True)
        self.assertEqual(app['abi'], 'dn2-master-mono32-l2-v1')
        measured = artifact(bytes(raw), dump, l2=True, metrics=True)
        self.assertEqual(measured.pop('abi'), 'dn2-master-mono32-l2-metrics-v1')
        self.assertEqual(measured, {k: v for k, v in app.items() if k != 'abi'})
        with self.assertRaisesRegex(ValueError, 'measurement requires the L2 layout'):
            artifact(*executable(), metrics=True)
        self.assertEqual(app['segments'][1]['offset_bytes'], 16384)
        self.assertEqual(app['zero'], [{'offset_bytes': 16588, 'size_bytes': 16180}])
        self.assertEqual(bytes.fromhex(app['segments'][0]['hex'])[-64:], b'\x01\x00' * 32)
        with self.assertRaisesRegex(ValueError, 'PC16'):
            artifact(*executable(), l2=True)
        for count, accepted in ((16320, True), (16322, False)):
            raw, dump = executable(bytes(count))
            raw = bytearray(raw)
            struct.pack_into('<I', raw, 24, 0xbc8000)
            dump = dump.replace('0016ed00', '00bc8000').replace('0016ed02', '00bc8002')
            dump = dump.replace('002ddd00', '20094000').replace('002ddd98', '20094098')
            if accepted:
                self.assertEqual(len(bytes.fromhex(artifact(bytes(raw), dump, l2=True)['segments'][0]['hex'])), 16384)
            else:
                with self.assertRaisesRegex(ValueError, '64-byte NOP read-ahead guard'):
                    artifact(bytes(raw), dump, l2=True)

    def test_guard_boundary(self):
        app = artifact(*executable(bytes(704)))
        self.assertEqual(bytes.fromhex(app['segments'][0]['hex'])[-64:], bytes.fromhex('0100') * 32)

    def test_toolchain_pin_and_clean_source(self):
        with tempfile.TemporaryDirectory() as work:
            repo = Path(work)
            subprocess.run(['git', 'init', '-q', work], check=True)
            source = repo / 'source.c'
            source.write_text('initial\n')
            subprocess.run(['git', 'add', 'source.c'], cwd=repo, check=True)
            subprocess.run(['git', '-c', 'user.name=Build Test', '-c', 'user.email=test@example.invalid',
                            'commit', '-qm', 'Initialize compiler fixture'], cwd=repo, check=True)
            revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()
            with self.assertRaisesRegex(ValueError, 'Selache must be at'):
                check_toolchain(repo)
            with patch('build.SELACHE_PIN', revision):
                self.assertEqual(check_toolchain(repo), revision)
                source.write_text('modified\n')
                with self.assertRaisesRegex(ValueError, 'checkout must be clean'):
                    check_toolchain(repo)

    def test_rejects_invalid_executables_and_abi_layout(self):
        raw, dump = executable()
        cases = [
            (b'', dump, 'linked little-endian SHARC'),
            (raw[:-1], dump, 'truncated section'),
            (raw, dump.replace('0016ed02', '0016ed08'), 'callback outside linked code'),
            (raw, dump.replace('002ddd98', '002ddd9c'), 'pitch table moved'),
            (raw, dump.replace('0016ed00 00000034', '0016ee00 00000034'), '768-byte area'),
            (raw, dump.replace('progbits', 'rela', 1), 'unresolved relocation'),
            (raw, dump + "0006 UNDEF 00000000 00000000 function global 00000000 'missing'\n", 'unresolved symbol'),
            (raw, dump + "0006 progbits 00000003 002ddd00 00000034 00000004:B 0 0 4 0 'extra'\n", 'only probe_code'),
            (*executable(bytes(772)), '768-byte area'),
            (*executable(bytes(706)), '64-byte NOP read-ahead guard'),
        ]
        for data, inspector, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                artifact(data, inspector)


if __name__ == '__main__':
    unittest.main()
