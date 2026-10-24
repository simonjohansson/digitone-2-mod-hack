# SPDX-License-Identifier: GPL-2.0-or-later
"""Compare actual C and assembly sine execution; instruction counts are not cycles."""
import argparse
from collections import Counter
import importlib
import json
from pathlib import Path
import re
import struct
import sys
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
PROBE = HERE.parent / 'sharc-sine-probe'
sys.dont_write_bytecode = True
sys.path.insert(0, str(PROBE))
from qualify import FLAGS, PIN, command, digest, rejected, require, section_rows, validate_data
from execute import ALIAS, OUTPUT, TOP, PHASE, INCREMENT, expected, make_runner, put, read, check_dag_store
from rebuild import check_arithmetic

DIGIKIT_PIN = 'ded547e14e63977c1a6e808812d46243e13c43d0'
GAIN = 0x2c00c8
GATE = 0x2dd9f0


def symbols_from(dump):
    return {name: int(value, 16) for value, name in re.findall(
        r"(?m)^[0-9a-f]{4}\s+[0-9a-f]{8}\s+([0-9a-f]{8})\s+.*'([^']+)'$", dump)}


def invoke(modules, runner, symbols, name, argument):
    run, _, _, state = modules
    runner.state.pc_sw = symbols['probe_call']
    for register, value in (('I12', symbols[name]), ('R4', argument)):
        runner.state.uregs[state.UREG_CODES[register]] = state.Const(value)
    runner.breakpoints = frozenset([symbols['probe_returned']])
    before = dict(runner.state.uregs)
    alt, pc_stack = dict(runner.state.bank_alt), list(runner.state.pc_stack)
    stores = len(runner.watch_log)
    instructions = runner.instructions
    low, entered = TOP, 0
    for _ in range(4000):
        entered += runner.state.pc_sw == symbols[name]
        try:
            runner.step()
        except run.Halt as halt:
            require(halt.reason == 'breakpoint' and halt.pc_sw == symbols['probe_returned'],
                    f'{name}: unexpected halt {halt.to_json()}')
            break
        sp = runner.state.uregs[state.UREG_CODES['I7']]
        require(isinstance(sp, state.Const), 'unknown stack pointer')
        low = min(low, sp.value)
        require(TOP - 128 <= sp.value <= TOP, 'stack bound exceeded')
    else:
        raise ValueError(f'{name}: failed to return within 4,000 instructions')
    require(entered == 1, f'{name}: omitted or repeated entry')
    names = {code: name for name, code in state.UREG_CODES.items()}
    changed = {names.get(code, str(code)) for code, value in before.items()
               if runner.state.uregs.get(code) != value}
    allowed = {f'R{i}' for i in range(8)} | {'I4', 'I12', 'ASTATX', 'STKYX'}
    require(changed <= allowed, f'{name}: preserved-register clobber {changed - allowed}')
    require(runner.state.bank_alt == alt and runner.state.pc_stack == pc_stack and
            not runner.state.loops and not runner.state.status_stack and not runner.state.call_stack,
            f'{name}: bank or hardware-stack clobber')
    require(not runner.state.provisional_used and not runner.state.provisional_interpreted
            and not runner.state.approx_recips_used, 'provisional execution semantics')
    permitted = [(TOP - 128, TOP + 4)]
    if 'init' in name:
        permitted += [(PHASE, INCREMENT + 4)]
    if 'render' in name:
        permitted += [(PHASE, PHASE + 4), (OUTPUT, OUTPUT + 128)]
    if name.startswith('sharc_'):
        permitted += [(GAIN, GAIN + 4)]
    writes = runner.watch_log[stores:]
    for event in writes:
        require(any(ALIAS + lo <= event.address and event.address + event.width <= ALIAS + hi
                    for lo, hi in permitted), f'{name}: write outside state/output/stack: {event}')
    output_stores = sum(ALIAS + OUTPUT <= e.address < ALIAS + OUTPUT + 128 for e in writes)
    require(output_stores == (64 if name == 'sharc_render.' else
                             32 if name == 'sine_render.' else 0), 'wrong output store count')
    require(read(runner, OUTPUT - 64, 64) == bytes([0x5a]) * 64 and
            read(runner, OUTPUT + 128, 64) == bytes([0x5a]) * 64 and
            read(runner, TOP - 256, 128) == bytes([0xa5]) * 128 and
            read(runner, TOP + 4, 64) == bytes([0xa5]) * 64, 'output/stack guard changed')
    return {'instructions': runner.instructions - instructions, 'stack_bytes': TOP - low,
            'clobbered': sorted(changed)}


