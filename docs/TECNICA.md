# TCAudio: notas técnicas

Firmware Ver.5.02. Todo lo que hay aquí se ha leído del firmware o medido en cámara, salvo lo marcado como **hipótesis**. [English version](TECHNICAL.md).

## Por qué la fp deja de grabar audio con TC-IN

La entrada de TC no es un hardware aparte. El LTC entra por el jack de audio y el códec, por cualquiera de los dos canales: el TC-IN de serie enganchó con TC por el canal 2 (Rode Wireless PRO) y por el canal 1 (Deity TC-1). Con TC-IN activo:

1. Una regla en `0xC00960FC` fuerza el ajuste de audio a apagado (`ctx+0xE0`, con `ctx = *0xC3075230`).
2. El DSP de audio arranca con el bit `0x800` en el registro `0x1C` de su bloque de control (`*(0x2AA01244)`). Entonces decodifica él el LTC y deja de entregar muestras: el registro de picos `0x198` lee 0.
3. El DSP deja la palabra decodificada en un buzón, en `0x2AA01200`.

El DSP fija su salida al arrancar. Quitar el bit en marcha enciende los medidores, pero no devuelve el audio al fichero; ponerlo en marcha no reactiva la lectura de LTC.

## Cómo funciona TCAudio

| Punto | Cambio | Para qué |
|---|---|---|
| `0xC00960FC` | `mov r3,#0` → `mov r3,r6` | Quita la regla "TC externo, sin audio" |
| `0xC04A152C` | `bne` → `b` | Evita que Grabación de audio salga en gris con TC-IN |
| `0xC00CEB94` | `mov r3,#2` → `mov r3,#1` | Reajusta con 1 fotograma de error |
| `0xC00DA154`, `0xC00DAA18` | `movs r1,#2` → otro valor, cuando hace falta | Micro interno con jack |
| `0xC00DA154`, `0xC00DAA14` | `bne` → `b`, cuando hace falta | Micros en estéreo con TC-IN apagado |
| `0xC00D6EC0` | salto a `tca_tick` | Se ejecuta una vez por bloque en la tarea del driver |
| `0xC31CC81C` | puntero a `tca_meter` | Retrollamada de vúmetros, puesta en marcha |

Todos están reclamados, así que el diario del cargador los restaura al apagar, en orden de reclamación.

`c00d6ec0` es un buen sitio: la llama la tarea del driver de audio (`c00d41a0`) una vez por bloque, y también el arranque del DSP (`c00d7b62`, dirección de retorno `0xC00D7B67`). `tca_tick` distingue ambos casos. Al arranque del DSP solo le responde "TC activo" si el audio está apagado, y así el DSP arranca sin el bit `0x800`.

### Decodificación

El anillo de muestras va de `*0xC31D23F8` a `*0xC31D23F0`, lo escribe el DSP (puntero de escritura: registro `0x74`) y es estéreo de 16 bits. El módulo lo lee en ráfagas LDM/STM de seis palabras, porque el anillo no pasa por caché y los accesos sueltos cargan mucho más el bus.

`ltcdec.c` es un decodificador bifase propio, de unas 90 líneas y sin dependencias:

- Umbral relativo a la señal: la histéresis es la cuarta parte de un pico que decae (unos 40 ms), con un mínimo de 32. El LTC real dividido entre 128 se leyó sin errores; solo ruido o solo voz no produjeron tramas.
- Una trama solo vale con 80 bits exactos entre dos sincronías.
- La duración del medio bit se adapta, limitada a 23,98–30 fps.

Con el jack y Audio Input en Auto se decodifican siempre los dos canales, para detectar el TC aunque llegue a un canal después de enganchar con el otro. Con el micro interno puesto (Internal, o Auto con TC por los dos canales), el otro canal se revisa 6 bloques de cada 64 para ahorrar CPU.

### Entrega del TC

