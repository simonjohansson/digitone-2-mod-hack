# Digitone II mod experiment

This separate repository starts with unchanged copies of Elekloader's C and
assembly SHARC sine examples. They are under `examples/sharc-sine/` and
`examples/sharc-sine-asm/`; all 14 copied files match the submodule exactly.

`vendor/elekloader` is a local submodule:

- Source: `/Users/simonjohansson/src/elekloader`
- Tracked branch: `sharc`
- Initial pin: `f44ca541a25b94cac6730fb0f93da599a1a127ed`

To fetch a newer committed local `sharc` revision:

```sh
git -c protocol.file.allow=always submodule update --init --remote vendor/elekloader
```

This updates the submodule pin in the working tree; commit that pin when the
new revision has been checked. Uncommitted source edits are not copied.
The local source repository shares its `sharc` branch with the worktree at
`/Users/simonjohansson/src/elekloader-sharc`.

## Experiment order

1. Demonstrate the unchanged C and ASM copies through the existing workflow.
2. Make one small source change in each and record the actual outcome.
3. Change Elekloader only for a demonstrated requirement, with a reproducer.

Setup alone does not demonstrate source compilation, firmware builds,
emulator execution or device output. The copied author scripts retain their
original in-tree path assumptions; test those before changing their setup.
Stock firmware and generated builds stay outside Git. The copied example
sources are GPL-2.0-or-later; see `LICENSE` and their SPDX headers.
