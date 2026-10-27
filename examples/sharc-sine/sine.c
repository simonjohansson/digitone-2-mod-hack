/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Per-voice oscillator; the stock engine supplies the envelope and filter. */
#include "digitone-mk2/voice.h"

struct voice { uint32_t phase; };
DN2_VOICE_STATE(struct voice);

/* Positive half of 0.125 * sin(2*pi*i/64), rounded to binary32.
 * shortcut: this Selache pin miscompiles negative float initializers;
 * use runtime negation until that compiler path is qualified. */
static const float table[32] = {
    0.000000000f, 0.012252143f, 0.024386290f, 0.036285585f,
    0.047835429f, 0.058924592f, 0.069446279f, 0.079299160f,
    0.088388348f, 0.096626307f, 0.103933702f, 0.110240158f,
    0.115484942f, 0.119617542f, 0.122598160f, 0.124398091f,
    0.125000000f, 0.124398091f, 0.122598160f, 0.119617542f,
    0.115484942f, 0.110240158f, 0.103933702f, 0.096626307f,
    0.088388348f, 0.079299160f, 0.069446279f, 0.058924592f,
    0.047835429f, 0.036285585f, 0.024386290f, 0.012252143f
};

/* Notes 36..47; higher octaves double the phase step. */
static const uint32_t steps12[12] = {
    5852465u, 6200470u, 6569170u, 6959793u,
    7373644u, 7812103u, 8276635u, 8768789u,
    9290209u, 9842633u, 10427907u, 11047982u
};

/* Knobs (machine.c): param[0] LEV, [1] TUNE, [8] OCT, [12] FINE, unsigned 8.8.
 * TUNE moves the note in semitones around 64; OCT in octaves, one per 16
 * around 64..79. FINE is not used yet. Notes outside 36..84 are silent. */
void sharc_voice_render(const struct dn2_voice_input *in)
{
    struct voice *v = in->state;
    uint32_t p = in->flags & DN2_VOICE_START ? 0 : v->phase;
    uint32_t note = (in->pitch >> 8) + (in->param[1] >> 8) + 12 * (in->param[8] >> 12) - 112;
    float gain = (float)(int32_t)(in->param[0] >> 8) * (1.0f / 127.0f);
    uint32_t step = 0;
    if (note >= 36 && note <= 84) {
        uint32_t k = note - 36, octave = 0;
        while (k >= 12) { k -= 12; ++octave; }
        step = steps12[k] << octave;
    }
    for (int i = 0; i < 32; ++i) {
        uint32_t index = p >> 26;
        float value = table[index & 31];
        if (index & 32) value = -value;
        if (!step) value = 0.0f;
        in->output[i] = value * gain;
        /* Preserve this example's documented octave-up tuning. */
        p += step + step;
    }
    v->phase = p;
}
