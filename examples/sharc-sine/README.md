# DN2 SHARC C sine

From the repository root, after the [one-time setup](../../README.md#setup-once):

```sh
mise run build-c
```

This compiles the current `sine.c` and the shared C entry wrapper, generates
a fresh artifact under `out/`, and builds verified firmware through the local
Elekloader submodule. The firmware includes the core and DN2 DigiHealth.
The current kernel doubles the phase advance, playing MIDI notes one octave
higher than the reference sine, with the same 0.125 peak and gain ramp.

See the [root README](../../README.md) for output paths and the ABI limits.
The copied prebuilt artifact and author qualification scripts are retained
for reference; the build command does not use them.
