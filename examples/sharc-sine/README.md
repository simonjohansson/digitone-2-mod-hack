# DN2 SHARC sine

This ordinary format-2 mod plays a monophonic, 0.125-peak sine from external
MIDI notes 36–84 on Digitone II OS 1.12. The core's H1 bridge reads the stock
SHARC voice frame, selects the newest triggered allocated slot, and takes its
settled pitch on the next render block. A release for a different slot leaves
the selected note playing; a matching release fades it out. The compiled SHARC
code generates the samples and a fixed 32-sample attack/release ramp, then the
bridge copies the 32-float master block to the right channel. No UI or new
control transport is involved. [Issue #47](https://github.com/simonjohansson/elekloader/issues/47)
tracks the device proof.

Set `DN2_STOCK` to your own exact OS 1.12 file and `DN2_CORE` to a matching
`core-dn2` `.elemod`. Then use the normal module commands:

```sh
python -m elekloader.sdk.build examples/sharc-sine --stock "$DN2_STOCK"
python -m elekloader.lint examples/sharc-sine/out/sharc-sine-0.1.elemod --stock "$DN2_STOCK" --with "$DN2_CORE" --json
python -m elekloader.patch --stock "$DN2_STOCK" --mod "$DN2_CORE" --mod examples/sharc-sine/out/sharc-sine-0.1.elemod --out sine.syx --version SK02
```

The core build uses the normal ColdFire cross compiler; its checked-in
`bridge.sharc.json` supplies the SHARC code. `sine.c`, `entry.c` and
`mods/core-dn2/sharc-bridge.s` are the author source for the two prelinked
artifacts. The pinned Selache producer checks exact executable sections,
equal-tempered phase steps, full-width register spills and the existing 2 KiB
reservation before replacing either artifact. No artifact contains stock
firmware.

The SDK needs no SHARC compiler for these commands. It validates and embeds
the existing artifacts, and the patcher independently verifies the firmware.
Source changes require an explicit new qualification; the current linker
accepts these exact keyed bytes, the qualified
[assembly kernel variant](../sharc-sine-asm/README.md), and the earlier
fixed-sine pair. The assembly example uses the same entry wrapper and core;
its author benchmark compares both kernels with identical inputs.
The checked author host is macOS on Apple silicon with mise Python 3.12.13,
Rust 1.98.1 and Apple command-line tools. [Install and build Selache](../sharc-sine-probe/README.md#build-and-check)
at commit `2b26d3b75c53063575bc5c820fa0d38879335187` once, then run:

```sh
mise exec python@3.12.13 -- python examples/sharc-sine/rebuild.py /path/to/selache
```

The producer is `rebuild.py` plus
[`keyed.py`](keyed.py), reusing the verified sine-kernel and spill checks.
The [format](../../docs/FORMAT.md#digitone-ii-sharc-app) records the exact
envelope and limits. No firmware, extracted section or generated `.elemod`
belongs in Git.

For a focused contribution, change the source, qualify and regenerate both
artifacts, then run the ordinary three module commands above. Other author
hosts and arbitrary SHARC programs are not yet qualified.

The `SK02` candidate was built from stock OS 1.12 SHA-256
`7e5b8a733d4ab1fdf7e5d22757f7d8eddaa3cbf71d0858874a791844c02ceb35`,
this core, the owner's DigiHealth fork 1.3 and the keyed sine. Its firmware
SHA-256 is `d89cb023a89478d495dfe62b9f71e35c1715f47d595226055253e89f7d52b2cd`.
The author used pinned Selache `2b26d3b75c53063575bc5c820fa0d38879335187`
on macOS arm64 with mise Python 3.12.13 and Rust 1.98.1. Source SHA-256:
`entry.c` `d22f34ce144bb8cfc1670322fdcb00c838e6c8e06e82dadec283144cf77b4dd3`,
`sharc-bridge.s` `46a6fd8a9a7c4a382615b649ec36963d7413903d3646af461f1b13f3a7ef1a3e`;
the unchanged `sine.c` is
`55663cc6d4b6d109b7f607a718fa5a48d3683e4bf4fa09ecf6a9a1c1806fa07e`.
The producer `keyed.py` and layout `keyed.ldf` are
`574979c235db60981eb1ba1108505cb0db2b43e0800af5ac61f9389e4f6273c4` and
`a6133ab1ac516d0200afa948d0e9984781a6fea6f46b6a8c852bef6dcf38e6ee`.
The prelinked app/core artifact SHA-256 values are
`c92cad748f785ece3a75b1492c3d232a4576601eb1ad0cac54b318fa92d44e7f` and
`5a40c795f6d8305a99f679bfb470d37e16e1f81af999f3f4a54a38ca13d8dcf0`.

Digikit cold-booted this exact firmware and reproduced MIDI note/pitch/release
without a DSP halt or missing block. A 16-note burst (36–51) settled on note
51 at 155.575 Hz, then released to silence, again with no halt or missing
block. The DN2 then booted `SK02`, reported
DigiHealth 1.3, and its 24-second USB master capture had 2,251 buffers with
zero drops. The capture SHA-256 is
`d9a4f8611e7053375bc406159a4744d89e0918f9cb4c2eaf8ef925a2d21219b0`.
Both channels were sample-identical. Settled tone peaks were 0.178571 and RMS
about 0.1263; idle and matching releases were silent. Before measurement,
the acceptance limit was at most 2 cents after capture-clock calibration.
A separate 12-second, 1,126-buffer USB callback regression measured
48,000.586 samples/host second (+0.021 cents from nominal 48 kHz). With that
correction, the largest absolute error among notes 36, 48, 60, 69, 70, 72,
81 and 84 was 0.05 cents. Their nominal-rate measurements in Hz were 65.405,
130.8096, 261.6253, 440.0020, 466.1559, 523.2371, 879.9844 and
1046.5184. Octaves doubled and adjacent notes followed equal temperament.

The physical capture also checked two repeated 69s, velocity-zero Note On,
CC123, and selecting 72 over a still-held 69. Releasing old 69 left 72
playing; releasing 72 reached silence. At that note handoff, the largest
adjacent-sample step fell from 0.110 in the earlier `SK01` prototype to
0.012 in `SK02`, normalized to the SHARC output scale. MIDI Stop and
real-time Reset preserved
an externally held note, while the next note and subsequent Note Off/CC123
still worked. That separate 11-second capture had zero dropped buffers and
SHA-256 `1284e291ee8de8f100efef4417387cbc7e22eec22458c8d5fdf8de9be1a933ee`.
That is the observed stock input lifecycle, not a new gate
policy. The local capture and firmware are not committed. This qualifies the
USB master route and the listed input range, not other outputs or worst-case
DSP timing.

Earlier fixed-tone qualification remains useful as a baseline:

On a physical DN2, the `SS01` build (stock OS 1.12, core 0.1, DigiHealth
fork 1.3 and the then-fixed sine 0.1; firmware SHA-256
`89f2cf3b2e0a9c8bfb3d83b50a2f03e51ee896dd729b95dcba153d62ebe30a4a`)
produced a stable 439.999 Hz tone on both USB master channels for 6 seconds,
then 30 seconds after a reinstall. At 48 kHz the measured peak was 0.178571
and RMS 0.126269 per channel: the [DN2 USB pack](https://github.com/ElekLabs/digikit/blob/9dee5d56c328e7091babdfd2c1d363657db78b96/docs/findings/55-fx-adapter.md#L44-L52)
scales the 0.125-peak master buffer by 1.428571. The pre-update baseline and
an `SI01` build carrying the same SHARC data with its callbacks disabled were
both exactly silent. The 30-second capture had no dropped buffers, non-finite
samples or clipping. Its changing samples across the two-page ring rule out a
frozen short-loop replay.
Captures and firmware remain local under ignored `out/`, outside Git. This
qualifies the USB master route for this fixed candidate, not the DAC, other
routes, arbitrary SHARC code or physical timing margin.

The first author iteration changed `sine_init(39370534u)` to
`sine_init(59055800u)` (660 Hz) in a clean copy of this source, then rebuilt
with pinned Selache on macOS arm64. The original author performed this rebuild;
a second contributor and other hosts have not repeated it. The generated
artifact SHA-256 was
`cd821783e36d5e1017980918650e919409a4b7ebf31bdb3e57dd8226e9e7531a`;
it differed from the 440 Hz artifact at only code offsets 328–331. Normal
build/lint/patch, the desktop model, Python web bridge and TypeScript kit
accepted it without Selache. Python and TypeScript built identical firmware
from the same mod set. A DN2 `S660` image with core 0.1, the owner's DigiHealth
fork 1.3 and the generated sine (SHA-256
`b3f18610add6ba4e7985cb75fd3277040b7ab9bd6cea96f8465b0a26646befce`)
booted and left DigiHealth responding. Eight seconds of physical USB master
capture measured 660.004 Hz, peak 0.178571 and RMS 0.126269 on both channels,
with no dropped capture buffers. The capture SHA-256 was
`9a94a3d1c8b0fd3141a22b2e341cbeb36f07b5aba1e26fac6e260685249dc13e`;
the private capture and firmware are not committed. Out-of-range frequency,
changed kernel source, wrong stock, missing core, two SHARC apps and corrupted
app code were each refused. This qualifies one source-edit loop and the USB
master route; it does not qualify arbitrary compiled SHARC code, hardware
timing margin, other audio routes or keyed playing.
