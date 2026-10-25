# SPDX-License-Identifier: GPL-2.0-or-later
"""Build the native-selector clone through the Elekloader submodule."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from build import ROOT, VENDOR, require, sha


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stock', default=os.environ.get('DN2_STOCK'))
    parser.add_argument('--digihealth', default=os.environ.get('DN2_DIGIHEALTH'))
    args = parser.parse_args()
    out = ROOT / 'out/clone'
    require(not out.is_symlink(), 'output directory must not be a symlink')
    if out.exists():
        shutil.rmtree(out)
    if not args.stock or not args.digihealth:
        parser.error('set DN2_STOCK and DN2_DIGIHEALTH in mise.local.toml')
    stock, health = (Path(p).expanduser().resolve() for p in (args.stock, args.digihealth))
    require(stock.is_file() and health.is_file(), 'stock or DigiHealth module is missing')
    require((VENDOR / 'elekloader/__init__.py').is_file(), 'initialize the Elekloader submodule')
    from elekloader import elemod, lint, patch
    from elekloader.sdk import build as sdk
    require(elemod.load_any(str(health)).id == 'digihealth', 'DN2_DIGIHEALTH must be DigiHealth')
    out.mkdir(parents=True)
    try:
        built = []
        for name, source in (('core', VENDOR / 'mods/core-dn2'),
                             ('machines', VENDOR / 'mods/machines-dn2'),
                             ('clone', ROOT / 'examples/synth-clone')):
            module, _ = sdk.build(str(source), str(stock), str(out / name))
            built.append(module)
        core, machines, clone = built
        require(lint.main([clone, '--stock', str(stock), '--with', core,
                           '--with', machines, '--with', str(health)]) == 0, 'clone lint failed')
        outputs, manifest = patch.build(str(stock), [core, str(health), machines, clone], 'MC01')
        firmware = out / 'firmware.syx'
        patch.save(outputs, manifest, str(firmware))
    except (sdk.BuildError, patch.PatchError) as error:
        raise ValueError(str(error)) from error
    report = {'submodule_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=VENDOR,
                                                          text=True).strip(),
              'stock_sha256': sha(stock), 'digihealth_sha256': sha(health),
              'sources_sha256': {str(p.relative_to(ROOT)): sha(p)
                                 for p in (ROOT / 'examples/synth-clone/clone.c',
                                           ROOT / 'examples/synth-clone/mod.json')},
              'modules': {elemod.load_any(p).id: {'path': p, 'sha256': sha(p)} for p in built},
              'firmware': str(firmware), 'firmware_sha256': sha(firmware),
              'master_output_replacement': False}
    (out / 'build-report.json').write_text(json.dumps(report, indent=2) + '\n')
    print('BUILT ' + str(firmware), flush=True)
    print('sha256 ' + report['firmware_sha256'], flush=True)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        sys.exit('BUILD FAILED: ' + str(error))
