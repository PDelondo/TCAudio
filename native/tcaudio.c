/* SPDX-License-Identifier: GPL-3.0-or-later
 * Copyright (C) 2026 Pablo Sánchez. Código escrito por Claude (Anthropic). */
/* TCAudio: TC-IN y audio a la vez en la SIGMA fp (sup de Loader v4, firmware 5.02).
 *
 * Con TC-IN activo la cámara arranca el DSP de audio con el bit 0x800 del registro 0x1C: el
 * DSP decodifica el LTC y deja de entregar audio. Este sup:
 *   1. quita la regla "TC externo -> sin audio" (0xC00960FC, como 60TCAU);
 *   2. saca de gris "Grabación de audio" (0xC04A152C) y usa ese ajuste: ON, el DSP arranca sin el
 *      bit y llegan las muestras a la CPU; OFF, TC-IN de serie;
 *   3. sustituye c00d6ec0, que la tarea del driver llama una vez por bloque, por tca_tick:
 *      decodifica el LTC del canal donde aparezca, en el anillo de muestras, y deja la palabra en el
 *      buzón que antes llenaba el DSP (0x2AA01200), de donde la cámara lee el TC.
 *   4. copia el audio sobre el canal del LTC en el anillo, ya decodificado, antes
 *      de que llegue al fichero; si los dos canales llevan LTC no copia nada.
 * Todo en RAM: el cargador devuelve las palabras originales al apagar.
 * Direcciones y pruebas: docs/TECHNICAL.md. */
#include "sloader4.h"
#include "fpui_stock.c"   /* fpSup/fpsup-ui: filas en la página de audio original */
#define LTCDEC_API static
#include "ltcdec.c"

typedef uint32_t u32;
typedef int16_t s16;

#define R32(a)        (*(volatile u32 *)(uintptr_t)(a))

#define SITE_RULE     0xC00960FCu       /* mov r3, #0 -> mov r3, r6 */
#define RULE_STOCK    0xE3A03000u
#define RULE_NEW      0xE1A03006u
#define SITE_GREY     0xC04A152Cu       /* bne -> b: "Grabación de audio" no sale en gris con TC-IN */
#define GREY_STOCK    0x1A000001u
#define GREY_NEW      0xEA000001u
#define SITE_JAM      0xC00CEB94u       /* mov r3, #2 -> #1: reajusta el TC con 1 fotograma de error */
#define JAM_STOCK     0xE3A03002u
#define JAM_NEW       0xE3A03001u
#define SITE_MIC_A    0xC00DA154u       /* movs r1, #2 -> #0x24: entrada del códec, en c00da128 */
#define MIC_A_WORD    0x2100D10Du       /* más el valor, << 16 */
#define SITE_MIC_B    0xC00DAA18u       /* lo mismo en c00da888, que corre al pulsar REC */
#define MIC_B_WORD    0x20052100u       /* más el valor */
#define IN_JACK       0x02u             /* registro 5: jack directo, de serie */
#define IN_MIC_R      0x28u             /* micros a la izquierda, jack derecho a la derecha */
#define IN_MIC_L      0x29u             /* micros a la izquierda, jack izquierdo cruzado. Por la rama INT, que deja
                                         * el registro 10 sin el bit 0x20: el jack derecho no entra (Deity A-OUT) */
#define IN_MICS       0x24u             /* estéreo de los micros: sigue la rama INT, que además limpia el
                                         * bit 0x20 del registro 10, el que deja entrar el jack derecho */
#define MIC_A_SKIP    0x2102E00Du       /* bne -> b: hacia la rama INT de c00da128 */
#define SITE_INT_A    0xC00DA1B0u       /* media palabra alta: movs r1, #0x24 de la rama INT de c00da128 */
#define SITE_INT_B    0xC00DAA74u       /* media palabra baja: lo mismo en c00da888 */
#define INT_MOVS      0x2100u           /* movs r1, #valor */
#define SITE_MIC_C    0xC00DAA14u       /* cmp r0, #1; bne en c00da888 */
#define MIC_C_STOCK   0xD10D2801u
#define MIC_C_SKIP    0xE00D2801u
#define MIC_MANAGER   0xC31F7D84u       /* +0: 0 micro interno, 1 jack */
#define F_SETMIC      0xC01FA178u       /* SetMic(gestor, modo), ARM: 0 interno, 1 jack */
/* Micro y mando por el mismo jack. El detector del jack (0xC3425BCC, FUN_c02e3a48) lee
 * E3 (+4, clavija dentro), E2 (+8, tipo de clavija) y E1 (+0xC, línea del mando). Con la caja, E2 dice
 * «mando»: la cámara nunca da el micro por presente y graba los micros internos. TCAudio pone a 1 el
 * byte de forzado +0x11 del detector («micro presente»), que no toca la lectura de los pulsos (+0x10),
 * y le pide a la cámara que reevalúe el jack con su propia función. Comprobado en cámara el 15-11. */
