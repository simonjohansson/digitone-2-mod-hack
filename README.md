# Digitone II mod experiment

Build either local SHARC sine into a Digitone II OS 1.12 firmware using
Elekloader as a submodule:

```sh
mise run build-c
mise run build-asm
# Same sources in the experimental 32 KiB L2 reservation:
mise run build-c-l2
mise run build-asm-l2
# Optional callback counters; configure DigiHealth 1.5 or newer:
mise run build-c-l2-metrics
mise run build-asm-l2-metrics
```

The commands always compile the current sources. Both examples currently
advance phase twice per sample, so MIDI notes play one octave higher than
the original examples. Each firmware includes the DN2 core and DigiHealth.

## Setup once

Install [mise](https://mise.jdx.dev/), the ColdFire `m68k-elf-` tools, and
[Selache](https://github.com/js216/selache). The tested Selache revision is
`2b26d3b75c53063575bc5c820fa0d38879335187`; its `selcc`, `selas`, `seld` and
`seldump` binaries must be built under `target/release/`. The commands require
that exact clean source revision and record hashes of the four binaries.
For a fresh compiler checkout:

```sh
git clone https://github.com/js216/selache.git "$HOME/src/selache"
cd "$HOME/src/selache"
git switch --detach 2b26d3b75c53063575bc5c820fa0d38879335187
mise exec rust@1.98.1 -- cargo build --release --locked -p selcc -p selas -p seld -p seldump
```

Then return to this mod repository. Python 3.12.13 is
managed by this repository's mise configuration; no Python packages are needed.
The ColdFire tools build the core. The local template sets
`ELEKLOADER_CROSS = "m68k-elf-"`; change it if your GNU tools use another prefix.

Initialize the submodule and configure your three local input paths:

```sh
git -c protocol.file.allow=always submodule update --init
cp mise.local.example.toml mise.local.toml
# Edit mise.local.toml: stock OS file, Selache checkout, DN2 DigiHealth .elemod.
mise trust
mise install
```

`DN2_STOCK` is your own Digitone II OS 1.12 `.syx` file. `SELACHE` is the
Selache checkout directory. `DN2_DIGIHEALTH` is the DN2 module built from
your DigiHealth fork; it supplies diagnostics and remote buttons. Ordinary
builds accept 1.3 or newer; measurement readback requires 1.5 or newer. These
files and the local configuration are not committed. Environment variables
with those names can also supply the paths.

## Outputs and checks

| Command | Sources | Firmware |
|---|---|---|
| `mise run build-c` | `examples/sharc-sine/sine.c` and `entry.c` | `out/c/firmware.syx` |
| `mise run build-asm` | `examples/sharc-sine-asm/sine.s` and the shared `entry.c` | `out/asm/firmware.syx` |
| `mise run build-c-l2` | Same C sources, `l2.ldf` | `out/c-l2/firmware.syx` |
| `mise run build-asm-l2` | Same ASM/shared wrapper, `l2.ldf` | `out/asm-l2/firmware.syx` |
| `mise run build-c-l2-metrics` | Same C/L2 sources, optional measurement core | `out/c-l2-metrics/firmware.syx` |
| `mise run build-asm-l2-metrics` | Same ASM/L2 sources, optional measurement core | `out/asm-l2-metrics/firmware.syx` |

Each command rebuilds its own output directory. It compiles and links the
kernel and C entry wrapper, reads callback locations from the linked
executable, and generates `out/<kind>/mod/sine.sharc.json`. It then uses only
the submodule's SDK, lint and firmware builder. The checked-in reference
artifacts and development qualification scripts are not build inputs.
No Elekloader helper paths or `PYTHONPATH` setup are needed.

The L1 commands retain the existing keyed ABI, 768-byte code area, 256-byte data area, pitch-table
address and 128-byte declared app stack contribution are retained. The
768-byte code area includes a required 64-byte NOP read-ahead guard, leaving
at most 704 bytes for linked instructions. The builder enforces this bound.
The [callback contract](vendor/elekloader/docs/SHARC-DN2-ABI.md) specifies the
current init/render, gate and register preservation rules. Builds
check artifact ownership and callback locations; Elekloader checks the core,
conflicts, budgets and resulting firmware. Runtime behavior and actual stack
usage still need qualification when DSP code changes. The generated
`out/<kind>/build-report.json` records source/output hashes, toolchain
revision, compiler binary hashes and host/Python versions. Firmware,
modules, compiler outputs and reports stay under ignored `out/`.

The L2 commands use `dn2-master-mono32-l2-v1` at loader
`0x20090000..0x20097fff`: 16 KiB code (including the same 64-byte guard)
and 16 KiB data. Its data starts at `0x20094000`; its code PC16 starts at
`0xbc8000`. The submodule installs the pinned bridge pitch table in L1.
The 128-byte app stack declaration is unchanged; L2 adds no stack.
See [L2 evidence and remaining limits](vendor/elekloader/docs/SHARC-L2.md).

The optional measurement commands keep those DSP bytes, addresses, guards
and 128-byte app declaration. They select `dn2-master-mono32-l2-metrics-v1`
and the matching core, whose eight-byte wrapper reduces the maximum app
stack allowance to 420 bytes. DigiHealth 1.5 reports raw render-callback
counters, not total DSP load or spare CPU time; absent/stale snapshots are
unavailable. See the [measurement contract](vendor/elekloader/docs/SHARC-DN2-METRICS.md).
No Elekloader development helper is part of these commands.

Run the firmware-free producer tests with `mise run test`. Both placements
passed coupled Digikit and short physical USB note checks. Each L2 variant
also passed a ten-minute physical sweep of all 49 supported notes eight
times (392 note/release pairs), with zero dropped capture buffers. The submodule records the later bounded project/effects checks and restoration evidence.
The corrected exact-stock memory audit closes Elekloader #53 with no
observed app-window references within its coverage. Indirect accesses and
physical cache/interrupt behavior remain limits. Heavy stock-project stress
is tracked in #56; its first multichannel/pattern increment passed bounded
controls with both C/ASM sines. A01 sequencer notes compete with external
MIDI for the single voice, so the mixed-run isolated-note oracle applies
only to unopposed A02/A03 intervals. Bounded project-load and raised-send C/ASM checks pass with DigiHealth 1.4;
wider sustained qualification remains #56 and hardware cycle margin #41. See the
submodule's [sustained results](vendor/elekloader/docs/SHARC-L2.md#ten-minute-physical-note-sweeps).

## Tested scope

Tested on macOS 27.0.1, Apple arm64, with mise Python 3.12.13 and
`m68k-elf-gcc` 16.2.0. The exact stock input is Digitone II OS 1.12 SHA-256
`7e5b8a733d4ab1fdf7e5d22757f7d8eddaa3cbf71d0858874a791844c02ceb35`.
Default-path reproducibility was checked with the original DigiHealth 1.3
module. Optional builds use the owner's [DigiHealth 1.5](https://github.com/simonjohansson/digihealth/commit/16758cc).
Both optional commands reproduce the coupled-emulator-tested firmware exactly;
physical measured-core qualification is tracked separately in the submodule.
The local helper file and generated firmware remain uncommitted.

This is one exclusive, global master-output mod: 32 mono binary32 samples
per block at 48 kHz, copied by the bridge to both channels. The C and ASM
variants cannot be enabled together. These examples do not add selectable
sound/filter machines, per-track routing or screen controls. App code and
data share 1 KiB in the original commands or 32 KiB in the experimental L2
commands. Qualifying more stack remains separate work; this example does
not integrate ModelDigi.

## Sync Elekloader

The submodule's source is `/Users/simonjohansson/src/elekloader`, tracking its
local `sharc` branch, currently pinned to
`5e11fc35031d94613257c32d1b075092425b6a9d`. To pull a newer committed revision:

```sh
git -c protocol.file.allow=always submodule update --init --remote vendor/elekloader
```

Test the builds before committing the updated submodule pin. Uncommitted
Elekloader source edits are not copied. The source repository shares its
`sharc` branch with `/Users/simonjohansson/src/elekloader-sharc`.

The original baseline copied all 14 example files unchanged at `d848df9`.
The C and ASM phase edits then demonstrated the exact-code refusal; the
pinned Elekloader revision accepts bounded application changes while keeping
the keyed bridge pinned. The example sources and build code are
GPL-2.0-or-later; see `LICENSE` and their SPDX headers. Never commit firmware.
