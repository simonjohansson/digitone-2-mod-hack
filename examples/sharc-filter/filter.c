/* SPDX-License-Identifier: GPL-2.0-or-later */
/* A resonant 2-pole low-pass or high-pass: a state-variable filter in topology-preserving
 * form, stable at any cutoff, with G (MODE) picking the output. Each slot keeps its two
 * integrator states in its voice state. */
#include "digitone-mk2/filter.h"

struct voice { float ic1, ic2; };
DN2_FILTER_STATE(struct voice);

/* shortcut: this Selache pin keeps a1 only in a register that the sample loop reuses;
 * coefficients in memory survive. Move them back into locals when the compiler is fixed. */
static float a1, a2, a3, k, mode;

void sharc_filter_render(const struct dn2_filter_input *in)
{
    /* The cutoff in Hz, read like a MIDI note: 69/128 is 440 Hz. Held to 20 Hz..16 kHz. */
    float f = in->cutoff * (128.0f / 12.0f) - 5.75f;    /* octaves from 440 Hz */
    float hz = 440.0f;
    while (f < 0.0f) { f += 1.0f; hz *= 0.5f; }
    while (f >= 1.0f) { f -= 1.0f; hz *= 2.0f; }
    hz *= ((0.0782015f * f + 0.2262718f) * f + 0.6951786f) * f + 1.0f;   /* 2^f on 0..1, within 0.01% */
    if (hz < 20.0f) hz = 20.0f;
    if (hz > 16000.0f) hz = 16000.0f;

    /* g = tan(pi Hz / 48000), Pade, within 0.2% up to pi/3. */
    float x = hz * 6.544984e-5f, x2 = x * x;
    float g = x * (15.0f - x2) / (15.0f - 6.0f * x2);

    /* k = 2 - 1.96 RESO: 2 open, near 0 at full resonance. */
    k = 2.0f - 1.96f * in->resonance;
    a1 = 1.0f / (1.0f + g * (g + k));
    a2 = g * a1;
    a3 = g * a2;

    /* MODE 0..63 (g below 0.5) is the low-pass, 64..127 the high-pass. */
    mode = in->g < 0.5f ? 0.0f : 1.0f;
    struct voice *v = in->state;
    if (in->flags & DN2_FILTER_RESET) { v->ic1 = 0.0f; v->ic2 = 0.0f; }
    float ic1 = v->ic1, ic2 = v->ic2;
    for (int i = 0; i < 32; ++i) {
        float s = in->samples[i];
        float v3 = s - ic2;
        float lp = ic2 + a2 * ic1 + a3 * v3;     /* v2: the low-pass */
        float bp = a1 * ic1 + a2 * v3;           /* v1: the band-pass */
        ic1 = 2.0f * bp - ic1;
        ic2 = 2.0f * lp - ic2;
        float hp = s - k * bp - lp;
        in->samples[i] = lp + mode * (hp - lp);
    }
    v->ic1 = ic1;
    v->ic2 = ic2;
}
