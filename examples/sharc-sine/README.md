# C sine

Edit `sine.c` and run `mise run build-c` from the repository root.
The command compiles this source and `entry.c`, links them with `l2.ldf`,
and builds verified DN2 firmware with the Elekloader submodule and DigiHealth.

The sine uses a uint32 phase accumulator and a positive half-table. The
local phase edit advances twice per sample, so the output is one octave
above the MIDI note. `entry.c` applies the note-held flag and a 32-sample
attack/release ramp.

See the [setup and build guide](../../README.md) and
[DSP function interface](../../vendor/elekloader/docs/SHARC-DN2-ABI.md).
