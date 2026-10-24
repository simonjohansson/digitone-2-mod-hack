# Digitone II mod experiment

Build either local SHARC sine into a Digitone II OS 1.12 firmware using
Elekloader as a submodule:

```sh
mise run build-c
mise run build-asm
```

The commands always compile the current sources. Both examples currently
advance phase twice per sample, so MIDI notes play one octave higher than
the original examples. Each firmware includes the DN2 core and DigiHealth.

## Setup once

Install [mise](https://mise.jdx.dev/), the ColdFire `m68k-elf-` tools, and
[Selache](https://github.com/js216/selache). The tested Selache revision is
`2b26d3b75c53063575bc5c820fa0d38879335187`; its `selcc`, `selas`, `seld` and
`seldump` binaries must be built under `target/release/`. Python 3.12.13 is
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
Selache checkout directory. `DN2_DIGIHEALTH` is the DN2 1.3 module built from
your DigiHealth fork; it supplies diagnostics and remote buttons. These
files and the local configuration are not committed. Environment variables
with those names can also supply the paths.

## Outputs and checks

| Command | Sources | Firmware |
|---|---|---|
| `mise run build-c` | `examples/sharc-sine/sine.c` and `entry.c` | `out/c/firmware.syx` |
| `mise run build-asm` | `examples/sharc-sine-asm/sine.s` and the shared `entry.c` | `out/asm/firmware.syx` |

Each command rebuilds its own output directory. It compiles and links the
kernel and C entry wrapper, reads callback locations from the linked
executable, and generates `out/<kind>/mod/sine.sharc.json`. It then uses only
the submodule's SDK, lint and firmware builder. The checked-in reference
artifacts and development qualification scripts are not build inputs.
No Elekloader helper paths or `PYTHONPATH` setup are needed.

The existing keyed ABI, 768-byte code area, 256-byte data area, pitch-table
address and 128-byte declared app stack contribution are retained. Builds
check artifact ownership and callback locations; Elekloader checks the core,
conflicts, budgets and resulting firmware. Runtime behavior and actual stack
usage still need qualification when DSP code changes. The generated
`out/<kind>/build-report.json` records source and output hashes. Firmware,
modules, compiler outputs and reports stay under ignored `out/`.

Run the firmware-free producer tests with `mise run test`. The committed
source pair has also been checked in Digikit; emulator evidence does not
establish physical-device timing or reliability.

## Sync Elekloader

The submodule's source is `/Users/simonjohansson/src/elekloader`, tracking its
local `sharc` branch, currently pinned to
`6ad9f9703f6fd0c2e1ef8e4edb92bd203aefa4db`. To pull a newer committed revision:

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
