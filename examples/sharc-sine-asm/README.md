# Assembly sine

Edit `sine.s` and run `mise run build-asm` from the repository root.
The command assembles this source and compiles the shared C `entry.c`, then
builds verified DN2 firmware with the Elekloader submodule and DigiHealth.

The assembly uses the same phase accumulator and table as the C sine,
including the octave-up edit. Both variants share the same bridge,
32 KiB layout and attack/release wrapper.

See the [setup and build guide](../../README.md) and
[DSP function interface](../../vendor/elekloader/docs/SHARC-DN2-ABI.md).