#define DET_FORCE     0xC3425BDCu       /* detector del jack 0xC3425BCC + 0x10: forzados +0x10/+0x11/+0x12 */
#define DET_FORCE_MIC 0x0000FF00u       /* +0x11: «micro presente» */
#define STATE_MIC     0xC303384Cu       /* *(c0017ac8(c00178d8())) byte 0: micro en el jack */
#define STATE_REMOTE  0xC3033850u       /* byte 4: el mando está activo (c02e3508) */
#define F_JACK_EVAL   0xC02E3E58u       /* (obj), ARM: la reevaluación del jack de la tarea RemoDev */
#define REMODEV_JACK  0xC3425B50u       /* RemoDev 0xC3425B3C + 0x14: {detector, objeto de eventos} */
#define DET_E3        0xC3425BD0u       /* detector + 4: dispositivo HAL 0xE3 (0 = clavija dentro) */
#define EVAL_TICKS    48u               /* ~1 s entre comprobaciones */
#define F_DCACHE      0xC000E91Cu
#define F_ICACHE      0xC000EABCu
/* Las filas. Registro de la UI: fpSup/screenflip/native/flip.c (Bei). */
#define GUI_OBJECT    0xC37B7048u
#define APP_REGISTRY  0x888u
#define REGISTRY_OFF  0x60u
#define F_UI_LOOKUP   0xC05DB419u       /* lookup(registro, nombre, &descriptor), Thumb */
#define F_UI_REGISTER 0xC05DB309u       /* register(app, cuenta, {tipo, nombre, valor}), Thumb */
#define F_UI_SUBSCRIBE 0xC0560FB0u      /* subscribe(objeto GUI, nombre, fn) */
#define F_UI_POST     0xC0593F30u       /* (0, nombre, valor): a la cola de la interfaz */
#define DSP_START_LR  0xC00D7B67u       /* c00d7b62: el arranque del DSP mira el retorno de c00d6ec0 */
#define SITE_TICK     0xC00D6EC0u       /* ldr.w pc, [pc, #0]; dirección de tca_tick */
#define TICK_STOCK0   0x8D04F84Du
#define TICK_STOCK1   0x71DCF241u
#define TICK_NEW0     0xF000F8DFu

#define TC_ENABLED    0xC31D17DCu       /* lo que devolvía c00d6ec0 */
#define DSP_BASE      0x2AA01244u       /* bloque de control del DSP */
#define DSP_WPTR      0x74u             /* puntero de escritura del anillo */
#define DSP_MODE      0x1Cu             /* bit 0x800: el DSP decodifica LTC y no entrega audio */
#define DSP_LTC_BIT   0x800u
#define SETTINGS      0xC3075230u       /* puntero al contexto de ajustes */
#define AUDIO_ON      0xE0u             /* byte: grabación de audio efectiva */
#define AUDIO_ITEM_VALUE 0xC31ACC7Eu    /* objeto del ajuste (ajustes + 0x6D0) + 0x12: valor del usuario */
#define AUDIO_BANK    0xC31B954Cu       /* qué juego de ajustes está activo: c00897e8 */
#define AUDIO_RAW_A   0xC31B3A80u       /* el valor guardado del usuario, id 0x6C: + 0x7C4 */
#define AUDIO_RAW_B   0xC31B9348u
#define EXCL_MGR      0xC3766AECu       /* gestor de exclusiones */
#define EXCL_CATEGORY 0x24u
#define EXCL_CACHE    0xC3768BB4u
#define F_UI_GET      0xC054DC80u
#define F_EXCL_NOTIFY 0xC049EF18u
#define SETTLE_TICKS  240u              /* ~5 s de bloques */
#define B8(a)         (*(volatile uint8_t *)(uintptr_t)(a))
#define MBOX_TC       0x2AA01200u       /* buzón del TC */
#define METER_CB      0xC31CC81Cu       /* retrollamada de vúmetros: c01ff2b0(dB izq, dB der) */
#define RING_LO       0xC31D23F8u
#define RING_HI       0xC31D23F0u

#define BIT_SAMPLES   25u               /* 48000 / (80 × 24) */
#define MAX_BYTES     0x8000u           /* por llamada: lo que falte se salta */
#define IDLE_TICKS    24u               /* ~0,5 s sin tramas: suelta el TC */
#define CLONE_RECENT  64u               /* ~1,4 s sin LTC en su canal: ya no se clona, sin TC no hay qué tapar */
#define CLONE_QUIET   128u              /* ~2,7 s sin LTC en el otro canal: se puede clonar */
#define SWITCH_TICKS  8u                /* ~0,2 s callado: otro canal puede tomar el relevo */
#define FPS           24u               /* hasta medir la señal: 23,976 y 24 cuentan hasta 24 */
#define CAM_RATE      0xC31CC408u       /* leído en cámara: 23,98 = 1; 24, 48 = 2; 50 = 3; 29,97, 59,94 = 4 */
#define SPAN_2001     (2001u << 8)      /* media de muestras por trama, en 1/256 */
#define SPAN_1601     ((1601u << 8) - 51u)  /* 1600,8 */
#define SETTLE_FRAMES 16u               /* tramas para que la media sirva */
#define SPAN_MIN      1500u             /* muestras por trama admisibles: 32 .. 22,8 fps */
#define SPAN_MAX      2100u
#define SPAN_30       1700u             /* por debajo, 29,97 o 30 */
#define SPAN_25       1960u             /* por debajo, 25 */
#define BURST         6u                /* muestras por ráfaga: seis registros libres */
#define OTHER_PERIOD  0x3Fu             /* el otro canal, con el micro o en Internal: 6 de cada 64 */
#define OTHER_BLOCKS  6u
#define FRAME_SAMPLES 2002u             /* muestras por fotograma a 23,976 fps, hasta medirlas */

