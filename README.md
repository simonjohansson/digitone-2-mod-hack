# Digitone II mod

Build the selectable C SINE synth and C LOWPASS filter with DigiHealth:

```sh
mise run build-c
```

Load `out/c/firmware.syx`. In **FUNC+SYN**, select **SINE** in SYN and
**LOWPASS** in FLTR. SINE uses synth ID 6; LOWPASS uses filter ID 8. The synth
and filter have separate memory and run through the stock track pipeline.
The SYN/FLTR pages have no mod controls; AMP, pan and mixer remain native.
This is experimental and timing is unqualified.

Edit `examples/sharc-sine/sine.c` or `examples/sharc-filter/filter.c` for
DSP changes. LOWPASS is a one-pole filter with a fixed coefficient of 1/16
at 48 kHz (about 493 Hz cutoff). Each voice has its own history, reset when
the adapter signals a new trigger or entry to the filter. Registration
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
and a built DN2 DigiHealth module from your fork.

Build the pinned open [Selache](https://github.com/js216/selache) compiler:

```sh
git clone https://github.com/js216/selache.git "$HOME/src/selache"
cd "$HOME/src/selache"
git switch --detach 2b26d3b75c53063575bc5c820fa0d38879335187
mise exec rust@1.98.1 -- cargo build --release --locked -p selcc -p selas -p seld -p seldump
```

In this repository:

```sh
git -c protocol.file.allow=always submodule update --init
cp mise.local.example.toml mise.local.toml
# Set DN2_STOCK, SELACHE and DN2_DIGIHEALTH.
mise trust
mise install
mise run build-c
```

`SELACHE` points to the clean compiler checkout with its four tools built.
Python 3.12.13 is managed by mise; no Python packages are required.

## Build and test

`mise run build-c` compiles the current source, packages it through the
Elekloader submodule and writes verified firmware with core, machines and
DigiHealth. A failed build removes stale firmware. Configuration, firmware
and build reports are ignored.

`mise run test` checks malformed compiler output, artifact bounds and
independent per-voice phase/retrigger state and the filter's sample bounds.
Native selection, controls, preset copy, saved projects and missing
registrations require runtime checks. See the [voice contract](vendor/elekloader/docs/MACHINES-DN2-VOICE.md)
and [filter interface](vendor/elekloader/docs/MACHINES-DN2-FILTER.md).

The C example plays one octave above integer MIDI notes 36..84, rounds
fractional pitch down and writes zeros outside that range. Elekloader supplies
the linker placement: each mod has 6 KiB of code including its guard and
7 KiB of data inside the shared 32 KiB reservation. SINE may declare 128
stack bytes; the filter callback is limited to 20. The adapter adds
eight bytes to each call. Edited kernels need no instruction allowlist update.

## Sync Elekloader

The submodule tracks the public Elekloader fork's `machines` branch.
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
committed directly to `machines` in each repository.
