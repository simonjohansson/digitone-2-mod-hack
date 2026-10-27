# SPDX-License-Identifier: GPL-2.0-or-later
"""Check the build's toolchain pin, failure handling and SINE's kernel without firmware."""
import shutil
import sys
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch
import unittest

import build
check_toolchain = build.check_toolchain


class BuildTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('git'), 'needs git')
    def test_toolchain_pin_and_clean_source(self):
        with tempfile.TemporaryDirectory() as work:
            repo = Path(work)
            subprocess.run(['git', 'init', '-q', work], check=True)
            source = repo / 'source.c'
            source.write_text('initial\n')
            subprocess.run(['git', 'add', 'source.c'], cwd=repo, check=True)
            subprocess.run(['git', '-c', 'commit.gpgsign=false', '-c', 'user.name=Build Test', '-c', 'user.email=test@example.invalid',
                            'commit', '-qm', 'Initialize compiler fixture'], cwd=repo, check=True)
            revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()
            with self.assertRaisesRegex(ValueError, 'Selache must be at'):
                check_toolchain(repo)
            with patch.object(build, 'SELACHE_PIN', revision):
                self.assertEqual(check_toolchain(repo), revision)
                source.write_text('modified\n')
                with self.assertRaisesRegex(ValueError, 'checkout must be clean'):
                    check_toolchain(repo)

    def test_failed_compilation_removes_stale_firmware(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            stock = root / 'stock.syx'
            stock.touch()
            tools = root / 'target/release'
            tools.mkdir(parents=True)
            for name in build.TOOLS:
                (tools / name).touch()
            firmware = root / 'out/c/firmware.syx'
            firmware.parent.mkdir(parents=True)
            firmware.write_bytes(b'stale')
            args = ['build.py', '--stock', str(stock), '--selache', str(root), '--digihealth', str(stock)]
            with patch.object(build, 'ROOT', root), patch.object(sys, 'argv', args), \
                    patch.object(build, 'check_toolchain', return_value=build.SELACHE_PIN), \
                    patch('elekloader.elemod.load_any', return_value=Mock(id='digihealth')), \
                    patch.object(build, 'run', side_effect=ValueError('compile failed')):
                with self.assertRaisesRegex(ValueError, 'compile failed'):
                    build.main()
            self.assertFalse(firmware.exists())


class VoiceStateTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('cc'), 'native C compiler unavailable')
    def test_phase_is_independent_and_retrigger_restarts_it(self):
        source = build.ROOT / 'examples/sharc-sine/sine.c'
        include = build.VENDOR / 'elekloader/sdk/include'
        with tempfile.TemporaryDirectory() as work:
            p = Path(work)
            (p/'test.c').write_text('#include <assert.h>\n#include <string.h>\n'
                + '#include "'+str(source)+'"\n'+'''
int main(void) {
 float a[32], b[32], first[32];
 uint32_t knobs[DN2_VOICE_PARAMS]={0};
 knobs[0]=127u<<8; knobs[1]=64u<<8; knobs[8]=64u<<8; knobs[12]=64u<<8;  /* LEV full, TUNE/OCT centred */
 struct dn2_voice_input in={a,0,60u<<8,DN2_VOICE_START,knobs};
 sharc_voice_render(&in); memcpy(first,a,sizeof a);
 uint32_t saved=phase[0];
 in.output=b; in.slot=1; in.pitch=64u<<8; sharc_voice_render(&in);
 assert(phase[0]==saved && phase[1]!=saved);
 uint32_t other=phase[1];
 in.output=a; in.slot=0; in.pitch=60u<<8; in.flags=0;
 sharc_voice_render(&in); assert(memcmp(first,a,sizeof a));
 in.flags=DN2_VOICE_START; sharc_voice_render(&in);
 assert(!memcmp(first,a,sizeof a) && phase[1]==other);
 in.slot=15; in.pitch=85u<<8; sharc_voice_render(&in);
 for(int i=0;i<32;++i) assert(a[i]==0.0f);
 return 0;
}
''')
            subprocess.run(['cc','-std=c99','-Wall','-Werror','-I',str(include),str(p/'test.c'),'-o',str(p/'test')],check=True)
            subprocess.run([str(p/'test')],check=True)


    @unittest.skipUnless(shutil.which('cc'), 'native C compiler unavailable')
    def test_knobs_set_level_and_pitch(self):
        source = build.ROOT / 'examples/sharc-sine/sine.c'
        include = build.VENDOR / 'elekloader/sdk/include'
        with tempfile.TemporaryDirectory() as work:
            p = Path(work)
            (p/'test.c').write_text('#include <assert.h>\n#include <math.h>\n'
                + '#include "'+str(source)+'"\n'+'''
static uint32_t knobs[DN2_VOICE_PARAMS];
static float out[32];
/* One START block of note 60; returns the phase it advanced, fills out. */
static uint32_t block(uint32_t lev, uint32_t tune, uint32_t oct) {
 knobs[0]=lev<<8; knobs[1]=tune<<8; knobs[8]=oct<<8;
 struct dn2_voice_input in={out,0,60u<<8,DN2_VOICE_START,knobs};
 sharc_voice_render(&in); return phase[0];
}
static float peak(void) { float m=0; for(int i=0;i<32;++i) if(fabsf(out[i])>m) m=fabsf(out[i]); return m; }
int main(void) {
 uint32_t base=block(127,64,64); float full=peak(); assert(full>0.1f);
 block(0,64,64); assert(peak()==0.0f);                        /* LEV 0 is silent */
 block(63,64,64); assert(fabsf(peak()/full-63.0f/127.0f)<1e-4f); /* LEV scales */
 assert(block(127,76,64)==2*base);                            /* TUNE +12 doubles the step */
 assert(block(127,64,80)==2*base && block(127,64,79)==base);  /* OCT: one octave per 16 */
 assert(block(127,64,48)==base/2);
 block(127,64,0); assert(peak()==0.0f);                       /* below note 36: silent */
 return 0;
}
''')
            subprocess.run(['cc','-std=c99','-Wall','-Werror','-I',str(include),str(p/'test.c'),'-o',str(p/'test'),'-lm'],check=True)
            subprocess.run([str(p/'test')],check=True)


if __name__ == '__main__':
    unittest.main()
