// SPDX-License-Identifier: GPL-2.0-or-later
// A resonant 2-pole low-pass: a state-variable filter in topology-preserving form,
// stable at any cutoff. Each slot keeps its own two integrator states. Written in
// assembly so it needs no stack: the adapter keeps every register it uses.
.SECTION/SW seg_swco;
.GLOBAL sharc_filter_render.;

// R4 points at struct dn2_filter_input: samples, slot, flags, cutoff, resonance.
sharc_filter_render.:
    I4=R4;
    R9=DM(0,I4);            // samples
    R10=DM(1,I4);           // slot
    R11=DM(2,I4);           // flags
    R0=DM(3,I4);            // cutoff
    R12=DM(4,I4);           // resonance
    R6=0x40000000;          // 2.0, for the reciprocals and the integrators

    // The cutoff in Hz, read like a MIDI note: 69/128 is 440 Hz. Held to 20 Hz..16 kHz.
    R1=0x412aaaab;          // 128/12
    F0=F0*F1;
    R1=0x40b80000;          // 69/12
    F0=F0-F1;               // octaves from 440 Hz
    R2=0x43dc0000;          // 440.0
    R3=0x3f800000;          // 1.0
    R5=0x3f000000;          // 0.5
below:
    F0=PASS F0;
    IF GE JUMP above;
    F0=F0+F3;
    F2=F2*F5;
    JUMP below;
above:
    COMP(F0,F3);
    IF LT JUMP fraction;
    F0=F0-F3;
    F2=F2*F6;
    JUMP above;
fraction:
    R1=0x3da0281c;          // 2^f on 0..1, within 0.01%
    F7=F0*F1;
    R1=0x3e67b3cb;
    F7=F7+F1;
    F7=F7*F0;
    R1=0x3f31f73a;
    F7=F7+F1;
    F7=F7*F0;
    F7=F7+F3;
    F2=F2*F7;               // Hz
    R1=0x41a00000;          // 20.0
    F2=MAX(F2,F1);
    R1=0x467a0000;          // 16000.0
    F2=MIN(F2,F1);

    // g = tan(pi Hz / 48000), Pade, within 0.2% up to pi/3.
    R1=0x3889421e;          // pi/48000
    F2=F2*F1;               // x
    F7=F2*F2;               // x^2
    R1=0x41700000;          // 15.0
    F8=F1-F7;
    F8=F8*F2;               // x (15 - x^2)
    R0=0xc0c00000;          // -6.0
    F7=F7*F0;
    F7=F7+F1;               // 15 - 6 x^2
    F0=RECIPS F7;
    F1=F0*F7;
    F1=F6-F1;
    F0=F0*F1;
    F1=F0*F7;
    F1=F6-F1;
    F0=F0*F1;
    F2=F8*F0;               // g

    // k = 2 - 1.96 RESO: 2 open, near 0 at full resonance. a1 = 1 / (1 + g (g + k)).
    R1=0x3ffae148;          // 1.96
    F12=F12*F1;
    F12=F6-F12;             // k
    F7=F2+F12;
    F7=F7*F2;
    F7=F7+F3;
    F0=RECIPS F7;
    F1=F0*F7;
    F1=F6-F1;
    F0=F0*F1;
    F1=F0*F7;
    F1=F6-F1;
    F3=F0*F1;               // a1
    F5=F2*F3;               // a2
    F8=F2*F5;               // a3

    // This slot's integrators, cleared on RESET.
    R1=LSHIFT R10 BY 2;
    R10=s1.;
    R10=R10+R1;
    R11=s2.;
    R11=R11+R1;
    I4=R10;
    R13=DM(I4,M5);          // ic1
    I4=R11;
    R14=DM(I4,M5);          // ic2
    I4=R4;
    R1=DM(2,I4);
    R0=1;
    R1=R1 AND R0;
    IF EQ JUMP run;
    R13=0;
    R14=0;
run:
    R15=32;
sample:
    I4=R9;
    R1=DM(I4,M5);
    F1=F1-F14;              // v3 = x - ic2
    F2=F5*F13;
    F2=F2+F14;
    F7=F8*F1;
    F2=F2+F7;               // v2 = ic2 + a2 ic1 + a3 v3
    F7=F3*F13;
    F0=F5*F1;
    F7=F7+F0;               // v1 = a1 ic1 + a2 v3
    F7=F7*F6;
    F13=F7-F13;             // ic1 = 2 v1 - ic1
    F7=F2*F6;
    F14=F7-F14;             // ic2 = 2 v2 - ic2
    DM(I4,M5)=R2;           // the low-pass output
    R0=4;
    R9=R9+R0;
    R15=R15-1;
    IF NE JUMP sample;
    I4=R10;
    DM(I4,M5)=R13;
    I4=R11;
    DM(I4,M5)=R14;
    I12=DM(M7,I6);
    JUMP(M14,I12) (DB);
    RFRAME;
    NOP;

.SECTION/DOUBLE32 seg_dmda;
.VAR s1. = 0;
.VAR = 0;
.VAR = 0;
.VAR = 0;
.VAR = 0;
.VAR = 0;
.VAR = 0;
.VAR = 0;
.VAR = 0;
.VAR = 0;
.VAR = 0;
.VAR = 0;
.VAR = 0;
.VAR = 0;
.VAR = 0;
.VAR = 0;
.VAR s2. = 0;
.VAR = 0;
.VAR = 0;
.VAR = 0;
.VAR = 0;
.VAR = 0;
.VAR = 0;
.VAR = 0;
.VAR = 0;
.VAR = 0;
.VAR = 0;
.VAR = 0;
.VAR = 0;
.VAR = 0;
.VAR = 0;
.VAR = 0;