Una trama solo se publica si es la siguiente de la anterior en su canal, lo que rechaza una trama rota suelta. La palabra va al buzón que antes llenaba el DSP: BCD `ff | ss<<8 | mm<<16 | hh<<24`, bit 6 drop-frame invertido, bit 31 válido. Se proyecta al fotograma actual según las muestras pasadas desde que se decodificó. Después el firmware la lee como siempre (`c00d41a0` → `_c31cc834` → `c00ce8e0`).

Detalles del enganche en `c00ce8e0`: suma 2 fotogramas, cuenta fotogramas hasta la velocidad, y a 24 fps solo acepta `ff ≡ 1 mod 4`. Reajusta con `c00ce560(tc, fps, drop, umbral)`; el umbral de serie es 2 fotogramas. No reajusta durante una toma.

### Velocidades

La velocidad se mide en la señal, en muestras por trama, porque el contador del TC depende del generador. Después se compara con el índice de la cámara en `0xC31CC408`, leído en cámara:

| Velocidad | Índice | Muestras por trama |
|---|---|---|
| 23,98 | 1 | 2002 |
| 24, 48 | 2 | 2000 |
| 25, 50 | 3 | 1920 |
| 29,97, 59,94 | 4 | 1601,6 |

Una media de 16 tramas separa 2002 de 2000, lo que una trama suelta no puede. Si no coincide no se publica nada y la cámara no engancha, como de serie. El clonado continúa. El índice se actualiza al salir del menú. 25 = 3 se deduce de `c00ce4b8` y de 50 = 3; no se leyó a 25.

### Clonado y vúmetros

El canal de audio se copia sobre el del LTC en el anillo antes de que lo tome el escritor del WAV. Se detiene si no llega LTC en unos 1,4 s, y no actúa si los dos canales llevan LTC. Todo lo que no se decodifica como LTC cuenta como audio: un canal en silencio, con ruido o con el eco del Zoom M4 (ver Lecciones) se clona igual.

El DSP mide los picos antes del clonado. La retrollamada de vúmetros `_c31cc81c` (`c01ff2b0(dB izq, dB der)`) se sustituye por `tca_meter`, que copia el valor del canal de audio. Solo afecta a la pantalla.

### Audio Input e Int+Ext Mics: micro interno y jack

La ruta de entrada es el registro 5 del códec, escrito con `c00daf70(reg, val)`. El firmware escribe 2 para el jack, y 0x24 (los dos micros), 0x0C o 0x30 (un micro) para los internos, desde `c00da128` y `c00da888`, esta al empezar a grabar. Medido en cámara, los campos parecen estos (**deducido**, sin hoja de datos): bits 0-1 el jack (2 directo, 1 las dos entradas del jack sumadas a la derecha), bits 2-3 y 4-5 un micro cada uno (1 derecha, 2 izquierda, 3 ambos). La rama del jack pone además el bit 0x20 del registro 10, que **parece** mantener conectada la entrada derecha.

| Valor | Izquierda | Derecha | Comprobado |
|---|---|---|---|
| 0x28 | Ambos micros | Entrada derecha del jack | C001_389: 411 de 414 tramas LTC, audio del jack izquierdo cortado |
| 0x29 | Ambos micros | Las dos entradas del jack sumadas | C001_385: 388 de 391 tramas con la derecha vacía; C001_386: inservible con audio en ella |
| 0x2B | Mezcla | Mezcla | C001_390: inservible |

El módulo escribe el valor en los dos sitios y limpia las cachés, desde la tarea del driver, con las rutinas del firmware. Auto usa 0x28 si llega LTC por los dos canales. Internal usa 0x28, o 0x29 si el TC se vio a la izquierda. El canal del TC pasa a ser el derecho y se clona el izquierdo sobre él. Con TC-IN apagado e Internal, los dos `bne` saltan a la rama del micro interno: estéreo y jack desconectado (C001_399). Int+Ext Mics usa 0x28 con TC-IN apagado (C001_401). Al cambiar una fila, el módulo escribe los sitios, pone a cero la palabra del gestor de micro y llama a `SetMic(1)` desde el hilo de la interfaz, y la cámara reescribe el códec como al enchufar un jack. Auto con TC en los dos canales no nace de una fila, así que rige la próxima vez que la cámara escribe el registro. Las dos filas se guardan en el fichero del módulo (`tca_rec`), nunca en los ajustes de la cámara. Con TC-IN activo, Int+Ext Mics se devuelve a Off con `0xC0593F30`. Las filas de serie se ponen en gris de forma nativa; la fila copiada no tiene esa conexión, y enlazar `SelectLock` con una variable no funcionó. El modelo del códec no se ha identificado.

