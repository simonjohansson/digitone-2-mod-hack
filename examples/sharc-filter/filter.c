/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Stateless filter proof; half gain is an execution control. */
#ifndef FILTER_GAIN
#define FILTER_GAIN 1.0f
#endif
#include "digitone-mk2/filter.h"
void sharc_filter_render(const struct dn2_filter_input *in)
{
    for (unsigned i = 0; i < 32; ++i)
        in->samples[i] *= FILTER_GAIN;
}
