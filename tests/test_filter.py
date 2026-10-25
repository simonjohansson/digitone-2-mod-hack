# SPDX-License-Identifier: GPL-2.0-or-later
"""The filter execution controls preserve their 32-sample boundary."""
import shutil
import subprocess
import tempfile
import unittest
import build
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class FilterTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('cc'), 'native C compiler unavailable')
    def test_unity_and_half_gain_only_write_the_supplied_block(self):
        with tempfile.TemporaryDirectory() as work:
            p = Path(work)
            for name, gain in [('unity', '1.0f'), ('half', '0.5f')]:
                subprocess.run(['cc', '-std=c99', '-Wall', '-Wextra', '-Werror',
                    '-I', str(build.VENDOR/'elekloader/sdk/include'), '-DFILTER_GAIN=' + gain, '-Dsharc_filter_render=' + name,
                    '-c', str(ROOT / 'examples/sharc-filter/filter.c'),
                    '-o', str(p / (name + '.o'))], check=True)
            (p / 'check.c').write_text(r'''#include <assert.h>
struct dn2_filter_input { float *samples; unsigned slot; };
void unity(const struct dn2_filter_input *);
void half(const struct dn2_filter_input *);
int main(void) {
    float a[34], b[34];
    for (unsigned i=0; i<34; ++i) a[i]=b[i]=(int)i / 64.0f - 0.25f;
    struct dn2_filter_input x={a+1,0}, y={b+1,15};
    unity(&x); half(&y);
    for (unsigned i=0; i<34; ++i) {
        float expected=(int)i / 64.0f - 0.25f;
        assert(a[i]==expected);
        assert(b[i]==(i>0 && i<33 ? expected*0.5f : expected));
    }
    return 0;
}
''')
            subprocess.run(['cc', str(p/'check.c'), str(p/'unity.o'), str(p/'half.o'),
                            '-o', str(p/'check')], check=True)
            subprocess.run([str(p/'check')], check=True)
