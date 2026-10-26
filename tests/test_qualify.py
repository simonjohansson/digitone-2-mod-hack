# SPDX-License-Identifier: GPL-2.0-or-later
"""The qualification's measurements on synthetic audio and screens."""
import unittest

import numpy as np

import qualify


def tone(hz, seconds=0.8, amplitude=0.01):
    t = np.arange(int(seconds * qualify.RATE)) / qualify.RATE
    silence = np.zeros(qualify.RATE // 5)
    return np.concatenate([silence, amplitude * np.sin(2 * np.pi * hz * t), silence])


def one_pole(x, a=1 / 16):
    y, out = 0.0, np.empty_like(x)
    for i, v in enumerate(x):
        y += a * (v - y)
        out[i] = y
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
        for synth in (qualify.SINE, qualify.FM_TONE):
            low, high = (tone(qualify.pitch_of(synth, n)) for n in (qualify.LOW, qualify.HIGH))
            drop = ((qualify.level_db(one_pole(low)) - qualify.level_db(one_pole(high)))
                    - (qualify.level_db(low) - qualify.level_db(high)))
            expected = (qualify.lowpass_db(qualify.pitch_of(synth, qualify.LOW))
                        - qualify.lowpass_db(qualify.pitch_of(synth, qualify.HIGH)))
            self.assertAlmostEqual(drop, expected, delta=0.2)
        self.assertAlmostEqual(qualify.lowpass_db(0), 0)

    def test_page_regions(self):
        empty = np.zeros((64, 128), dtype=np.uint8)
        empty[41:56:2, 25:125:13] = 1  # dotted outlines only
        self.assertFalse(qualify.labels_shown(empty))
        labelled = empty.copy()
        labelled[60, 30:40] = 1
        self.assertTrue(qualify.labels_shown(labelled))

    def test_page_as_stock_needs_an_empty_graph_and_everything_else_equal(self):
        stock = np.zeros((64, 128), dtype=np.uint8)
        stock[60, 30:40] = 1
        stock[45, 25:60] = 1  # the response curve
        custom = stock.copy()
        custom[qualify.GRAPH] = 0
        self.assertTrue(qualify.page_as_stock(custom, stock))
        self.assertFalse(qualify.page_as_stock(stock, stock))
        self.assertFalse(qualify.page_as_stock(custom, custom))  # no curve on the stock page
        moved = custom.copy()
        moved[60, 41] = 1
        self.assertFalse(qualify.page_as_stock(moved, stock))

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
