/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "mk2/machines.h"

/* Synth and filter IDs use separate namespaces: SINE is synth 6, LOWPASS filter 8. */
const struct mk2_synth sine_machine = {6, "SINE", "SINE"};