struct tca {
    u32 magic;                          /* 'TCA1' */
    u32 ticks, blocks, frames, idle;    /* contadores, para leerlos por consola */
    u32 lo, hi, pos, word;
    u32 count, raw, raw_at, have;       /* muestras leídas; última trama y dónde acabó */
    u32 fixed;                          /* ya se arregló lo que la cámara hizo antes de cargarnos */
    u32 notified;                       /* ya se avisó al gestor de exclusiones */
    u32 channel;                        /* canal del LTC: 0 izquierdo, 1 derecho */
    u32 known;                          /* ya se ha visto LTC: `channel` vale */
    u32 seen_at[2];                     /* `ticks` de la última trama de cada canal */
    u32 last[2];                        /* última trama de cada canal, sana o no */
    u32 sync_at[2];                     /* `count` de esa trama */
    u32 span[2];                        /* muestras por trama medidas */
    u32 avg[2];                         /* su media, en 1/256 */
    u32 spans[2];                       /* cuántas van medidas */
    u32 fps[2];                         /* hasta cuánto cuentan sus fotogramas */
    u32 cloned;                         /* muestras copiadas */
    u32 cloning;                        /* el último bloque se clonó */
    u32 mic;                            /* el canal izquierdo lleva el micro interno: se clona */
    u32 input;                          /* valor del registro 5 en los sitios */
    u32 forced;                         /* fila MIC-IN: 0 Auto, 1 Internal */
    u32 mix;                            /* fila INT+EXT: micros y jack, cada uno en su canal */
    u32 source;                         /* canal del jack donde se vio el TC */
    u32 both;                           /* Auto: LTC por los dos canales */
    u32 mic_name[3], mix_name[3];       /* MV_fpMicInt, MV_fpMicMix */
    u32 locked;                         /* último TC-IN avisado a la fila, + 1 */
    u32 row_ok;                         /* la fila Int+Ext Mics está puesta */
    u32 int_ok;                         /* los sitios de la rama INT son los de serie */
    u32 int_a, int_b;                   /* sus palabras de serie */
    u32 apply_name[3];                  /* MV_fpMicApl: pide a la UI aplicar la entrada */
    u32 apply_ok;                       /* la variable existe y está suscrita */
    u32 pending;                        /* la entrada cambió y aún no se ha aplicado */
    u32 apply_seq;
    u32 force_mic;                      /* el byte +0x11 del detector está a 1 */
    u32 latch, eval, eval_at;           /* caja vista desde que entró la clavija; reevaluar; último tick */
    const struct sl_svc *svc;
    u32 block;
    char path[64];
    uint8_t fobj[0x800] __attribute__((aligned(64)));   /* objeto de fichero + lectura de vuelta */
    u32 meter;                          /* retrollamada de vúmetros de serie */
    struct ltcdec dec[2];
};

__asm__(".text\n.balign 4\ntca_ctx:\n    .word 0\n"
        "tca_ui:\n    .word 0x4D4F4455, 0, 0, 0\n");
extern const u32 tca_ctx;
extern const u32 tca_ui[4];             /* las filas, tras el código: build_tcaudio.py rellena esto */

/* Un campo BCD + 1, con acarreo al llegar a `limit`. */
static u32 bcd_inc(u32 v, u32 mask, u32 limit, u32 *carry)
{
    u32 n = (v & 0xFu) + ((v >> 4) & mask) * 10u + 1u;
    *carry = n >= limit;
    if (*carry) n = 0;
    return (n % 10u) | ((n / 10u) << 4);
}

/* La palabra del DSP, un fotograma más adelante. */
static u32 next_frame(u32 w, u32 fps)
{
    u32 c, ff = bcd_inc(w, 3u, fps, &c), ss = (w >> 8) & 0x7Fu, mm = (w >> 16) & 0x7Fu;
    u32 hh = (w >> 24) & 0x3Fu;

    if (c) ss = bcd_inc(ss, 7u, 60u, &c);
    if (c) mm = bcd_inc(mm, 7u, 60u, &c);
    if (c) hh = bcd_inc(hh, 3u, 24u, &c);
    return (w & 0xC0000040u) | ff | ss << 8 | mm << 16 | hh << 24;
}

static int audio_on(void);
static int recording(void);

/* ¿Hay clavija? El dispositivo 0xE3 del detector, leído como lo lee c02e3c28 (método +0xC). */
static u32 jack_in(void)
{
    uintptr_t dev = R32(DET_E3);

    if (!dev || (dev & 3u) || !R32(dev))
        return 1u;
    return ((u32 (*)(uintptr_t))R32(R32(dev) + 0xCu))(dev) == 0u;
}

/* ¿La señal va a la velocidad de la cámara? La media de las tramas separa 2002 de 2000 muestras
 * (23,98 de 24) y 1601,6 de 1600 (29,97 de 30), que una trama suelta no distingue. */
static u32 rate_matches(u32 fps, u32 avg)
{
    u32 i = R32(CAM_RATE);

    if (i == 1u) return fps == 24u && avg >= SPAN_2001;
    if (i == 2u) return fps == 24u && avg < SPAN_2001;
    if (i == 3u) return fps == 25u;
    if (i == 4u) return fps == 30u && avg >= SPAN_1601;
    return 1;
}

