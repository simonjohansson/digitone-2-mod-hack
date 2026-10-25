/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "mk2/machines.h"

/* Synth and filter IDs use separate namespaces; both examples use 6. */
const struct mk2_synth sine_machine = {6, 0, "SINE", "SINE"};

/* SYN1: knob A is sound slot 25, B is 26. SYN2: A is slot 33, E is 37.
 * The other knobs stay empty. */
static const struct mk2_param level = {"LEV", 100}, tune = {"TUNE", 64};
static const struct mk2_param octave = {"OCT", 64}, fine = {"FINE", 64};
static const struct mk2_page sine_page[2] = {{{&level, &tune}},
                                             {{&octave, 0, 0, 0, &fine}}};
const struct mk2_voice_pages sine_pages = {6, 2, sine_page};
