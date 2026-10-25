# SPDX-License-Identifier: GPL-2.0-or-later
"""Closed-form low-pass response, independent slots and reset boundaries."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import build


class FilterTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('cc'), 'native C compiler unavailable')
    def test_response_state_reset_and_sample_bounds(self):
        source = build.ROOT / 'examples/sharc-filter/filter.c'
        with tempfile.TemporaryDirectory() as work:
            p = Path(work)
            (p / 'check.c').write_text('#include <assert.h>\n#include <math.h>\n'
                '#include <string.h>\n#include "' + str(source) + '"\n' + r'''
static void near(float a, double b) { assert(fabs((double)a-b) < 3e-7); }
int main(void)
{
    float a[34], b[32], first[32];
    a[0] = -123.0f; a[33] = 456.0f;
    memset(a+1,0,32*sizeof(float)); a[1]=1;
    struct dn2_filter_input x={a+1,0,DN2_FILTER_RESET};
    sharc_filter_render(&x);
    for(unsigned i=0;i<32;++i) near(a[i+1],0.0625*pow(0.9375,i));
    assert(a[0]==-123.0f && a[33]==456.0f);
    for(unsigned i=0;i<32;++i) b[i]=1;
    struct dn2_filter_input y={b,15,DN2_FILTER_RESET};
    sharc_filter_render(&y);
    for(unsigned i=0;i<32;++i) near(b[i],1-pow(0.9375,i+1));
    memset(a+1,0,32*sizeof(float)); x.flags=0; sharc_filter_render(&x);
    for(unsigned i=0;i<32;++i) near(a[i+1],0.0625*pow(0.9375,i+32));
    for(unsigned i=0;i<32;++i) b[i]=1;
    y.flags=0; sharc_filter_render(&y);
    for(unsigned i=0;i<32;++i) near(b[i],1-pow(0.9375,i+33));
    x.flags=DN2_FILTER_RESET; memset(a+1,0,32*sizeof(float));
    sharc_filter_render(&x);
    for(unsigned i=0;i<32;++i) assert(a[i+1]==0);
    for(unsigned i=0;i<32;++i) a[i+1]=1;
    sharc_filter_render(&x); memcpy(first,a+1,sizeof first);
    for(unsigned i=0;i<32;++i) a[i+1]=1;
    sharc_filter_render(&x); assert(!memcmp(first,a+1,sizeof first));
    assert(a[0]==-123.0f && a[33]==456.0f);
    return 0;
}
''')
            subprocess.run(['cc', '-std=c99', '-Wall', '-Wextra', '-Werror',
                            '-I', str(build.VENDOR/'elekloader/sdk/include'),
                            str(p/'check.c'), '-lm', '-o', str(p/'check')], check=True)
            subprocess.run([str(p/'check')], check=True)


if __name__ == '__main__':
    unittest.main()
