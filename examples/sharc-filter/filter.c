/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "digitone-mk2/filter.h"
static float history[16] = {0};
void sharc_filter_render(const struct dn2_filter_input *in)
{
    float y = history[in->slot];
    if (in->flags & DN2_FILTER_RESET)
        y = 0.0f;
    for (unsigned i = 0; i < 32; ++i) {
        y += 0.0625f * (in->samples[i] - y);
        in->samples[i] = y;
    }
    history[in->slot] = y;
}