static void feed(struct tca *t, u32 c, s16 s)
{
    u32 w;

    /* el canal lo decide la sincronía; con LTC en los dos se queda en el actual */
    if (ltcdec_feed(&t->dec[c], s, &w)) {
        u32 span = t->count - t->sync_at[c], good;

        t->sync_at[c] = t->count;
        if (span >= SPAN_MIN && span <= SPAN_MAX) {     /* la velocidad la dice la propia señal */
            t->span[c] = span;
            if (t->spans[c] == 0u)
                t->avg[c] = span << 8;
            t->avg[c] += (int32_t)((span << 8) - t->avg[c]) >> 4;
            if (t->spans[c] < SETTLE_FRAMES)
                t->spans[c]++;
            t->fps[c] = span < SPAN_30 ? 30u : span < SPAN_25 ? 25u : FPS;
        }
        good = w == next_frame(t->last[c], t->fps[c]);  /* una trama suelta puede venir rota */
        t->last[c] = w;
        t->seen_at[c] = t->ticks;
        if (!good)
            return;
        if (t->spans[c] >= SETTLE_FRAMES && !rate_matches(t->fps[c], t->avg[c]))
            return;                                     /* otra velocidad: no engancha, como de serie */
        if (c == t->channel || t->idle > SWITCH_TICKS) {
            t->channel = c;
            t->known = 1;
            t->raw = w;
            t->raw_at = t->count;
            t->have = 1;
            t->frames++;
            t->idle = 0;
        }
    }
}

static inline void burst_read(u32 a, u32 *out)
{
    __asm__ volatile("ldmia %0, {r3-r6, r8, r10}\n stmia %1, {r3-r6, r8, r10}\n" : : "r"(a), "r"(out)
                     : "r3", "r4", "r5", "r6", "r8", "r10", "memory");
}

static inline void burst_write(u32 a, const u32 *in)
{
    __asm__ volatile("ldmia %1, {r3-r6, r8, r10}\n stmia %0, {r3-r6, r8, r10}\n" : : "r"(a), "r"(in)
                     : "r3", "r4", "r5", "r6", "r8", "r10", "memory");
}

static void take(struct tca *t, u32 from, u32 to)
{
    /* solo mientras llegue LTC; si el otro canal también lo lleva no hay audio que copiar */
    u32 clone = audio_on() && (t->mic || (t->known && t->ticks - t->seen_at[t->channel] <= CLONE_RECENT &&
                                          t->ticks - t->seen_at[t->channel ^ 1u] > CLONE_QUIET));

    t->cloning = clone;

    /* el anillo no pasa por caché: se lee y se escribe a ráfagas de BURST muestras, que ocupan el
     * bus mucho menos que una a una. En Auto con el jack se decodifican siempre los dos canales:
     * a ratos, una ventana corta puede no completar ninguna trama y el LTC por los dos canales
     * no se detecta nunca. Con el micro puesto o en Internal, el otro canal solo a ratos. */
    u32 both = !t->known || (!t->forced && t->input == IN_JACK) || (t->ticks & OTHER_PERIOD) < OTHER_BLOCKS;
    u32 buf[BURST];

    for (u32 a = from; a < to; a += BURST * 4u) {
        u32 n = (to - a) / 4u;

        if (n >= BURST) {
            n = BURST;
            burst_read(a, buf);
        } else {
            for (u32 i = 0; i < n; i++)
                buf[i] = R32(a + i * 4u);
        }
        for (u32 i = 0; i < n; i++) {
            u32 pair = buf[i], w;

            t->count++;
            if (both || t->channel == 0u) feed(t, 0u, (s16)(pair & 0xFFFFu));
            if (both || t->channel == 1u) feed(t, 1u, (s16)(pair >> 16));
            if (clone)
                buf[i] = t->channel ? (pair & 0xFFFFu) | (pair << 16)
                                    : (pair & 0xFFFF0000u) | (pair >> 16);
            (void)w;
        }
        if (clone) {
            if (n == BURST) {
                burst_write(a, buf);
            } else {
                for (u32 i = 0; i < n; i++)
                    R32(a + i * 4u) = buf[i];
            }
            t->cloned += n;
        }
    }
}

static void pump(struct tca *t)
{
    u32 base = R32(DSP_BASE), lo = R32(RING_LO), hi = R32(RING_HI), cur;

    if ((base >> 28) != 5u || lo >= hi || (lo & 3u) || (hi & 3u))
        return;
    cur = R32(base + DSP_WPTR) & ~3u;
    if (cur < lo || cur >= hi)
        return;
    if (lo != t->lo || hi != t->hi) {           /* anillo nuevo: el DSP ha arrancado */
        t->lo = lo;
        t->hi = hi;
        t->pos = lo;                            /* desde el principio: lo primero también se copia */
        ltcdec_init(&t->dec[0], BIT_SAMPLES);
        ltcdec_init(&t->dec[1], BIT_SAMPLES);
    }
    if (cur == t->pos)
        return;
    t->blocks++;
    if (((cur - t->pos) & 0x7FFFFFFFu) > MAX_BYTES && cur > t->pos)
        t->pos = cur - MAX_BYTES;
    if (cur > t->pos) {
        take(t, t->pos, cur);
    } else {
        take(t, t->pos, hi);
        take(t, lo, cur);
    }
    t->pos = cur;
    if (++t->idle >= IDLE_TICKS) {
        t->have = 0;
        R32(MBOX_TC) = 0;
    } else if (t->have) {
        /* la palabra del fotograma de ahora, no del último que terminó: redondeada */
        u32 w = t->raw, c = t->channel, span = t->span[c], left = t->count - t->raw_at + span / 2u;

        for (; left >= span; left -= span)
            w = next_frame(w, t->fps[c]);
        t->word = w;
        R32(MBOX_TC) = w;
    }
}

