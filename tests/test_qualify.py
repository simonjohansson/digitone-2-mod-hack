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
        self.assertTrue(qualify.cells_empty(empty))
        self.assertFalse(qualify.labels_shown(empty))
        labelled = empty.copy()
        labelled[60, 30:40] = 1
        self.assertTrue(qualify.labels_shown(labelled))
        knob = empty.copy()
        knob[48, 27:40] = 1
        self.assertFalse(qualify.cells_empty(knob))

    def test_highlighted_row(self):
        screen = np.zeros((64, 128), dtype=np.uint8)
        self.assertIsNone(qualify.highlighted_row(screen))
        screen[32:40, :] = 1
        screen[34, 10:20] = 0
        self.assertEqual(qualify.highlighted_row(screen).shape, (8, 120))
        self.assertEqual(qualify.highlighted_row(screen)[2, 10], 0)

    def test_label_is_four_characters(self):
        self.assertRegex(qualify.label(), r'^Q[0-9A-Z]{3}$')


if __name__ == '__main__':
    unittest.main()