def build(selache, modules, out, kind, wrapper=False):
    from tools.patchkit import pk_sharc
    tools = selache / 'target/release'
    stem = kind + ('-wrapper' if wrapper else '')
    obj = out / f'{kind}.doj'
    if kind == 'c':
        command([tools / 'selcc', *FLAGS, '-no-std-inc', '-I', selache / 'libsel/include',
                 '-c', '-o', obj, PROBE / 'sine.c'])
    else:
        command([tools / 'selas', '-proc', 'ADSP-21569', '-o', obj, HERE / 'sine.s'])
    objects = [obj]
    if wrapper:
        entry = out / 'entry.doj'
        command([tools / 'selcc', *FLAGS, '-no-std-inc', '-I', selache / 'libsel/include',
                 '-c', '-o', entry, HERE.parent / 'sharc-sine/entry.c'])
        objects.append(entry)
    objects.append(out / 'caller.doj')
    exe = out / f'{stem}.dxe'
    layout = HERE.parent / 'sharc-sine/keyed.ldf' if wrapper else PROBE / 'synthetic.ldf'
    command([tools / 'seld', '-proc', 'ADSP-21569', '-T', layout,
             '-entry', 'probe_call', '-Map', out / f'{stem}.map', '-o', exe, *objects])
    raw = exe.read_bytes()
    dump = command([tools / 'seldump', '-fh', '-sh', '-n', '.symtab', exe], text_output=True)
    listing = command([tools / 'seldump', '-ns', 'probe_code', exe], text_output=True)
    (out / f'{stem}-symbols.txt').write_text(dump)
    (out / f'{stem}-instructions.txt').write_text(listing)
    rows = section_rows(dump, raw)
    require({name for name, row in rows.items() if row['flags'] & 2} ==
            {'probe_code', 'probe_data'}, 'unexpected loadable sections')
    require(not any(row['kind'] in ('rela', 'rel') for row in rows.values()) and
            not re.search(r"(?m)^[0-9a-f]{4}\s+UNDEF.*'[^']+'$", dump), 'unresolved symbol/relocation')
    validate_data(rows['probe_data']['bytes'][:152])
    require(rows['probe_data']['size'] == (204 if wrapper else 152), 'changed data layout')
    if kind == 'c' and not wrapper:
        # The first 320 bytes remain the previously qualified compiler output.
        from qualify import CODE_SHA256
        require(digest(rows['probe_code']['bytes'][:320]) == CODE_SHA256, 'C baseline changed')
    ldr = out / f'{stem}.ldr'
    command([tools / 'selload', '-proc', 'ADSP-21569', '-o', ldr, exe])
    stream = ldr.read_bytes()
    memory = modules[2].LoadedMemory.from_stream(stream)
    instructions = re.findall(r'(?m)^  ([0-9a-f]{8})\s+([0-9a-f]+)\s+(.+)$', listing)
    require(sum(len(code) // 2 for _, code, _ in instructions) == rows['probe_code']['size'],
            'incomplete code listing')
    forms = Counter()
    code_start = 0x16ed00 if wrapper else 0x180000
    arithmetic_fields = 0
    for pc, code, mnemonic in instructions:
        decoded = modules[1].decode_at(memory, None, int(pc, 16))
        require(decoded.kind == 'confident' and decoded.length_bytes == len(code) // 2,
                f'unsupported decode at {pc}: {mnemonic}')
        check_dag_store(mnemonic)
        require(pk_sharc.shift_imm_problem(decoded.fields, decoded.type_name) is None,
                f'invalid immediate shift at {pc}')
        require(not pk_sharc.ureg_class_problems(decoded.fields, pc, decoded.type_name),
                f'invalid register class at {pc}')
        if kind == 'asm' and int(pc, 16) < code_start + 88:
            arithmetic_fields += check_arithmetic(code, mnemonic)
        forms[decoded.type_name] += 1
    symbols = symbols_from(dump)
    code_size = 2 * (symbols['probe_call'] - code_start)
    if wrapper:
        code = rows['probe_code']['bytes'][:code_size]
        code = b''.join(code[i:i + 2][::-1] for i in range(0, len(code), 2))
        code += bytes.fromhex('01000100') * ((768 - len(code)) // 4)
        qualified = {'c': '89ceab25abe173f9e050adedbd5f6a2dd67bbc06e09ea28ba40e2ad76d0a524a',
                     'asm': 'ebe4ba4d0c3692f1e3f6309403613a7f47f823c4189155e9b4f1c2d9707219a6'}
        require(digest(code) == qualified[kind], 'final installed application bytes changed')
        require(symbols['steps12.'] == 0x2ddd98, 'keyed bridge pitch table moved')
    require(symbols['sine_init.'] == code_start and
            struct.unpack_from('<I', raw, 24)[0] == symbols['probe_call'], 'incorrect entries')
    return stream, symbols, {'code_bytes': code_size, 'data_bytes': rows['probe_data']['size'],
                             'executable_sha256': digest(raw), 'loader_sha256': digest(stream),
                             'decoded_forms': dict(forms), 'manual_checked_compute_fields': arithmetic_fields,
                             'shift_and_register_class_checked': len(instructions)}


def main():
    global PHASE, INCREMENT, GAIN
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('selache', type=Path)
    parser.add_argument('digikit', type=Path)
    args = parser.parse_args()
    selache, digikit = (p.expanduser().resolve() for p in (args.selache, args.digikit))
    for repo, pin in ((selache, PIN), (digikit, DIGIKIT_PIN)):
        require(command(['git', 'rev-parse', 'HEAD'], cwd=repo, text_output=True).strip() == pin,
                f'{repo} is not pinned to {pin}')
        require(not command(['git', 'status', '--porcelain', '--untracked-files=normal'],
                            cwd=repo, text_output=True).strip(), f'{repo} has source changes')
    sys.path.insert(0, str(digikit))
    modules = [importlib.import_module('tools.sharc_dev.' + name) for name in
               ('sharc_run', 'sharc_trace', 'sharcldr', 'sharc_core.state')]
    require(all(Path(m.__file__).resolve().is_relative_to(digikit) for m in modules),
            'unexpected runner import')
    from tools.patchkit import pk_sharc
    require(Path(pk_sharc.__file__).resolve().is_relative_to(digikit), 'unexpected ISA check import')
    require(pk_sharc.shift_imm_problem({'shiftimm[22:16]': 0, 'shiftimm[15:0]': 0xf00b,
                                       'dataex[3:0]': 0}, '6b_shiftimm') is not None,
            'malformed negative shift was not rejected')
    require(pk_sharc.ureg_class_problems({'d': 1, 'ureg': 0x14, 'g': 0}, 'negative', '15a'),
            'same-DAG store was not rejected')
    out = Path(tempfile.mkdtemp(prefix='sharc-sine-asm-'))
    rejected('reserved shift opcode',
             lambda: check_arithmetic('01a01507', 'r5=lshift r0 by r7'),
             'wrong register-shift bit fields')
    command([selache / 'target/release/selas', '-proc', 'ADSP-21569', '-o',
             out / 'caller.doj', PROBE / 'caller.s'])
    results = {}
    for wrapper in (False, True):
        PHASE = 0x2ddd90 if wrapper else 0x2c0090
        INCREMENT, GAIN = PHASE + 4, PHASE + 0x38
        variants = {kind: build(selache, modules, out, kind, wrapper) for kind in ('c', 'asm')}
        for kind, (stream, symbols, facts) in variants.items():
            records = []
            cases = [(0, 39370534, 8), (0, 1 << 26, 2), (0x7ffffffe, 1, 2),
                     (0xfffffffe, 1, 2), (0x80000000, 0, 2), (0, 0xffffffff, 2),
                     (0xdeadbeef, 0x12345678, 4)]
            for initial_phase, increment, count in cases:
                prefix = 'sharc' if wrapper else 'sine'
                runner = make_runner(modules, stream, symbols['probe_call'],
                                     symbols[f'{prefix}_init.'], increment)
                put(runner, PHASE, 0xdeadbeef)
                put(runner, INCREMENT, 0xa5a5a5a5)
                if wrapper:
                    put(runner, GAIN, 0x3f800000)
                    put(runner, GATE, 1)
                records.append({'case': 'init', **invoke(modules, runner, symbols,
                                                       f'{prefix}_init.', increment)})
                require(read(runner, PHASE, 8) == struct.pack('<II', 0, increment),
                        'init failed to reset dirty phase/increment')
                if wrapper:
                    require(read(runner, GAIN, 4) == bytes(4), 'init failed to reset gain')
                phase, gain = initial_phase, 0.0
                put(runner, PHASE, phase)
                for block in range(count):
                    gate = block % 3 != 2
                    if wrapper:
                        put(runner, GATE, int(gate))
                    want, phase = expected(phase, increment)
                    if wrapper:
                        floats = list(struct.unpack('<32f', want))
                        for i, value in enumerate(floats):
                            gain = min(1.0, gain + .03125) if gate else max(0.0, gain - .03125)
                            floats[i] = value * gain
                        want = struct.pack('<32f', *floats)
                    record = invoke(modules, runner, symbols, f'{prefix}_render.', OUTPUT)
                    require(read(runner, OUTPUT, 128) == want, f'{kind}: sample mismatch')
                    require(read(runner, PHASE, 8) == struct.pack('<II', phase, increment),
                            f'{kind}: phase/increment mismatch')
                    if wrapper:
                        require(read(runner, GAIN, 4) == struct.pack('<f', gain), 'gain mismatch')
                    records.append({'case': 'render', 'phase': phase, 'increment': increment,
                                    'gate': gate if wrapper else None, **record})
            if not wrapper:
                for phase in [i << 26 for i in range(64)] + [0xffffffff]:
                    runner = make_runner(modules, stream, symbols['probe_call'],
                                         symbols['sine_sample.'], phase)
                    records.append({'case': 'sample', **invoke(modules, runner, symbols,
                                                               'sine_sample.', phase)})
                    actual = runner.state.uregs[modules[3].UREG_CODES['R0']]
                    require(isinstance(actual, modules[3].Const) and
                            actual.value == struct.unpack('<I', expected(phase, 0, 1)[0])[0],
                            f'{kind}: direct sample mismatch at {phase:#x}')
                runner = make_runner(modules, stream, symbols['probe_call'], symbols['sine_render.'], OUTPUT)
                put(runner, PHASE, 1 << 26)
                put(runner, INCREMENT, 0)
                put(runner, 0x2c0004, 0)
                invoke(modules, runner, symbols, 'sine_render.', OUTPUT)
                require(read(runner, OUTPUT, 128) != expected(1 << 26, 0)[0],
                        'deliberate arithmetic mutation was not detected')
            facts['calls'] = records
            facts['render_instructions'] = [min(r['instructions'] for r in records if r['case']=='render'),
                                            max(r['instructions'] for r in records if r['case']=='render')]
            facts['max_stack_bytes'] = max(r['stack_bytes'] for r in records)
            results[kind + ('_wrapper' if wrapper else '')] = facts
    sources = [HERE / 'sine.s', HERE / 'benchmark.py', HERE / 'rebuild.py',
               PROBE / 'sine.c', PROBE / 'caller.s', PROBE / 'synthetic.ldf',
               HERE.parent / 'sharc-sine/entry.c', HERE.parent / 'sharc-sine/keyed.ldf']
    report = {'selache_commit': PIN, 'digikit_commit': DIGIKIT_PIN,
              'sources_sha256': {str(path.relative_to(ROOT)): digest(path.read_bytes()) for path in sources},
              'tools_sha256': {name: digest((selache / 'target/release' / name).read_bytes())
                               for name in ('selcc', 'selas', 'seld', 'seldump', 'selload')},
              'runner_sources_sha256': {str(Path(m.__file__).relative_to(digikit)):
                                        digest(Path(m.__file__).read_bytes()) for m in [*modules, pk_sharc]},
              'results': results, 'arithmetic_mutations_detected': 2,
              'reserved_shift_opcode_rejected': True,
              'negative_shift_and_same_dag_rejected': True,
              'limitations': ['retired runner instructions, not silicon cycles',
                              'synthetic context; no device timing or interrupt/cache qualification']}
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    for kind, facts in results.items():
        print(kind, facts['code_bytes'], 'code bytes;', facts['data_bytes'], 'data bytes;',
              facts['render_instructions'], 'render instructions;', facts['max_stack_bytes'], 'stack bytes')
    print('PASS: exact samples/state, guarded ABI, 64 table phases, wrap, repeated blocks and mutations')
    print(out / 'report.json')


if __name__ == '__main__':
    main()