/* El ajuste de audio y la exclusión se calcularon antes de cargar este sup, con la regla de serie.
 * El valor guardado del usuario se lee en la tabla de ajustes, sin llamar a ninguna función: la copia
 * del objeto del ajuste (+0x12) sale a 0 en un arranque en frío con TC-IN activo.
 * Unos segundos después se avisa al gestor de exclusiones, como habría hecho él: la fila sale de gris. */
static void refresh_settings(struct tca *t)
{
    u32 ctx = R32(SETTINGS), up;

    if (ctx < 0xC0000000u)
        return;
    if (!t->fixed) {
        u32 raw = B8(B8(AUDIO_BANK) ? AUDIO_RAW_B : AUDIO_RAW_A) != 0;

        t->fixed = 1;
        B8(AUDIO_ITEM_VALUE) = (uint8_t)raw;
        B8(ctx + AUDIO_ON) = (uint8_t)raw;
    }
    if (!t->notified && t->ticks >= SETTLE_TICKS) {
        t->notified = 1;
        if (R32(EXCL_CACHE) == 0u) {
            R32(EXCL_CACHE) = 1;
            up = ((u32 (*)(u32, u32))F_UI_GET)(2, 0);
            up = ((u32 (*)(u32))R32(R32(up) + 0x18u))(up);
            ((void (*)(u32, u32, u32, u32))F_EXCL_NOTIFY)(EXCL_MGR, up, EXCL_CATEGORY, 1);
        }
    }
}

/* Con audio, el bit del LTC se quita en caliente (el DSP arrancó antes de cargar este sup). Ponerlo en
 * caliente no sirve: el DSP no vuelve a leer el LTC (31-10). Tras pasar a OFF sin reiniciar el DSP, el
 * módulo sigue leyendo el TC hasta el próximo arranque del DSP, que ya lo hará él. Devuelve si hay que leer. */
static int sync_dsp(int audio)
{
    u32 base = R32(DSP_BASE), mode;

    if ((base >> 28) != 5u)
        return 0;
    mode = R32(base + DSP_MODE);
    if (audio && (mode & DSP_LTC_BIT))
        R32(base + DSP_MODE) = mode & ~DSP_LTC_BIT;
    return audio || !(mode & DSP_LTC_BIT);
}

/* ctx+0xE0 cambia al salir del menú. */
static int audio_on(void)
{
    u32 ctx = R32(SETTINGS);

    return ctx >= 0xC0000000u && B8(ctx + AUDIO_ON);
}

/* El registro 5 del códec decide qué entra en cada canal, y la cámara lo escribe al grabar y al
 * salir de los menús: basta cambiar el valor en sus dos sitios. Con el jack, IN_MIC_R deja el jack
 * derecho en su canal; IN_MIC_L cruza el izquierdo pero suma el derecho, que debe llegar vacío.
 * Auto: el micro solo si hay LTC por los dos canales. Internal: siempre. INT+EXT, micros y jack
 * cada uno en su canal, solo sin TC-IN: con TC ese canal es del LTC, y la fila no hace nada. */
static u32 wanted_input(struct tca *t, u32 on, u32 audio)
{
    u32 r = t->ticks - t->seen_at[1], l = t->ticks - t->seen_at[0];

    if (t->input == IN_JACK && t->known)
        t->source = t->channel;
    if (!on)
        return t->mix ? IN_MIC_R : t->forced ? IN_MICS : IN_JACK;
    if (!audio)
        return IN_JACK;
    if (t->forced)
        return t->known && !t->source ? IN_MIC_L : IN_MIC_R;
    if (t->both) {                              /* se mantiene hasta perder el LTC */
        if (r <= CLONE_RECENT)
            return IN_MIC_R;
        t->both = 0;
    }
    if (t->input == IN_JACK && t->spans[0] && t->spans[1] && l <= CLONE_QUIET && r <= CLONE_RECENT) {
        t->both = 1;
        return IN_MIC_R;
    }
    return IN_JACK;
}

/* Devuelve si la entrada ha cambiado: hay que aplicarla. IN_MICS e IN_MIC_L van por la rama INT,
 * cambiando su constante; si sus sitios no son los de serie, IN_MIC_L sigue por la rama del jack. */
static u32 select_input(struct tca *t, u32 on, u32 audio)
{
    u32 want = wanted_input(t, on, audio), via_int;

    t->mic = on && want != IN_JACK && R32(MIC_MANAGER);
    if (want == t->input)
        return 0;
    t->input = want;
    if (on && want != IN_JACK)
        t->channel = 1u;
    via_int = want == IN_MICS || (want == IN_MIC_L && t->int_ok);
    R32(SITE_MIC_A) = via_int ? MIC_A_SKIP : MIC_A_WORD | want << 16;
    R32(SITE_MIC_B) = MIC_B_WORD | (via_int ? IN_JACK : want);
    R32(SITE_MIC_C) = via_int ? MIC_C_SKIP : MIC_C_STOCK;
    if (t->int_ok) {
        u32 v = INT_MOVS | (via_int ? want : IN_MICS);

        R32(SITE_INT_A) = (t->int_a & 0xFFFFu) | v << 16;
        R32(SITE_INT_B) = (t->int_b & 0xFFFF0000u) | v;
    }
    ((void (*)(void))F_DCACHE)();
    ((void (*)(void))F_ICACHE)();
    return 1;
}

