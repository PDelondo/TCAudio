/* SPDX-License-Identifier: GPL-3.0-or-later
 * Copyright (C) 2026 Pablo Sánchez. Código escrito por Claude (Anthropic). */
#include "ltcdec.h"

#define SYNC_WORD 0xBFFCu       /* 0011 1111 1111 1101 tal como entra, bit 64 primero */
#define HYST_MIN  32            /* histéresis mínima: el ruido de fondo nunca es un cruce */
#define PEAK_FALL 11            /* caída del pico: unos 40 ms, más que una trama no hace falta */
#define HALF_MIN  (160u - 16u)  /* medio bit, 1/16 de muestra: 30 fps .. 23,976 fps */
#define HALF_MAX  (200u + 16u)

LTCDEC_API void ltcdec_init(struct ltcdec *d, uint32_t samples_per_bit)
{
    d->prev = 0;
    d->peak = 0;
    d->since = 0;
    d->half = samples_per_bit * 8;  /* samples_per_bit / 2, en 1/16 de muestra */
    d->pending = 0;
    d->hi = d->lo = 0;
    d->sync = 0;
    d->bits = 0;
}

static int push_bit(struct ltcdec *d, uint32_t bit, uint32_t *word)
{
    uint32_t lo = d->lo, hi = d->hi, w;

    /* los 64 bits de datos entran por el extremo alto de hi:lo, LSB primero */
    d->lo = (lo >> 1) | (hi << 31);
    d->hi = (hi >> 1) | ((d->sync & 1u) << 31);
    d->sync = ((d->sync >> 1) | (bit << 15)) & 0xFFFFu;
    if (d->bits < 255)
        d->bits++;
    if (d->sync != SYNC_WORD)
        return 0;
    w = d->bits;
    d->bits = 0;
    if (w != 80)                            /* la trama solo vale entre dos sincronías */
        return 0;
    lo = d->lo;
    hi = d->hi;
    w  = (lo & 0xFu) | ((lo >> 4) & 0x30u);                     /* ff */
    if (!(lo & (1u << 10)))                                     /* drop-frame (bit 10) */
        w |= 0x40u;
    w |= ((lo >> 16) & 0xFu) << 8 | ((lo >> 24) & 0x7u) << 12;  /* ss */
    w |= (hi & 0xFu) << 16 | ((hi >> 8) & 0x7u) << 20;          /* mm */
    w |= ((hi >> 16) & 0xFu) << 24 | ((hi >> 24) & 0x3u) << 28; /* hh */
    *word = w | 0x80000000u;
    return 1;
}

static void adapt(struct ltcdec *d, uint32_t t)
{
    if (t < HALF_MIN) t = HALF_MIN;                     /* el ruido no puede arrastrarlo */
    if (t > HALF_MAX) t = HALF_MAX;
    d->half += ((int32_t)t - (int32_t)d->half) >> 5;
}

LTCDEC_API int ltcdec_feed(struct ltcdec *d, int16_t s, uint32_t *word)
{
    uint32_t mag = (uint32_t)(s < 0 ? -(int32_t)s : s) << 8, hyst;
    int16_t level;
    int crossed;
    uint32_t t, limit;

    d->peak -= d->peak >> PEAK_FALL;
    if (mag > d->peak)
        d->peak = mag;
    hyst = d->peak >> 10;                               /* la cuarta parte del pico */
    if (hyst < HYST_MIN)
        hyst = HYST_MIN;
    level = s > (int32_t)hyst ? 1 : s < -(int32_t)hyst ? -1 : d->prev;
    crossed = level != d->prev;

    d->prev = level;
    d->since++;
    if (!crossed)
        return 0;
    t = d->since << 4;
    d->since = 0;
    limit = d->half + (d->half >> 1);                   /* frontera corto/largo: 1,5 medios */
    if (t < HALF_MIN >> 1 || t > HALF_MAX * 4)          /* ruido o hueco: empieza de nuevo */
        { d->pending = 0; return 0; }
    if (t < limit) {                                    /* medio bit */
        adapt(d, t);
        if (d->pending) {
            d->pending = 0;
            return push_bit(d, 1, word);
        }
        d->pending = 1;
        return 0;
    }
    adapt(d, t >> 1);                                   /* bit entero */
    d->pending = 0;
    return push_bit(d, 0, word);
}
