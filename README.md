# Digitone II mod examples

Edit `examples/sharc-sine/sine.c`, then build a selectable track synth:

```sh
mise run build-c
```

Load `out/c/firmware.syx`. Open **FUNC+SYN**, choose **SINE**, and press
**YES** to assign it to the selected track. It uses the stock AMP envelope,
filter, pan and mixer; other tracks can keep their stock synths. The SYN
page has no FM controls pretending to affect SINE. The native machine selector identifies the engine as SINE.

The C example deliberately plays one octave above its MIDI input. It has
independent phase for each of 16 voice slots and resets phase on retrigger.
Integer MIDI pitches 36..84 are supported; other pitches produce zero
oscillator samples. Fractional pitch is rounded down to a MIDI note.

Every build includes core-dn2 0.2, machines-dn2 0.2 and the configured
DigiHealth module. There are no mod-local firmware hook sites. SINE uses
permanent ID **6**; ID **5** belongs to the separate CLONE fixture. Keep
these identities when changing code or combining mods.

## Set up once

Install [mise](https://mise.jdx.dev/), the ColdFire cross tools, and your
DN2 DigiHealth mod from `~/src/digihealth`. Supply your own stock Digitone
II OS 1.12 file, SHA-256
`7e5b8a733d4ab1fdf7e5d22757f7d8eddaa3cbf71d0858874a791844c02ceb35`.

Build the qualified [Selache](https://github.com/js216/selache) revision:

```sh
git clone https://github.com/js216/selache.git "$HOME/src/selache"
cd "$HOME/src/selache"
git switch --detach 2b26d3b75c53063575bc5c820fa0d38879335187
mise exec rust@1.98.1 -- cargo build --release --locked -p selcc -p selas -p seld -p seldump
```

Back in this repository:

```sh
git -c protocol.file.allow=always submodule update --init
cp mise.local.example.toml mise.local.toml
# Set DN2_STOCK, SELACHE and DN2_DIGIHEALTH in mise.local.toml.
mise trust
mise install
mise run build-c
```

`SELACHE` names the clean compiler checkout with all four tools built.
`DN2_DIGIHEALTH` is required; this candidate was checked with DigiHealth
1.5. Python 3.12.13 is managed by mise. No Python packages are required.

## Build and sync

| Command | Result |
|---|---|
| `mise run build-c` | Selectable C SINE, `out/c/firmware.syx` |
| `mise run build-clone` | Stock FM TONE clone named CLONE, `out/clone/firmware.syx`; no SHARC compiler needed |
| `mise run build-asm` | Earlier assembly master-output reference, `out/asm/firmware.syx`; it is not a selectable track synth |
| `mise run test` | Producer, independent-phase and retrigger checks without firmware execution |

The producer compiles current source, reads the linked function entry,
packages it through the Elekloader submodule, checks the mod combination
and writes verified firmware. Each output folder is cleared before a
build, so a failed compile leaves no stale firmware. Build reports in
`out/<kind>/build-report.json` record the sources, compiler, submodule,
modules and firmware. Configuration, firmware and captures are ignored.

The C command selects the voice layout automatically. The 32 KiB area
includes its pinned adapter and state; the author area has 14 KiB of code
(including the guard) and 15 KiB of data. A source edit needs no Elekloader
allowlist update. See the [voice contract](vendor/elekloader/docs/MACHINES-DN2-VOICE.md)
for callback, register and stack requirements.

The submodule fetches `machines` from the local Elekloader checkout:

```sh
cd vendor/elekloader
git -c protocol.file.allow=always fetch origin machines
git switch --detach FETCH_HEAD
cd ../..
mise run build-c
```

Commit the updated submodule pointer in this repository after checking the
new firmware. The source checkout and this consumer both use the linear
`machines` workflow.

## Qualification

This track-synth candidate is qualified in the coupled DigiKit emulator,
with DigiHealth. The checked cases include native selection, overlapping
notes, all 16 allocated slots, 24 retriggers including slot reuse, release,
stock-track coexistence, AMP volume and filter response. A half-gain C edit
changes the output as expected, and restoring the source reproduces the
original firmware and audio. Saved-project and missing-mod results are
recorded in the companion's [qualification record](vendor/elekloader/docs/MACHINES-DN2-VOICE.md).

No hardware or timing qualification is claimed for this new track-synth
callback. Restoring its stock hook exposes FM TONE beneath the replacement;
its possible extra oscillator cost is tracked in #66 in the Elekloader
fork. The assembly master-output reference has a different callback
contract and is outside this track-synth qualification. #65 remains deferred.