/* El DSP mide los picos antes de clonar: el vúmetro del TC muestra el del audio. */
static void tca_meter(int left, int right)
{
    struct tca *t = (struct tca *)(uintptr_t)tca_ctx;

    if (t->cloning) {
        if (t->channel)
            right = left;
        else
            left = right;
    }
    ((void (*)(int, int))t->meter)(left, right);
}

static u32 tca_tick(void)
{
    u32 lr = (u32)(uintptr_t)__builtin_return_address(0), on = R32(TC_ENABLED);
    struct tca *t = (struct tca *)(uintptr_t)tca_ctx;

    if (lr == DSP_START_LR)                     /* ¿el DSP lee el LTC? Solo sin audio */
        return on && !audio_on();
    t->ticks++;
    t->cloning &= on;
    /* Con la caja (el mando se ha visto activo desde que entró la clavija), «micro presente» forzado en
     * el detector, sea cual sea Audio Input: la cámara queda en modo jack y las filas de TCAudio deciden
     * la entrada en el códec, como sin la caja. Nunca al cargar: escribir en el detector durante el
     * arranque dejaba sin filas a TCAudio y sin acceso a SupManager (variantes 1.4.0, 15-11). */
    if (t->ticks >= SETTLE_TICKS && t->ticks - t->eval_at >= EVAL_TICKS) {
        u32 want;

        t->eval_at = t->ticks;
        if (!jack_in())
            t->latch = 0;                               /* sin clavija: se olvida la caja */
        else if (B8(STATE_REMOTE))
            t->latch = 1;                               /* la caja se vio: aguanta el parpadeo al encender el mando */
        want = t->latch;                /* también en Internal: TCAudio elige la entrada en el códec desde el
                                         * modo jack (con TC, micros en un canal y TC en el otro) */
        if (want != t->force_mic) {
            t->force_mic = want;
            B8(DET_FORCE + 1u) = (uint8_t)want;
            t->eval = 1;
        }
        /* La cámara reevalúa el jack solo cuando cambia algo en él: se lo pedimos con su propia función
         * (comprobado por consola el 15-11: micro 0 -> 1, evento 0x65). Se llama desde la tarea de audio:
         * la petición a la UI (MV_fpMicApl) no se atiende; ver la revisión pendiente en docs/TECNICA.md. */
        if ((t->eval || (want && !B8(STATE_MIC))) && !recording()) {
            t->eval = 0;
            ((void (*)(u32))F_JACK_EVAL)(REMODEV_JACK);
        }
    }
    if (t->row_ok && t->locked != on + 1u) {                 /* con TC-IN la fila Int+Ext Mics se queda en Off */
        t->locked = on + 1u;
        ((void (*)(u32, const char *, u32))F_UI_POST)(0, (const char *)t->mix_name, on ? 0u : t->mix);
    }
    if (!on) {
        t->pending |= select_input(t, 0u, 0u);
    } else {
        int audio;
        u32 cb = R32(METER_CB), mine = (u32)(uintptr_t)&tca_meter;

        refresh_settings(t);
        audio = audio_on();
        if (!audio)
            t->cloning = 0;
        if (cb && cb != mine) {
            t->meter = cb;
            R32(METER_CB) = mine;
        }
        if (sync_dsp(audio))
            pump(t);
        t->pending |= select_input(t, 1u, (u32)audio);
    }
    /* la cámara solo reescribe el códec al grabar o al salir del menú: se le pide a la UI que lo
     * haga ya. Nunca grabando, y una sola petición hasta que la UI la atienda. */
    if (t->pending == 1u && t->apply_ok && !recording()) {
        t->pending = 2u;
        ((void (*)(u32, const char *, u32))F_UI_POST)(0, (const char *)t->apply_name, ++t->apply_seq);
    }
    return on;
}

/* Las filas se guardan en el propio fichero del módulo, nunca en los ajustes de la cámara. */
#define REC_MAGIC     0x32434154u       /* "TAC2" */
#define HDR           48u
#define ALIAS         0x80000000u
#define F_CTOR        0xC0365E90u
#define F_OPEN        0xC0365FB0u
#define F_READ        0xC0366060u
#define F_WRITE       0xC03660E8u
#define F_SEEK        0xC03661E0u
#define F_CLOSE       0xC0366020u
#define F_DTOR        0xC0365ED0u
#define MOV_SELECTOR  0xC347D1D4u       /* grabando: como res-custom y Gyro2 */
#define MOVREC_DEFAULT 0xC347E08Fu
#define MOVREC_SEL21  0xC3476609u
#define XMP_FOBJ_PTR  0xC358E140u
#define MOV_ACTIVE    0xC35A952Cu

__asm__(".text\n.balign 4\ntca_rec:\n    .word 0, 0, 0, 0\n");
extern const u32 tca_rec[4];

static u32 uca(u32 a) { return a >= 0xC0000000u ? a - ALIAS : a; }
static u32 rec_check(u32 v) { return ~(REC_MAGIC ^ v); }

static int recording(void)
{
    u32 sel = R32(MOV_SELECTOR) == 0x21u ? MOVREC_SEL21 : MOVREC_DEFAULT;

    return B8(sel) || R32(XMP_FOBJ_PTR) || B8(MOV_ACTIVE);
}

