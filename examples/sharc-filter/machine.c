/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "mk2/machines.h"

/* The FLTR page's graph: LOWPASS's response, roughly. Columns span the FREQ scale
 * (0..127, read like MIDI notes, as the kernel reads the cutoff); rows are 3 dB each,
 * with 0 dB on row 12. Above the cutoff it falls 12 dB an octave; RESO raises a peak
 * at the cutoff, 20 log10(2 / (2 - 1.96 RESO/128)) dB, by RESO/16. */
static void curve(uint32_t freq, uint32_t reso, uint8_t y[51])
{
    static const int peak_db[8] = {0, 1, 2, 4, 6, 8, 12, 17};
    int cutoff = (int)(freq >> 8), peak = peak_db[(reso >> 8) >> 4];
    for (int x = 0; x < 51; ++x) {
        int d = x * 127 / 50 - cutoff;  /* semitones above the cutoff */
        int db = d <= 0 ? (d > -12 ? peak * (12 + d) / 12 : 0) : -d + (d < 12 ? peak * (12 - d) / 12 : 0);
        y[x] = db < -36 ? 0xff : (uint8_t)((db + 36) / 3);
    }
}

/* shortcut: clone as host type, replaced by #83's host type in cleanup */
const struct mk2_filter lowpass = {8, 1, "LOWPASS", "LOWPASS", curve};
