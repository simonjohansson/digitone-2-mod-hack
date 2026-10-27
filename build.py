# SPDX-License-Identifier: GPL-2.0-or-later
"""Compile SINE and LOWPASS and build DN2 firmware with DigiHealth."""
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


def artifact(raw, dump, machine_kind='voice', machine_id=6):
    """Read the tested Selache inspector output for this example's fixed layout."""
    require(len(raw) >= 52 and raw[:7] == b'\x7fELF\x01\x01\x01'
            and struct.unpack_from('<HHI', raw, 16) == (2, 0x85, 1),
            'expected a linked little-endian SHARC ELF32 executable')
    from elekloader.sdk.sharc import machine_layout, voice_artifact, filter_artifact
    layout = machine_layout(machine_kind)
    rows = {}
    for match in re.finditer(
            r"(?m)^[0-9a-f]{4}\s+([a-z_ ]+?)\s+([0-9a-f]{8})\s+([0-9a-f]{8})\s+"
            r"([0-9a-f]{8})\s+([0-9a-f]{8}):B\s+\d+\s+\d+\s+\d+\s+\d+\s+'([^']*)'", dump):
        kind, flags, address, offset, size, name = match.groups()
        flags, address, offset, size = [int(x, 16) for x in (flags, address, offset, size)]
        require(kind not in ('rel', 'rela', 'nobits'), 'unresolved relocation or uninitialized section')
        require(not flags & 2 or kind == 'progbits', 'allocated section must be progbits')
        require(name not in rows and offset + size <= len(raw), 'duplicate or truncated section')
        rows[name] = (flags, address, raw[offset:offset + size])
    allocated = {name for name, row in rows.items() if row[0] & 2}
    require('probe_code' in allocated and allocated <= {'probe_code', 'probe_data'},
            'expected only probe_code and probe_data loadable sections')
    require(not re.search(r"(?m)^[0-9a-f]{4}\s+UNDEF.*'[^']+'$", dump), 'unresolved symbol')
    code_flags, code_pc, code = rows['probe_code']
    data_flags, data_address, data = rows.get('probe_data', (3, layout['data_address'], b''))
    require((code_flags, data_flags) == (7, 3), 'wrong code/data section attributes')
    code_base, data_base = layout['code_pc16'], layout['data_address']
    code_limit = layout['code_bytes']
    require(code_pc == code_base and 0 < len(code) <= code_limit and len(code) % 2 == 0,
            'code must fit the %d-byte area at PC16 0x%x' % (code_limit, code_base))
    require(len(code) + CODE_GUARD_BYTES <= code_limit, 'code leaves no 64-byte NOP read-ahead guard')
    require(data_address == data_base and 0 <= len(data) <= layout['data_bytes'] and len(data) % 4 == 0,
            'data must fit its area at 0x%x' % data_base)

    def symbol(name):
        matches = re.findall(r"(?m)^[0-9a-f]{4}\s+[0-9a-f]{8}\s+([0-9a-f]{8})\s+.*'%s'$"
                             % re.escape(name), dump)
        require(len(matches) == 1, 'missing or duplicate symbol: ' + name)
        return int(matches[0], 16)

    entry = symbol('sharc_' + machine_kind + '_render.')
    offset = (entry - code_pc) * 2
    require(0 <= offset < len(code), 'callback outside linked code')
    require(struct.unpack_from('<I', raw, 24)[0] == entry, 'wrong executable entry')
    code = b''.join(code[i:i + 2][::-1] for i in range(0, len(code), 2))
    producer = voice_artifact if machine_kind == 'voice' else filter_artifact
    return producer(code, data, offset, machine_id, 128 if machine_kind == 'voice' else 20)