static void save_rows(struct tca *t)
{
    u32 v = t->forced | t->mix << 1;
    u32 r[4] = {REC_MAGIC, 1u, v, rec_check(v)};
    u32 at = (u32)(uintptr_t)tca_rec - t->block, fo = uca((u32)(uintptr_t)t->fobj), back = fo + 0x300u;

    if (recording() || t->svc->file_is(t->svc, t->path, (const void *)(uintptr_t)t->block, HDR) != SL_OK)
        return;                         /* renombrado o sustituido: nunca se escribe */
    for (u32 i = 0; i < 4u; i++)
        R32(back + 0x40u + 4u * i) = r[i];
    ((void (*)(void))F_DCACHE)();
    ((u32 (*)(u32, u32))F_CTOR)(fo, 1);
    if (((u32 (*)(u32, u32, u32))F_OPEN)(fo, (u32)(uintptr_t)t->path, 7)) {
        if (((u32 (*)(u32, u32, u32))F_SEEK)(fo, at, 0))
            ((u32 (*)(u32, u32, u32))F_WRITE)(fo, uca(back + 0x40u), 16);
        ((u32 (*)(u32))F_CLOSE)(fo);
    }
    ((u32 (*)(u32, u32))F_DTOR)(fo, 2);
}

/* En el hilo de la UI, como la detección del jack: SetMic solo actúa si el modo cambia, y entonces
 * la cámara reescribe el códec con el valor que hay en los sitios. Sin jack no hay nada que cambiar. */
static void apply_now(struct tca *t)
{
    select_input(t, R32(TC_ENABLED) != 0u, (u32)audio_on());
    t->pending = 0;
    if (R32(MIC_MANAGER) == 1u && !recording()) {
        R32(MIC_MANAGER) = 0;
        ((void (*)(u32, u32))F_SETMIC)(MIC_MANAGER, 1u);
    }
}

static u32 apply_requested(uintptr_t descriptor)
{
    (void)descriptor;
    apply_now((struct tca *)(uintptr_t)tca_ctx);
    return 0;
}

static u32 mic_changed(uintptr_t descriptor)
{
    struct tca *t = (struct tca *)(uintptr_t)tca_ctx;

    if (R32(descriptor + 8u) == 1u && t->mix && !R32(TC_ENABLED)) {     /* Int+Ext Mics: solo Auto */
        ((void (*)(u32, const char *, u32))F_UI_POST)(0, (const char *)t->mic_name, 0u);
        return 0;
    }
    t->forced = R32(descriptor + 8u) == 1u;
    t->both = 0;
    t->seen_at[0] = t->seen_at[1] = t->ticks - 0x1000u;  /* lo visto con el micro puesto no cuenta */
    if (t->forced)
        t->mix = 0;                             /* la última elección manda al apagar TC-IN */
    save_rows(t);
    apply_now(t);
    return 0;
}

static u32 mix_changed(uintptr_t descriptor)
{
    struct tca *t = (struct tca *)(uintptr_t)tca_ctx;

    if (R32(TC_ENABLED)) {                      /* sin efecto con TC-IN: vuelve a Off, y lo guardado sigue */
        if (R32(descriptor + 8u))
            ((void (*)(u32, const char *, u32))F_UI_POST)(0, (const char *)t->mix_name, 0u);
        return 0;
    }
    t->mix = R32(descriptor + 8u) == 1u;
    if (t->mix && t->forced) {
        t->forced = 0;
        ((void (*)(u32, const char *, u32))F_UI_POST)(0, (const char *)t->mic_name, 0u);
    }
    save_rows(t);
    apply_now(t);
    return 0;
}

static uintptr_t ui_variable(uintptr_t app, const char *name)
{
    uintptr_t d = 0;

    if (((u32 (*)(uintptr_t, const char *, uintptr_t *))F_UI_LOOKUP)(R32(app + APP_REGISTRY) + REGISTRY_OFF,
                                                                     name, &d) || !d || (d & 3u))
        return 0;
    return d;
}

/* Sin la fila, el módulo sigue valiendo: se queda en su valor de arranque. */
static u32 add_row(const struct sl_svc *svc, const char *name, const char *from, const char *to,
                   u32 value, u32 (*changed)(uintptr_t))
{
    uintptr_t app = R32(GUI_OBJECT);
    struct uia_outcome ui;

    if (!app || (app & 3u) || !R32(app + APP_REGISTRY))
        return 0;
    if (!ui_variable(app, name)) {
        u32 def[3];

        def[0] = 0;
        def[1] = (u32)(uintptr_t)name;
        def[2] = value;
        if (((u32 (*)(uintptr_t, u32, const u32 *))F_UI_REGISTER)(app, 1u, def) || !ui_variable(app, name))
            return 0;
    }
    svc->publish(svc);
    if (fpui_stock_apply(svc, from, (u32)(to - from), &ui) != UIA_OK)
        return 0;
    ((u32 (*)(uintptr_t, const char *, uintptr_t))F_UI_SUBSCRIBE)(GUI_OBJECT, name, (uintptr_t)changed);
    svc->publish(svc);
    return 1;
}

/* Una variable de la UI sin fila: tca_tick la publica y la UI llama a apply_now en su hilo. */
static u32 add_variable(const char *name, u32 (*changed)(uintptr_t))
{
    uintptr_t app = R32(GUI_OBJECT);
    u32 def[3];

    if (!app || (app & 3u) || !R32(app + APP_REGISTRY))
        return 0;
    if (!ui_variable(app, name)) {
        def[0] = 0;
        def[1] = (u32)(uintptr_t)name;
        def[2] = 0;
        if (((u32 (*)(uintptr_t, u32, const u32 *))F_UI_REGISTER)(app, 1u, def) || !ui_variable(app, name))
            return 0;
    }
    ((u32 (*)(uintptr_t, const char *, uintptr_t))F_UI_SUBSCRIBE)(GUI_OBJECT, name, (uintptr_t)changed);
    return 1;
}

