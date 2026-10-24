# SPDX-License-Identifier: GPL-2.0-or-later
"""The artifact boundary must reject malformed output before firmware builds."""
import struct
import unittest

from build import artifact


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
        ]
        for data, inspector, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                artifact(data, inspector)


if __name__ == '__main__':
    unittest.main()
