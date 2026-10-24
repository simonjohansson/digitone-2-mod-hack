# SPDX-License-Identifier: GPL-2.0-or-later
"""Compile either local SHARC sine and build a verified DN2 firmware."""
import argparse
import hashlib
import json
import os
import platform
from pathlib import Path
import re
import shlex
import shutil
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
VENDOR = ROOT / 'vendor/elekloader'
SELACHE_PIN = '2b26d3b75c53063575bc5c820fa0d38879335187'
CODE_GUARD_BYTES = 64
sys.path.insert(0, str(VENDOR))


def require(ok, message):
    if not ok:
        raise ValueError(message)


def check_toolchain(path):
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=path, text=True).strip()
    require(revision == SELACHE_PIN, 'Selache must be at ' + SELACHE_PIN)
    require(not subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=normal'],
                                        cwd=path, text=True).strip(), 'Selache checkout must be clean')
    return revision


def artifact(raw, dump):
    """Read the tested Selache inspector output for this example's fixed layout."""
    require(len(raw) >= 52 and raw[:7] == b'\x7fELF\x01\x01\x01'
            and struct.unpack_from('<HHI', raw, 16) == (2, 0x85, 1),
            'expected a linked little-endian SHARC ELF32 executable')
    rows = {}
    for match in re.finditer(
            r"(?m)^[0-9a-f]{4}\s+(\w+)\s+([0-9a-f]{8})\s+([0-9a-f]{8})\s+"
            r"([0-9a-f]{8})\s+([0-9a-f]{8}):B\s+\d+\s+\d+\s+\d+\s+\d+\s+'([^']*)'", dump):
        kind, flags, address, offset, size, name = match.groups()
        flags, address, offset, size = [int(x, 16) for x in (flags, address, offset, size)]
        require(kind not in ('rel', 'rela', 'nobits'), 'unresolved relocation or uninitialized section')
        require(name not in rows and offset + size <= len(raw), 'duplicate or truncated section')
        rows[name] = (flags, address, raw[offset:offset + size])
    require({name for name, row in rows.items() if row[0] & 2} == {'probe_code', 'probe_data'},
            'expected only probe_code and probe_data loadable sections')
    require(not re.search(r"(?m)^[0-9a-f]{4}\s+UNDEF.*'[^']+'$", dump), 'unresolved symbol')
    _, code_pc, code = rows['probe_code']
    _, data_address, data = rows['probe_data']
    require(code_pc == 0x16ed00 and 0 < len(code) <= 768 and len(code) % 2 == 0,
            'code must fit the existing 768-byte area at PC16 0x16ed00')
    require(len(code) + CODE_GUARD_BYTES <= 768, 'code leaves no 64-byte NOP read-ahead guard')
    require(data_address == 0x2ddd00 and 200 <= len(data) <= 256 and len(data) % 4 == 0,
            'data must fit the existing 256-byte area at 0x2ddd00')

    def symbol(name):
        matches = re.findall(r"(?m)^[0-9a-f]{4}\s+[0-9a-f]{8}\s+([0-9a-f]{8})\s+.*'%s'$"
                             % re.escape(name), dump)
        require(len(matches) == 1, 'missing or duplicate symbol: ' + name)
        return int(matches[0], 16)

    require(symbol('steps12.') == 0x2ddd98, 'pitch table moved from the keyed bridge address')
    entries = {role + '_offset_bytes': (symbol('sharc_' + role + '.') - code_pc) * 2
               for role in ('init', 'render')}
    require(all(0 <= off < len(code) for off in entries.values()), 'callback outside linked code')
    require(struct.unpack_from('<I', raw, 24)[0] == symbol('sharc_init.'), 'wrong executable entry')
    code = b''.join(code[i:i + 2][::-1] for i in range(0, len(code), 2))
    code += b'\x01\x00' * ((768 - len(code)) // 2)
    end = 768 + len(data)
    return {'abi': 'dn2-master-mono32-v1',
            'segments': [{'offset_bytes': 0, 'kind': 'code', 'hex': code.hex()},
                         {'offset_bytes': 768, 'kind': 'data', 'hex': data.hex()}],
            'zero': [{'offset_bytes': end, 'size_bytes': 1024 - end}] if end < 1024 else [],
            'entries': entries, 'stack_bytes': 128}


def run(args, capture=False):
    args = list(map(str, args))
    print('+ ' + shlex.join(args), flush=True)
    return subprocess.run(args, check=True, text=True,
                          stdout=subprocess.PIPE if capture else None).stdout


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind', choices=('c', 'asm'))
    parser.add_argument('--stock', default=os.environ.get('DN2_STOCK'))
    parser.add_argument('--selache', default=os.environ.get('SELACHE'))
    parser.add_argument('--digihealth', default=os.environ.get('DN2_DIGIHEALTH'))
    args = parser.parse_args()
    if not all((args.stock, args.selache, args.digihealth)):
        parser.error('set DN2_STOCK, SELACHE and DN2_DIGIHEALTH in mise.local.toml; see README.md')
    stock, selache, health = [Path(p).expanduser().resolve()
                             for p in (args.stock, args.selache, args.digihealth)]
    require(stock.is_file() and health.is_file(), 'stock or DigiHealth module is missing')
    revision = check_toolchain(selache)
    tools = selache / 'target/release'
    require(all((tools / name).is_file() for name in ('selcc', 'selas', 'seld', 'seldump')),
            'SELACHE must contain target/release/{selcc,selas,seld,seldump}')
    require((VENDOR / 'elekloader/__init__.py').is_file(), 'initialize the Elekloader submodule')
    from elekloader import elemod, formats, lint, patch, sharc
    from elekloader.sdk import build as sdk

    require(elemod.load_any(str(health)).id == 'digihealth', 'DN2_DIGIHEALTH must be a DigiHealth module')
    out = ROOT / 'out' / args.kind
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    source_dir = ROOT / 'examples' / ('sharc-sine' if args.kind == 'c' else 'sharc-sine-asm')
    shared = ROOT / 'examples/sharc-sine'
    kernel = source_dir / ('sine.c' if args.kind == 'c' else 'sine.s')
    flags = ['-proc', 'ADSP-21569', '-char-size-8', '-O1', '-no-std-inc',
             '-I', selache / 'libsel/include']
    compiler = [tools / 'selcc', *flags, '-c'] if args.kind == 'c' else [tools / 'selas', '-proc', 'ADSP-21569']
    run([*compiler, '-o', out / 'sine.doj', kernel])
    run([tools / 'selcc', *flags, '-c', '-o', out / 'entry.doj', shared / 'entry.c'])
    exe = out / 'sine.dxe'
    run([tools / 'seld', '-proc', 'ADSP-21569', '-T', shared / 'keyed.ldf',
         '-entry', 'sharc_init.', '-Map', out / 'sine.map', '-o', exe,
         out / 'sine.doj', out / 'entry.doj'])
    dump = run([tools / 'seldump', '-fh', '-sh', '-n', '.symtab', exe], capture=True)
    (out / 'sine.inspection.txt').write_text(dump)
    app = artifact(exe.read_bytes(), dump)
    _, _, release = formats.load(str(stock))
    sharc.module_payload(app, release.sharc)
    stage = out / 'mod'
    stage.mkdir()
    shutil.copyfile(source_dir / 'mod.json', stage / 'mod.json')
    payload = stage / 'sine.sharc.json'
    payload.write_text(json.dumps(app, indent=1) + '\n')
    try:
        core, _ = sdk.build(str(VENDOR / 'mods/core-dn2'), str(stock), str(out / 'core'))
        module, _ = sdk.build(str(stage), str(stock), str(out / 'module'))
        require(lint.main([module, '--stock', str(stock), '--with', core,
                           '--with', str(health)]) == 0, 'module lint failed')
        version = 'OC02' if args.kind == 'c' else 'OA02'
        outputs, manifest = patch.build(str(stock), [core, str(health), module], version)
        firmware = out / 'firmware.syx'
        patch.save(outputs, manifest, str(firmware))
    except (sdk.BuildError, patch.PatchError) as error:
        raise ValueError(str(error)) from error
    report = {'kind': args.kind,
              'host': {'system': platform.platform(), 'machine': platform.machine(), 'python': sys.version},
              'toolchain': {'commit': revision, 'tools_sha256': {name: sha(tools / name)
                            for name in ('selcc', 'selas', 'seld', 'seldump')}},
              'sources_sha256': {str(p.relative_to(ROOT)): sha(p)
                                 for p in (kernel, shared / 'entry.c', shared / 'keyed.ldf')},
              'stock_sha256': sha(stock), 'digihealth_sha256': sha(health),
              'artifact': str(payload), 'artifact_sha256': sha(payload),
              'module': module, 'module_sha256': sha(module),
              'firmware': str(firmware), 'firmware_sha256': sha(firmware)}
    report['submodule_commit'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=VENDOR, text=True).strip()
    (out / 'build-report.json').write_text(json.dumps(report, indent=2) + '\n')
    print('BUILT ' + str(firmware), flush=True)
    print('sha256 ' + report['firmware_sha256'], flush=True)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        sys.exit('BUILD FAILED: ' + str(error))