### Ajuste de audio

Grabación de audio es el ajuste de serie. Su valor efectivo es `ctx+0xE0`, que se actualiza al salir del menú. En un arranque en frío con TC-IN, la copia del objeto del ajuste (`0xC31ACC7E`) lee 0, así que el módulo lee el valor guardado: `0xC31B3A80` si el byte en `0xC31B954C` es 0; si no, `0xC31B9348`. Unos 5 s tras arrancar escribe la caché de exclusión `0xC3768BB4` y avisa al gestor, `c049ef18(0xC3766AEC, up, 0x24, 1)`, para que la fila salga de gris. Esto se hace desde la tarea del driver. Intentos anteriores de llamar a otras funciones desde esa tarea colgaron la cámara.

## Lecciones

- **Todo puntero al módulo debe ir reclamado.** Una versión de desarrollo cambiaba `_c31cc81c` sin reclamarlo. Al apagar, el diario quitaba el tick pero no el puntero, y la cámara se colgaba con el LED rojo encendido (compatible con una llamada a memoria liberada, ver `HOOKS_AT_POWER_OFF.md` de fpSup). Reclamarlo después de `c00d6ec0`, para que el tick salga antes, lo arregló. Las pruebas grabando no llegan a ese camino.
- **No llames a funciones del firmware desde la tarea del driver** si no estás seguro de sus argumentos.
- **Escuchar un canal a ratos no basta para detectarlo.** Una trama solo vale con 80 bits entre dos sincronías, y una ventana corta puede no completar ninguna. Si el otro canal solo se revisaba a ratos y el TC le llegaba después del enganche, Auto no lo detectaba nunca. Lo reproduce `test_ltc_reaching_the_right_after_the_lock_selects_the_mic`.
- **Señal no es TC.** El Zoom M4 lleva la entrada y la salida de TC en el mismo jack: punta entrada, anillo salida (manual del M4, p. 13). Con un cable estéreo, el canal izquierdo de la fp queda en la entrada del Zoom y recoge por diafonía capacitiva un eco del LTC del anillo: picos en cada flanco y casi nada entre ellos (C001_466, grabada con TC-IN apagado). El decodificador ve decenas de cruces falsos por bit y no saca ni una trama, así que el módulo lo trata como audio.

## Pruebas

`test_tcaudio.py` ejecuta el core v4 real y este módulo en Unicorn con LTC sintético. Cubren decodificación, detección de canal, clonado, vúmetros, velocidades, parches y restauración al apagar. Varias pruebas, entre ellas la del apagado, se rompieron adrede para comprobar que pueden fallar. El emulador no ejecuta el reajuste del propio firmware. Los resultados en cámara están en el README.

## Cuestiones abiertas

- Por qué una toma puede empezar con 2 fotogramas de diferencia si se graba justo tras enganchar. **Hipótesis:** el reajuste, que actúa como mucho cada segundo, aún no había actuado.
- Mensaje `1474` en arranque en frío: es la clave del texto "Now Connecting" en `SYS_WarningMsg`. También sale sin TCAudio. **Hipótesis:** la resolución de textos en `C05E5B58`, que engancha el cargador.
- Grabar el micro de la cámara con el Deity TC-1 en A-OUT (TC por el canal 1 y audio por el 2). Haría falta un valor del registro 5 del códec que junte los micros con solo el canal 1 del jack, y no se ha encontrado: 0x28 corta el canal 1 y 0x29 lo suma con el 2.
- Drop-frame con TC-IN: la cámara cuenta sin drop-frame aunque el generador vaya en DF (C001_435: 17:20:59:29 → 17:21:00:00). No se ha buscado en el firmware dónde se decide.