static void add_rows(struct tca *t, const struct sl_svc *svc)
{
    const char *base = (const char *)&tca_ctx;

    t->mic_name[0] = t->mix_name[0] = 0x665F564Du;      /* MV_f */
    t->mic_name[1] = t->mix_name[1] = 0x63694D70u;      /* pMic */
    t->mic_name[2] = 0x00746E49u;                       /* Int */
    t->mix_name[2] = 0x0078694Du;                       /* Mix */
    add_row(svc, (const char *)t->mic_name, base + tca_ui[0], base + tca_ui[1], t->forced, mic_changed);
    t->row_ok = add_row(svc, (const char *)t->mix_name, base + tca_ui[2], base + tca_ui[3], t->mix, mix_changed);
    t->apply_name[0] = 0x665F564Du;                     /* MV_f */
    t->apply_name[1] = 0x63694D70u;                     /* pMic */
    t->apply_name[2] = 0x006C7041u;                     /* Apl */
    t->apply_ok = add_variable((const char *)t->apply_name, apply_requested);
}

int sup_entry(void *block, const struct sl_svc *svc, const char *path)
{
    const struct sl4_header *h = block;
    struct tca *t = (struct tca *)((uintptr_t)block + ((h->file_len + 63u) & ~63u));
    u32 stock;

    (void)path;
    stock = RULE_STOCK;
    if (svc->claim(svc, SITE_RULE, 4u, &stock, SL_EXCL)) return SL_RELEASE;
    stock = GREY_STOCK;
    if (svc->claim(svc, SITE_GREY, 4u, &stock, SL_EXCL)) return SL_RELEASE;
    stock = JAM_STOCK;
    if (svc->claim(svc, SITE_JAM, 4u, &stock, SL_EXCL)) return SL_RELEASE;
    stock = MIC_A_WORD | IN_JACK << 16;
    if (svc->claim(svc, SITE_MIC_A, 4u, &stock, SL_EXCL)) return SL_RELEASE;
    stock = MIC_B_WORD | IN_JACK;
    if (svc->claim(svc, SITE_MIC_B, 4u, &stock, SL_EXCL)) return SL_RELEASE;
    stock = MIC_C_STOCK;
    if (svc->claim(svc, SITE_MIC_C, 4u, &stock, SL_EXCL)) return SL_RELEASE;
    t->int_a = R32(SITE_INT_A);
    t->int_b = R32(SITE_INT_B);
    t->int_ok = t->int_a >> 16 == (INT_MOVS | IN_MICS) && (t->int_b & 0xFFFFu) == (INT_MOVS | IN_MICS) &&
                !svc->claim(svc, SITE_INT_A, 4u, &t->int_a, SL_EXCL) &&
                !svc->claim(svc, SITE_INT_B, 4u, &t->int_b, SL_EXCL);
    stock = R32(DET_FORCE) & ~DET_FORCE_MIC;           /* +0x10..+0x13: nuestro byte, de serie a 0 */
    if (svc->claim(svc, DET_FORCE, 4u, &stock, SL_EXCL)) return SL_RELEASE;
    stock = TICK_STOCK0;
    if (svc->claim(svc, SITE_TICK, 4u, &stock, SL_EXCL)) return SL_RELEASE;
    stock = TICK_STOCK1;
    if (svc->claim(svc, SITE_TICK + 4u, 4u, &stock, SL_EXCL)) return SL_RELEASE;
    stock = R32(METER_CB);              /* después de SITE_TICK: al apagar, el diario la restaura
                                         * cuando tca_tick ya no puede volver a cambiarla */
    if (svc->claim(svc, METER_CB, 4u, &stock, SL_EXCL)) return SL_RELEASE;

    t->magic = 0x31414354u;
    t->channel = 1u;
    t->input = IN_JACK;
    t->source = 1u;
    t->svc = svc;
    t->block = (u32)(uintptr_t)block;
    if (tca_rec[0] == REC_MAGIC && tca_rec[3] == rec_check(tca_rec[2]) && tca_rec[2] <= 3u) {
        t->forced = tca_rec[2] & 1u;
        t->mix = tca_rec[2] >> 1;
    }
    {
        const char *p = svc->self_path(svc, block);
        u32 n = 0;

        if (p)
            for (; n < sizeof t->path - 1u && p[n]; n++) t->path[n] = p[n];
        t->path[n] = 0;
    }
    t->span[0] = t->span[1] = FRAME_SAMPLES;
    t->fps[0] = t->fps[1] = FPS;
    ltcdec_init(&t->dec[0], BIT_SAMPLES);
    ltcdec_init(&t->dec[1], BIT_SAMPLES);
    *(volatile u32 *)(uintptr_t)&tca_ctx = (u32)(uintptr_t)t;
    svc->publish(svc);
    add_rows(t, svc);
    R32(SITE_RULE) = RULE_NEW;
    R32(SITE_GREY) = GREY_NEW;
    R32(SITE_JAM) = JAM_NEW;
    R32(SITE_TICK + 4u) = (u32)(uintptr_t)&tca_tick;    /* primero la dirección */
    svc->publish(svc);
    R32(SITE_TICK) = TICK_NEW0;
    svc->publish(svc);
    return SL_KEEP;
}
