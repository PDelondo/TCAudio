/* SPDX-License-Identifier: GPL-3.0-or-later
 * Copyright (C) 2026 Pablo Sánchez. Código escrito por Claude (Anthropic). */
/* Decodificador LTC (SMPTE 12M, bifase) propio, sin dependencias: se alimenta muestra a
 * muestra y devuelve 1 cuando completa una trama con su palabra de sincronía. */
#ifndef LTCDEC_H
#define LTCDEC_H
#include <stdint.h>

#ifndef LTCDEC_API
#define LTCDEC_API
#endif

struct ltcdec {
    int16_t  prev;        /* nivel anterior (+1 / -1 / 0), para detectar el cruce */
    uint32_t peak;        /* pico reciente, en 1/256: el umbral sigue al nivel de la señal */
    uint32_t since;       /* muestras desde el último cruce */
    uint32_t half;        /* medio bit estimado, en 1/16 de muestra */
    uint8_t  pending;     /* 1 = visto medio bit corto, falta el segundo */
    uint32_t hi, lo;      /* últimos 64 bits de datos */
    uint32_t sync;        /* últimos 16 bits (palabra de sincronía) */
    uint32_t bits;        /* bits desde la última sincronía */
};

LTCDEC_API void ltcdec_init(struct ltcdec *d, uint32_t samples_per_bit);
/* Devuelve 1 si la muestra `s` completa una trama: *word = TC en el formato del DSP de la
 * fp (BCD: ff | ss<<8 | mm<<16 | hh<<24, bit 6 = drop-frame invertido, bit 31 = válido). */
LTCDEC_API int ltcdec_feed(struct ltcdec *d, int16_t s, uint32_t *word);
#endif
