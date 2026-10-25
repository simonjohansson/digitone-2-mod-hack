/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "digitone-mk2/machines.h"

/* ID 5 belongs to the separate CLONE fixture. Never reuse its saved identity. */
const struct dn2_synth sine_machine = {6, 0, "SINE", "SINE"};