def run(args, capture=False):
    args = list(map(str, args))
    print('+ ' + shlex.join(args), flush=True)
    return subprocess.run(args, check=True, text=True,
                          stdout=subprocess.PIPE if capture else None).stdout


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stock', default=os.environ.get('DN2_STOCK'))
    parser.add_argument('--selache', default=os.environ.get('SELACHE'))
    parser.add_argument('--digihealth', default=os.environ.get('DN2_DIGIHEALTH'))
    parser.add_argument('--version', default='SL01', help='the 4 characters the unit shows')
    args = parser.parse_args()
    out = ROOT / 'out/c'
    require(not out.is_symlink(), 'output directory must not be a symlink')
    if out.exists():
        shutil.rmtree(out)
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
    from elekloader import elemod, lint, patch
    from elekloader.sdk import build as sdk

    helper = elemod.load_any(str(health))
    require(helper.id == 'digihealth', 'DN2_DIGIHEALTH must be a DigiHealth module')
    out.mkdir(parents=True)
    from elekloader.sdk.sharc import machine_layout
    kernels = {}
    source_paths = []
    for kind, stem, machine_id in [('voice', 'sine', 6), ('filter', 'filter', 8)]:
        source_dir = ROOT / 'examples' / ('sharc-' + stem)
        kernel = source_dir / (stem + '.c')
        folder = out / kind
        folder.mkdir()
        if kernel.exists():
            flags = ['-proc', 'ADSP-21569', '-char-size-8', '-O1', '-no-std-inc',
                     '-I', selache / 'libsel/include', '-I', VENDOR / 'elekloader/sdk/include']
            run([tools / 'selcc', *flags, '-c', '-o', folder / (stem + '.doj'), kernel])
        else:  # the filter is assembly: it needs no stack (see elekloader#91)
            kernel = source_dir / (stem + '.s')
            run([tools / 'selas', '-proc', 'ADSP-21569', '-o', folder / (stem + '.doj'), kernel])
        bounds = machine_layout(kind)
        layout = folder / 'layout.ldf'
        layout.write_text("""ARCHITECTURE(ADSP-21569)
$OBJECTS = $COMMAND_LINE_OBJECTS;
MEMORY {
 code { TYPE(BW RAM) START(0x%x) END(0x%x) WIDTH(8) }
 data { TYPE(BW RAM) START(0x%x) END(0x%x) WIDTH(8) }
}
PROCESSOR p0 { OUTPUT($COMMAND_LINE_OUTPUT_FILE) SECTIONS {
 probe_code SW { INPUT_SECTION_ALIGN(2) INPUT_SECTIONS($OBJECTS(seg_swco)) } > code
 probe_data BW { INPUT_SECTION_ALIGN(4) INPUT_SECTIONS($OBJECTS(seg_dmda)) } > data
} }
""" % (bounds['code_pc16'] * 2, bounds['code_pc16'] * 2 + bounds['code_bytes'] - 1,
       bounds['data_address'], bounds['data_address'] + bounds['data_bytes'] - 1))
        exe = folder / (stem + '.dxe')
        run([tools / 'seld', '-proc', 'ADSP-21569', '-T', layout,
             '-entry', 'sharc_' + kind + '_render.', '-Map', folder / (stem + '.map'),
             '-o', exe, folder / (stem + '.doj')])
        dump = run([tools / 'seldump', '-fh', '-sh', '-n', '.symtab', exe], capture=True)
        (folder / 'inspection.txt').write_text(dump)
        app = artifact(exe.read_bytes(), dump, kind, machine_id)
        stage = folder / 'mod'
        stage.mkdir()
        for name in ('mod.json', 'machine.c'):
            shutil.copyfile(source_dir / name, stage / name)
        payload = stage / (stem + '.sharc.json')
        payload.write_text(json.dumps(app, indent=1) + '\n')
        module, _ = sdk.build(str(stage), str(stock), str(folder / 'module'))
        kernels[kind] = {'artifact': str(payload), 'artifact_sha256': sha(payload),
                         'module': module, 'machine_id': machine_id}
        source_paths += [kernel, source_dir / 'mod.json', source_dir / 'machine.c']
    try:
        core, _ = sdk.build(str(VENDOR / 'mods/core-dn2'), str(stock), str(out / 'core'))
        companion, _ = sdk.build(str(VENDOR / 'mods/machines-dn2'), str(stock), str(out / 'machines'))
        modules = [core, str(health), companion, *[k['module'] for k in kernels.values()]]
        for kernel in kernels.values():
            module = kernel['module']
            require(lint.main([module, '--stock', str(stock),
                               *[arg for m in modules if m != module for arg in ('--with', m)]]) == 0,
                    'module lint failed')
        outputs, manifest = patch.build(str(stock), modules, args.version)
        firmware = out / 'firmware.syx'
        patch.save(outputs, manifest, str(firmware))
    except (sdk.BuildError, patch.PatchError) as error:
        raise ValueError(str(error)) from error
    report = {'host': {'system': platform.platform(), 'machine': platform.machine(), 'python': sys.version},
              'toolchain': {'commit': revision, 'tools_sha256': {name: sha(tools / name)
                            for name in ('selcc', 'selas', 'seld', 'seldump')}},
              'sources_sha256': {str(p.relative_to(ROOT)): sha(p) for p in source_paths},
              'stock_sha256': sha(stock), 'digihealth_sha256': sha(health), 'kernels': kernels,
              'modules': {elemod.load_any(m).id: {'path': m, 'sha256': sha(m)} for m in modules},
              'firmware': str(firmware), 'firmware_sha256': sha(firmware),
              'elekloader': {'path': str(VENDOR),
                            'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=VENDOR, text=True).strip(),
                            'dirty': bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=VENDOR).strip())}}
    (out / 'build-report.json').write_text(json.dumps(report, indent=2) + '\n')
    print('BUILT ' + str(firmware), flush=True)
    print('sha256 ' + report['firmware_sha256'], flush=True)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        sys.exit('BUILD FAILED: ' + str(error))
