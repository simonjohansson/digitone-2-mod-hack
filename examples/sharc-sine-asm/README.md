# DN2 SHARC assembly sine

This companion to [`sharc-sine`](../sharc-sine/README.md) replaces its sine
kernel with hand-written SHARC+ assembly. The same C entry wrapper handles
note initialization and the 32-sample gain ramp; the same core bridge selects
the note and copies mono to stereo. The two versions use identical table
values, phase accumulation, 0.125 peak amplitude, note range and output route.

Build with the ordinary module workflow, supplying your own DN2 OS 1.12 and
matching core:

```sh
python -m elekloader.sdk.build examples/sharc-sine-asm --stock "$DN2_STOCK"
python -m elekloader.lint examples/sharc-sine-asm/out/sharc-sine-asm-0.1.elemod --stock "$DN2_STOCK" --with "$DN2_CORE" --json
python -m elekloader.patch --stock "$DN2_STOCK" --mod "$DN2_CORE" --mod examples/sharc-sine-asm/out/sharc-sine-asm-0.1.elemod --out sine-asm.syx --version SA01
```

Select either the C or assembly sine; two applications cannot own the same
reservation. These consumer commands need no SHARC compiler. The checked-in
`sine.sharc.json` contains only author-owned bytes, not firmware. It uses the
same 1 KiB app area, keyed core and artifact format as the C example.

## Rebuild and compare

Use the [pinned Selache tools](../sharc-sine-probe/README.md#build-and-check),
including `selload`, and the PX-fixed Digikit checkout at
`ded547e14e63977c1a6e808812d46243e13c43d0`:

```sh
mise exec python@3.12.13 -- python examples/sharc-sine-asm/rebuild.py /path/to/selache
mise exec python@3.12.13 -- python examples/sharc-sine-asm/benchmark.py /path/to/selache /path/to/digikit-px-fork
```

The producer assembles `sine.s`, compiles the existing
[`entry.c`](../sharc-sine/entry.c), links with the existing keyed layout, and
checks the inspected final sections and entrypoints before replacing the
artifact. The C wrapper and core source are shared, not copied. The artifact
keeps the existing conservative 128-byte stack contribution; measured use
for the listed synthetic cases is lower.

The comparison executes both actual binaries and checks every binary32
sample and both state words against an independent host calculation. It
covers all 64 table phases, signed zero, uint32 wrap, zero and backwards
increments, dirty-state initialization, repeated blocks, and gain ramps.
It checks return targets, register/bank/stack preservation, guarded writes
and output store counts. Zeroing a table value must produce a mismatch.
The combined-wrapper comparison uses the exact final L1 callback bytes and
data; its small synthetic caller occupies otherwise unused padding.

| Scope | Code bytes | Data bytes | Instructions per 32-frame render | Maximum stack bytes |
|---|---:|---:|---:|---:|
| C kernel | 320 | 152 | 1,654–1,686 | 64 |
| Assembly kernel | 176 | 152 | 527 | 8 |
| C kernel + shared keyed wrapper | 664 | 204 | 2,722–2,834 | 108 |
| Assembly kernel + shared keyed wrapper | 520 | 204 | 1,579–1,675 | 52 |

These are **retired interpreter instructions, not device cycles**. Each
measurement includes the identical synthetic caller; code sizes exclude
that caller and padding. The assembly inlines the lookup and changes the
binary32 sign bit directly, removing the C implementation's per-sample call,
conditional sign branch and local spills. This compares these two concrete
implementations and this compiler pin, not C and assembly in general.

The report and author executables go to a fresh temporary directory printed
by the benchmark. Reports include source, tool, runner and final-artifact
hashes for reproducing the comparison. Selache is pinned to
`2b26d3b75c53063575bc5c820fa0d38879335187`. Assembly source SHA-256 is
`2de1c631c25fac19c0598cd0907cb71e68bc8200ccea872016cacebde6c71b5c`;
artifact SHA-256 is
`b90006ec64f2da2b6fbb414240326eb676c57cc4696cb395f54f14e0e4db5e7f`.

## Physical DN2 check

On 24 October 2026, the ordinary SDK built `SA01` from DN2 OS 1.12,
core-dn2 0.1, the owner's DigiHealth fork 1.3 and this assembly app. Firmware
SHA-256 was
`893e56d950c96f7892a5a1de4c9635dfb001d3bea526fc043e7bb427352662f4`.
The exact candidate first cold-booted in PX-fixed Digikit and ran 1,900
audio blocks with no DSP halt or missing block. Python and TypeScript
produced identical firmware, manifests and maps for the same mod set;
missing-core and two-app cases were refused by both.

After the authorized DN2 update, DigiHealth 1.3 responded and a 24.0107-second
USB master capture recorded 2,251 buffers with zero drops. Left and right
were sample-identical, peak was 0.17857146, and the final second was silent.
Notes 36, 48, 60, 69, 70, 72, 81 and 84 were within 0.066 cents at nominal
48 kHz; this capture did not independently calibrate the sample clock.
The sequence also exercised repeated notes, velocity-zero Note On, CC123,
and an overlapping note change followed by releasing the old and selected
notes. Capture SHA-256:
`b3a178912e022f24e0bd97e64ba25578a809498b0d32f43465f68c0c864b6510`.

Private capture, event log and analysis remain under
`/private/tmp/sharc-sine-20261024/` as `SA01-notes.caf`, `SA01-notes.json`
and `SA01-notes.analysis.json`. The device was then disconnected by the
owner. This qualifies the listed notes on USB master; physical cycle
measurements and other output routes remain pending.

## Instruction and device boundary

The producer pins all final code/data bytes and rejects unresolved symbols,
relocations, moved entries, changed attributes and illegal same-DAG stores.
The new short AND/XOR/add/decrement and register-shift fields are checked
against [ADI's SHARC+ Core Programming Reference, revision 1.5](https://www.analog.com/media/en/dsp-documentation/processor-manuals/sc58x-2158x-prm.pdf),
sections 18-3/4 and 18-10; a reserved shift-opcode bit pattern is rejected.
The benchmark also applies the pinned Digikit immediate-shift and register
class checks to every decoded instruction in both kernels and the exact
combined callbacks, with deliberate malformed-shift and same-DAG negatives.
The sign operation uses the fixed-point register field, with the floating
extension cleared as specified in section 19-13. The shift count is loaded
into a register, avoiding the previously reported negative-immediate shift
encoding defect.

Successful runner checks do not establish silicon timing, interrupts,
caches, other audio routes or worst-case DSP load. Further physical
C/assembly comparison and timing belong to
[issue #41](https://github.com/simonjohansson/elekloader/issues/41).
