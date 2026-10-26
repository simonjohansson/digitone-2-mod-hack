# Digitone II mod

Build the selectable C SINE synth and assembly LOWPASS filter with DigiHealth:

```sh
mise run build-c
```

Load `out/c/firmware.syx`. In **FUNC+SYN**, select **SINE** in SYN and
**LOWPASS** in FLTR. SINE uses synth ID 6; LOWPASS uses filter ID 8. The synth
and filter have separate memory and run through the stock track pipeline.
SINE's SYN page has no controls. LOWPASS shows LOWPASS 4's FLTR page, and
its FREQ, RESO and the stock filter envelope (ENV, ATK/DEC/SUS/REL,
pre-delay) move it. AMP, pan and mixer remain native.
This is experimental and timing is unqualified.

Edit `examples/sharc-sine/sine.c` or `examples/sharc-filter/filter.s` for
DSP changes. LOWPASS is a resonant 2-pole state-variable low-pass. Its
cutoff reads the stock cutoff like a MIDI note (FREQ 69 is 440 Hz, held to
20 Hz..16 kHz) and RESO sets its damping. Each voice has its own state,
reset when the adapter signals a new trigger or entry to the filter. It is
written in assembly because the filter callback may use only 20 stack
bytes, which a C filter of this size exceeds with Selache (elekloader#91). Registration
uses `mk2/machines.h` and the `mk2_*` contribution tables. Only DN2 OS 1.12
is implemented.

## Set up

```sh
git clone --recurse-submodules https://github.com/simonjohansson/digitone-2-mod-hack.git
cd digitone-2-mod-hack
```

Install [mise](https://mise.jdx.dev/) and the ColdFire cross tools. Supply
your own stock Digitone II OS 1.12 file, SHA-256
`7e5b8a733d4ab1fdf7e5d22757f7d8eddaa3cbf71d0858874a791844c02ceb35`,
and a built [DN2 DigiHealth module](https://github.com/simonjohansson/digihealth/tree/codex/dn2-remote-control/dn2#build).
Set `DN2_DIGIHEALTH` to the module filename printed by that build.

Build the pinned open [Selache](https://github.com/js216/selache) compiler:

```sh
git clone https://github.com/js216/selache.git "$HOME/src/selache"
cd "$HOME/src/selache"
git switch --detach 2b26d3b75c53063575bc5c820fa0d38879335187
mise exec rust@1.98.1 -- cargo build --release --locked -p selcc -p selas -p seld -p seldump
```

In this repository:

```sh
git submodule update --init
cp mise.local.example.toml mise.local.toml
# Set DN2_STOCK, SELACHE and DN2_DIGIHEALTH.
mise trust
mise install
mise run build-c
```

`SELACHE` points to the clean compiler checkout with its four tools built.
Python 3.12.13 and uv are managed by mise. The build needs no Python
packages; `mise run test` and `mise run qualify` fetch pinned ones with uv.

## Build and test

`mise run build-c` compiles the current source, packages it through the
Elekloader submodule and writes verified firmware with core, machines and
DigiHealth. A failed build removes stale firmware. Configuration, firmware
and build reports are ignored.

`mise run test` checks malformed compiler output, artifact bounds and
independent per-voice phase/retrigger state, and the qualification's
measurements, including LOWPASS's closed-form response against a
sample-by-sample model. The assembly filter itself is checked by
`mise run qualify`. See the [voice contract](vendor/elekloader/docs/MACHINES-DN2-VOICE.md)
and [filter interface](vendor/elekloader/docs/MACHINES-DN2-FILTER.md).

## Qualify

`mise run qualify` builds one candidate, labelled with the four
characters the unit shows, and runs the same cases in DigiKit's coupled
emulator and on the unit: SINE and FM TONE, each through MULTI-MODE and
LOWPASS on track 1. Each case selects the machines and reads them back
from RAM, opens the stock filter over MIDI, records notes 36 and 84,
and checks the FLTR page. Every note must sound at its pitch, and
LOWPASS at FREQ 72 must take note 84 down against note 36 by its response
at the synth's pitch, within -2/+3 dB, compared with the open stock
filter. LOWPASS's page must match LOWPASS 4's pixel for pixel, except for
an empty response graph. Note 60 must follow LOWPASS's response at four
FREQ and two RESO settings within 1.5 dB. Over a held note, ENV 0 must
leave it steady, positive ENV must open then close it, negative ENV the
reverse, ATK must delay its peak and pre-delay its start. Tracks 1 and 2
played together must each follow their own FREQ, an LFO on track 1 must
leave track 2's note alone, and FLTR2 BASE and AMP VOL must change LOWPASS
as they change MULTI-MODE. Key tracking must move it by its response, and
LFO 1 on FREQ, RESO, ENV, pre-delay and ATK/DEC/SUS/REL must each change
the note. Parameter locks on step 9 must change that step and not step 1;
for now this runs in the emulator only, because DigiHealth's remote turns
do not edit a held trig's lock on the unit. Back on LOWPASS after
MULTI-MODE, the first note must match a repeat of it, and a note must
sound the same after the project is saved and loaded. Each of its controls must move with its knob
(pre-delay with FUNC+A), its CC and its NRPN, read back from RAM. The
filter and its values must survive SAVE PROJECT AS into slot 128 and
LOAD PROJECT. Nothing is checked by hand. The report, the recordings and
the firmware's SHA-256 go to `out/qualify/`.

The unit part flashes the candidate with `elektroid-cli` and confirms it
with DigiHealth's remote YES, so the installed firmware must include
DigiHealth. It runs only in the project recorded as the scratch project.
It saves only into slot 128, and only when that slot is empty or holds
the project an earlier run saved there. Afterwards it loads the scratch
project again, discarding the run's changes. Load a project you can
lose, then record it once:

```sh
mise run qualify -- --remember-scratch
```

Set `DIGIHEALTH`, `DIGIKIT`, `DIGI_EMUD` and `DN2_EMU_READY` in
`mise.local.toml` (see the example). Without a unit, the device part is
reported as skipped and the run exits with status 2, not as passed.
`--targets emulator`, `--cases save,tracks`, `--no-build` and `--no-flash` run
part of it; after a failure, rerun only the failed cases on the same candidate.

The C example plays one octave above integer MIDI notes 36..84, rounds
fractional pitch down and writes zeros outside that range. Elekloader supplies
the linker placement: each mod has 6 KiB of code including its guard and
7 KiB of data inside the shared 32 KiB reservation. SINE may declare 128
stack bytes; the filter callback is limited to 20, which the assembly
LOWPASS meets by using none. The adapter adds eight bytes to each call. Edited kernels need no instruction allowlist update.

## Sync Elekloader

The submodule tracks the public Elekloader fork's `filter-page` branch.
For local Elekloader development, keep the checkout's remote on your
filesystem with this uncommitted setting:

```sh
git config submodule.vendor/elekloader.url "$HOME/src/elekloader"
cd vendor/elekloader
git config remote.origin.url "$HOME/src/elekloader"
cd ../..
```

Then sync from the repository root:

```sh
git -c protocol.file.allow=always submodule update --remote vendor/elekloader
mise run test
mise run build-c
```

Commit the submodule pointer after checking the build. All feature work is
committed directly to `filter-page` in each repository.
