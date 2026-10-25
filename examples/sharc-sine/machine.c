/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "mk2/machines.h"

/* Synth and filter IDs use separate namespaces; both examples use 6. */
const struct mk2_synth sine_machine = {6, 0, "SINE", "SINE"};

/* One SYN page: knob A is sound slot 25, B is 26; C..H stay empty. */
static const struct mk2_param level = {"LEV", 100}, tune = {"TUNE", 64};
static const struct mk2_page sine_page[1] = {{{&level, &tune}}};
const struct mk2_voice_pages sine_pages = {6, 1, sine_page};
