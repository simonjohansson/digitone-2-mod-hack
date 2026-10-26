# SPDX-License-Identifier: GPL-2.0-or-later
"""The qualification's measurements on synthetic audio and screens."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import numpy as np

import build
import qualify


def tone(hz, seconds=0.8, amplitude=0.01):
    t = np.arange(int(seconds * qualify.RATE)) / qualify.RATE
    silence = np.zeros(qualify.RATE // 5)
    return np.concatenate([silence, amplitude * np.sin(2 * np.pi * hz * t), silence])


def svf(x, freq, reso=0, highpass=False):
    """The example LOWPASS, sample by sample, with the envelope at rest; MODE picks HIGHPASS."""
    cutoff = min(max(440 * 2 ** ((freq - 69) / 12), 20), 16000)
    v = np.pi * cutoff / qualify.RATE
    g = v * (15 - v * v) / (15 - 6 * v * v)
    k = 2 - 1.96 * reso / 128
    a1 = 1 / (1 + g * (g + k))
    a2, ic1, ic2, out = g * a1, 0.0, 0.0, np.empty_like(x)
    a3 = g * a2
    for i, s in enumerate(x):
        v3 = s - ic2
        v1, v2 = a1 * ic1 + a2 * v3, ic2 + a2 * ic1 + a3 * v3
        ic1, ic2, out[i] = 2 * v1 - ic1, 2 * v2 - ic2, s - k * v1 - v2 if highpass else v2
    return out


class MeasurementTests(unittest.TestCase):
    def test_pitch_and_level_of_the_held_note(self):
        hz = qualify.pitch_of(qualify.SINE, 60)
        x = tone(hz)
        self.assertAlmostEqual(qualify.pitch_hz(x) / hz, 1, delta=0.001)
        self.assertAlmostEqual(qualify.level_db(x), 20 * np.log10(0.01 / np.sqrt(2)), delta=0.1)

    def test_silence_has_no_held_part(self):
        x = np.zeros(qualify.RATE)
        self.assertEqual(len(qualify.held(x)), 0)
        self.assertEqual(qualify.level_db(x), -np.inf)
        self.assertEqual(qualify.pitch_hz(x), 0)

    def test_the_closed_form_response_matches_the_example_low_pass(self):
        hz = qualify.pitch_of(qualify.SINE, 60)
        x = tone(hz)
        for freq, reso in ((50, 0), (72, 0), (90, 0), (72, 100), (72, 120)):
            for highpass in (False, True):
                got = qualify.level_db(svf(x, freq, reso, highpass)) - qualify.level_db(x)
                self.assertAlmostEqual(got, qualify.svf_db(hz, freq, reso, highpass), delta=0.1)
        self.assertAlmostEqual(qualify.svf_db(1e-3, 72), 0, delta=1e-6)

    def test_tone_level_separates_two_notes(self):
        x = tone(523.25, amplitude=0.01) + tone(1046.5, amplitude=0.001)
        self.assertAlmostEqual(qualify.tone_db(x, 523.25), 20 * np.log10(0.01 / np.sqrt(2)), delta=0.1)
        self.assertAlmostEqual(qualify.tone_db(x, 1046.5), 20 * np.log10(0.001 / np.sqrt(2)), delta=0.1)
        self.assertEqual(qualify.tone_db(np.zeros(qualify.RATE), 440), -np.inf)

    def test_onsets_find_notes_after_silence(self):
        x = np.zeros(3 * qualify.RATE)
        for start in (1000, qualify.RATE + 1000, 2 * qualify.RATE + 1000):
            x[start:start + qualify.RATE // 8] = 0.01 * np.sin(np.arange(qualify.RATE // 8))
        found = qualify.onsets(x)
        self.assertEqual(len(found), 3)
        for got, start in zip(found, (1000, qualify.RATE + 1000, 2 * qualify.RATE + 1000)):
            self.assertLessEqual(abs(got - start), 2)  # the sine starts at zero
        self.assertEqual(qualify.onsets(np.zeros(qualify.RATE)), [])

    def test_window_levels_follow_a_changing_level(self):
        x = tone(1000, seconds=1)
        x[qualify.RATE // 5 + qualify.RATE // 2:] *= 0.1  # 20 dB down halfway through
        w = qualify.window_db(x)
        self.assertEqual(len(w), 49)
        self.assertAlmostEqual(w[5], 20 * np.log10(0.01 / np.sqrt(2)), delta=0.1)
        self.assertAlmostEqual(w[5] - w[-2], 20, delta=0.1)
        self.assertEqual(len(qualify.window_db(np.zeros(qualify.RATE))), 0)

    def test_page_regions(self):
        empty = np.zeros((64, 128), dtype=np.uint8)
        empty[41:56:2, 25:125:13] = 1  # dotted outlines only
        self.assertFalse(qualify.labels_shown(empty))
        labelled = empty.copy()
        labelled[60, 30:40] = 1
        self.assertTrue(qualify.labels_shown(labelled))

    def test_page_as_stock_needs_the_curve_and_everything_else_equal(self):
        stock = np.zeros((64, 128), dtype=np.uint8)
        stock[60, 30:40] = 1
        stock[45, 25:60] = 1  # the stock response curve
        dots = qualify.curve_dots(64 << 8, 0)
        custom = stock.copy()
        custom[qualify.GRAPH] = 0
        for x, y in dots:
            custom[y, x] = 1
        self.assertTrue(qualify.page_as_stock(custom, stock, dots))
        self.assertFalse(qualify.page_as_stock(stock, stock, dots))
        blank = stock.copy()
        blank[qualify.GRAPH] = 0
        self.assertFalse(qualify.page_as_stock(custom, blank, dots))  # no curve on the stock page
        moved = custom.copy()
        moved[60, 41] = 1
        self.assertFalse(qualify.page_as_stock(moved, stock, dots))

    @unittest.skipUnless(shutil.which('cc'), 'native C compiler unavailable')
    def test_curve_dots_follow_the_examples_curve(self):
        source = build.ROOT / 'examples/sharc-filter/machine.c'
        with tempfile.TemporaryDirectory() as work:
            p = Path(work)
            (p / 'check.c').write_text('#include <stdio.h>\n#include "' + str(source) + '"\n' + r'''
int main(void)
{
    uint8_t y[51];
    for (unsigned freq = 0; freq < 128; freq += 9)
        for (unsigned reso = 0; reso < 128; reso += 11) {
            lowpass.curve(freq << 8, reso << 8, y);
            for (unsigned x = 0; x < 51; ++x)
                printf("%u ", y[x]);
            printf("\n");
        }
    return 0;
}
''')
            subprocess.run(['cc', '-std=c99', '-Wall', '-Wextra', '-Werror', '-I',
                            str(build.VENDOR / 'elekloader/sdk/include'), str(p / 'check.c'), '-o', str(p / 'check')],
                           check=True)
            rows = subprocess.run([str(p / 'check')], check=True, capture_output=True, text=True).stdout.split('\n')
        cases = [(f, r) for f in range(0, 128, 9) for r in range(0, 128, 11)]
        for (freq, reso), row in zip(cases, rows):
            y = [int(v) for v in row.split()]
            drawn = {(21 + x, 56 - min(v, 16)) for x, v in enumerate(y) if v != 0xff}
            self.assertEqual(drawn, qualify.curve_dots(freq << 8, reso << 8), (freq, reso))

    def test_highlighted_row(self):
        screen = np.zeros((64, 128), dtype=np.uint8)
        self.assertIsNone(qualify.highlighted_row(screen))
        screen[32:40, :] = 1
        screen[34, 10:20] = 0
        self.assertEqual(qualify.highlighted_row(screen).shape, (8, 120))
        self.assertEqual(qualify.highlighted_row(screen)[2, 10], 0)

    def test_project_rows(self):
        empty = np.ones((8, 120), dtype=np.uint8)  # highlighted: lit, with the text dark
        empty[1:6, 6:17] = 0  # the slot number
        for y in (2, 3, 4):
            empty[y, 38 - y + 4 * np.arange(13)] = 0
        self.assertTrue(qualify.empty_slot(empty))
        named = empty.copy()
        named[1:6, 30:120] = 1
        named[3, 36:80:3] = 0
        self.assertFalse(qualify.empty_slot(named))
        active = named.copy()
        active[2:5, 33] = 0  # the active project's marker
        self.assertTrue(qualify.same_project(active, named))
        other = named.copy()
        other[3, 10] = 1 - other[3, 10]  # another slot number
        self.assertFalse(qualify.same_project(other, named))
        self.assertTrue(qualify.same_slot(empty, named))
        self.assertFalse(qualify.same_slot(other, named))
        self.assertFalse(qualify.same_project(empty, named))

    def test_label_is_four_characters(self):
        self.assertRegex(qualify.label(), r'^Q[0-9A-Z]{3}$')


if __name__ == '__main__':
    unittest.main()
