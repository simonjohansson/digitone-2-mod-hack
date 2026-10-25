# Digitone II mod examples

The `machines` branch adds a C registration fixture in the native synth
selector:

```sh
mise run build-clone
```

It includes core-dn2 0.2, the `machines-dn2` companion and DigiHealth. Open
**FUNC+SYN**, choose **CLONE** and press **YES** once to assign it. CLONE
uses FM TONE's sound and parameter pages; it has no master-output sine or
custom SHARC code. The DSP firmware stays stock. This first step proves
native selection and saved identity before adding a custom synth voice.
It uses the existing ColdFire compiler and does not need Selache.

The descriptor is in `examples/synth-clone/clone.c`. Its four fields are the
permanent ID (5 here), the stock synth (0 = FM TONE), the display name and
the short label. The manifest contributes a pointer to `dn2_synths`; it has
no firmware hook sites. IDs do not depend on mod load order. Duplicate IDs
are refused, and an installed companion keeps an absent mod's ID as a
silent MISSING machine. See the [companion contract](vendor/elekloader/docs/MACHINES-DN2.md).

The existing master-output SHARC sine remains a separate C baseline:

Compile the local SHARC C sine and build Digitone II OS 1.12 firmware:

```sh
mise run build-c
```

The sine commands use 32 KiB automatically and include core-dn2 0.2 and your
DigiHealth module for remote buttons. The local C and ASM edits advance
phase twice per sample, so notes sound one octave above the MIDI input.
The mod owns the final mono output, which the core copies to both channels.

## Set up once

You need [mise](https://mise.jdx.dev/), the ColdFire cross tools, your stock
Digitone II OS 1.12 file, and a DN2 DigiHealth `.elemod` built from your fork
at `~/src/digihealth`. The supplied local configuration uses
`m68k-elf-` as the ColdFire tools' prefix.

Only the master-output sine needs Selache; skip this compiler step for
CLONE. For the sine, build the specified [Selache](https://github.com/js216/selache)
revision once:

```sh
git clone https://github.com/js216/selache.git "$HOME/src/selache"
cd "$HOME/src/selache"
git switch --detach 2b26d3b75c53063575bc5c820fa0d38879335187
mise exec rust@1.98.1 -- cargo build --release --locked -p selcc -p selas -p seld -p seldump
```

Back in this mod repository, initialize the local Elekloader submodule and
configure `DN2_STOCK` and `DN2_DIGIHEALTH` (`SELACHE` is only for the sine):

```sh
git -c protocol.file.allow=always submodule update --init
cp mise.local.example.toml mise.local.toml
# Edit DN2_STOCK, SELACHE and DN2_DIGIHEALTH in mise.local.toml.
mise trust
mise install
mise run build-clone
```

`DN2_STOCK` is your stock `.syx` file; its SHA-256 must be
`7e5b8a733d4ab1fdf7e5d22757f7d8eddaa3cbf71d0858874a791844c02ceb35`.
`SELACHE` is the clean compiler checkout with its four release tools built.
`DN2_DIGIHEALTH` is required for every build and must name a DN2 DigiHealth
module. The commands have been checked with the owner's DigiHealth 1.5.
Python 3.12.13 is managed by mise; no Python packages are required.

## Build outputs and checks

| Command | Source | Firmware |
|---|---|---|
| `mise run build-clone` | `examples/synth-clone/clone.c` (stock DSP) | `out/clone/firmware.syx` |
| `mise run build-c` | `examples/sharc-sine/sine.c` and `entry.c` | `out/c/firmware.syx` |
| `mise run build-asm` | `examples/sharc-sine-asm/sine.s` and the shared `entry.c` | `out/asm/firmware.syx` |

Each sine command compiles the current source, generates `out/<kind>/mod/sine.sharc.json`,
and uses the submodule's SDK to package the mod and core. It then checks the
combination and builds verified firmware with DigiHealth. The compiler
inspector supplies the actual function entry addresses. Build reports record
source, compiler, submodule, module and firmware identities. Each output
folder is cleared before the build, so a failed attempt cannot leave old
firmware there. Git ignores local configuration and all outputs.

The layout provides 16,320 bytes of instructions, 64 bytes of NOP padding
and 16 KiB of data. Each example declares 128 stack bytes; the maximum mod
allowance is 428. Follow the [DSP function interface](vendor/elekloader/docs/SHARC-DN2-ABI.md)
for register preservation, memory access and pitch/release behavior. Build
checks validate the declared layout; changed DSP code still needs execution
and timing tests. This master-output sine has no per-track routing. The
separate CLONE
fixture adds native selection with stock synthesis; custom per-voice DSP
is the next step.

Run `mise run test` for the firmware-free producer tests. These check changed
code/data, malformed compiler output, memory bounds, the compiler revision
and cleanup after a failed build. The source-build workflow is tested on
macOS arm64; see [the upstream evidence and limits](vendor/elekloader/docs/SHARC-DN2.md)
for the core's execution evidence.

Both current C/ASM builds were cold-loaded in the coupled DigiKit emulator
with DigiHealth 1.5. Each rendered 3,122 DSP blocks (99,904 stereo frames)
for MIDI notes 60, 69 and 72, at the expected octave-up pitches. Installed
code and callback addresses matched, channels were identical, samples were
finite, peak was 0.125, and idle/release tails were silent. No DSP halt or
missing block was reported. These are emulator results, not hardware timing.

## Sync Elekloader

The submodule uses the local repository `/Users/simonjohansson/src/elekloader`
and tracks `machines`. That integration branch starts at upstream main
`06319680960ae2b6dfe7fabe1608dc97581ec94e`; changes are committed linearly.
The submodule pin records the tested revision.

Commit changes to the local Elekloader `machines` branch, then:

```sh
git -c protocol.file.allow=always submodule update --remote vendor/elekloader
mise run test
mise run build-clone
mise run build-c
```

Commit the new submodule pin after checking the builds. Local uncommitted
Elekloader edits are not copied by a submodule update.

The ASM source and `build-asm` command are retained as a reference. New
machine work compiles and tests C only.

The example sources and build scripts are GPL-2.0-or-later. Never commit stock
firmware, built firmware, extracted sections or built `.elemod` files.
