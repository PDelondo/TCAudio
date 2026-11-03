# TCAudio: technical notes

Firmware Ver.5.02. Everything here was read from the firmware or measured on a camera unless marked **hypothesis**. [Versión en español](TECNICA.md).

## Why the fp stops recording audio with TC-IN

The timecode input is not separate hardware. LTC enters through the audio jack and the audio codec, on either channel: stock TC-IN locked with TC on channel 2 (Rode Wireless PRO) and on channel 1 (Deity TC-1). With TC-IN on:

1. A rule at `0xC00960FC` forces the audio setting off (`ctx+0xE0`, where `ctx = *0xC3075230`).
2. The audio DSP starts with bit `0x800` set in register `0x1C` of its control block (`*(0x2AA01244)`). The DSP then decodes LTC itself and stops delivering samples: peak register `0x198` reads 0.
3. The DSP leaves the decoded word in a mailbox at `0x2AA01200`.

The DSP fixes its output when it starts. Clearing the bit while running turns the meters on but does not bring audio back to the file, and setting it while running does not re-enable LTC decoding.

## How TCAudio works

| Site | Change | Purpose |
|---|---|---|
| `0xC00960FC` | `mov r3,#0` → `mov r3,r6` | Removes the "external TC means no audio" rule |
| `0xC04A152C` | `bne` → `b` | Stops Audio Recording going grey with TC-IN |
| `0xC00CEB94` | `mov r3,#2` → `mov r3,#1` | Re-jams at 1 frame of error |
| `0xC00DA154`, `0xC00DAA18` | `movs r1,#2` → another value, when needed | Internal mic with the jack |
| `0xC00DA154`, `0xC00DAA14` | `bne` → `b`, when needed | Stereo mics with TC-IN off |
| `0xC00D6EC0` | jump to `tca_tick` | Runs once per audio block in the driver task |
| `0xC31CC81C` | pointer to `tca_meter` | Meter callback, set at run time |

All of them are claimed, so the loader's journal puts them back at power-off, in claim order.

`c00d6ec0` is a good place to work: it is called by the audio driver task (`c00d41a0`) once per block, and also by the DSP start (`c00d7b62`, return address `0xC00D7B67`). `tca_tick` tells these apart. For the DSP start it answers "TC enabled" only if audio is off, so the DSP starts without bit `0x800`.

### Decoding

The sample ring runs from `*0xC31D23F8` to `*0xC31D23F0`, written by the DSP (write pointer: register `0x74`), in 16-bit stereo. The module reads it in bursts of six LDM/STM words, because the ring is uncached and single accesses load the bus far more.

`ltcdec.c` is an original biphase decoder, about 90 lines, with no dependencies:

- Threshold relative to the signal: hysteresis is a quarter of a decaying peak (about 40 ms), with a floor of 32. Real LTC divided by 128 decoded with no errors; noise or speech alone produced no frames.
- A frame counts only with exactly 80 bits between two sync words.
- The half-bit length adapts, limited to 23.98–30 fps.

With the jack and Audio Input on Auto, both channels are always decoded, so TC is found even when it reaches one channel after locking on the other. With the internal mic in use (Internal, or Auto with TC on both channels), the other channel is checked for 6 blocks in every 64 to save CPU.

### Delivering the timecode

A frame is published only if it is the successor of the previous frame on its channel, which rejects a single broken frame. The word goes in the mailbox the DSP used to fill: BCD `ff | ss<<8 | mm<<16 | hh<<24`, bit 6 inverted drop-frame, bit 31 valid. It is projected to the current frame by the samples elapsed since decoding. The firmware then reads it as usual (`c00d41a0` → `_c31cc834` → `c00ce8e0`).

Lock details in `c00ce8e0`: it adds 2 frames, counts frames up to the frame rate, and at 24 fps accepts only `ff ≡ 1 mod 4`. It re-jams through `c00ce560(tc, fps, drop, threshold)`; stock threshold is 2 frames. It does not re-jam during a take.

### Rates

The rate is measured in the signal, in samples per frame, because the TC count depends on the generator. It is then compared with the camera's index at `0xC31CC408`, read on camera:

| Rate | Index | Samples per frame |
|---|---|---|
| 23.98 | 1 | 2002 |
| 24, 48 | 2 | 2000 |
| 25, 50 | 3 | 1920 |
| 29.97, 59.94 | 4 | 1601.6 |

A 16-frame average separates 2002 from 2000, which a single frame cannot. On mismatch nothing is published, so the camera stays unlocked, as stock does. Cloning continues. The index updates on leaving the menu. 25 = 3 is inferred from `c00ce4b8` and from 50 = 3, not read at 25.

### Cloning and meters

The audio channel is copied over the LTC channel in the ring before the WAV writer takes it. It stops if no LTC has arrived for about 1.4 s, and does not run if both channels carry LTC. Anything that does not decode as LTC counts as audio: a channel with silence, noise or the Zoom M4 echo (see Lessons) is cloned all the same.

The DSP measures peaks before cloning. The meter callback at `_c31cc81c` (`c01ff2b0(dB left, dB right)`) is replaced by `tca_meter`, which copies the audio channel's value. It only affects the display.

### Audio Input and Int+Ext Mics: internal mic and the jack

Input routing is register 5 of the audio codec, written through `c00daf70(reg, val)`. The firmware writes 2 for the jack, and 0x24 (both mics), 0x0C or 0x30 (one mic) for the internal mics, from `c00da128` and `c00da888`, the latter when recording starts. Measured on camera, the fields look like this (**inferred**, there is no datasheet): bits 0-1 the jack (2 straight, 1 both jack inputs summed on the right), bits 2-3 and 4-5 one mic each (1 right, 2 left, 3 both). The jack branch also sets bit 0x20 of register 10, which **seems** to keep the right jack input connected.

| Value | Left | Right | Verified |
|---|---|---|---|
| 0x28 | Both mics | Right jack input | C001_389: 411 of 414 LTC frames, audio on left jack cut |
| 0x29 | Both mics | Both jack inputs summed | C001_385: 388 of 391 frames with the right input empty; C001_386: unusable with audio on it |
| 0x2B | Mixed | Mixed | C001_390: unusable |

The module writes the value at both sites and flushes the caches, from the driver task, using the firmware cache routines. Auto uses 0x28 when LTC arrives on both channels. Internal uses 0x28, or 0x29 if TC was seen on the left. The TC channel becomes right and left is cloned over it. With TC-IN off and Internal, both `bne` go to the internal-mic branch: stereo, jack disconnected (C001_399). Int+Ext Mics uses 0x28 with TC-IN off (C001_401). On a row change the module writes the sites, clears the mic manager word and calls `SetMic(1)` from the UI thread, so the camera rewrites the codec as when a jack is plugged in. Auto with TC on both channels has no row change, so it applies the next time the camera writes the register. Both rows are stored in the module file (`tca_rec`), never in camera settings. While TC-IN is on, Int+Ext Mics is posted back to Off with `0xC0593F30`. Stock rows go grey natively; the copied row has no such link, and binding `SelectLock` to a variable did not work. The codec model has not been identified.

### Audio setting

Audio Recording is the stock setting. Its effective value is `ctx+0xE0`, updated on leaving the menu. On a cold boot with TC-IN the setting object's copy (`0xC31ACC7E`) reads 0, so the module reads the stored value directly: `0xC31B3A80` if the byte at `0xC31B954C` is 0, otherwise `0xC31B9348`. About 5 s after boot it writes the exclusion cache `0xC3768BB4` and notifies the exclusion manager, `c049ef18(0xC3766AEC, up, 0x24, 1)`, so the row leaves grey. This runs from the driver task. Earlier attempts calling other functions from that task hung the camera.

## Lessons

- **Every pointer into the module must be claimed.** A development version set `_c31cc81c` without a claim. At power-off the journal removed the tick but not the pointer, and the camera hung with the red LED on (consistent with a call into released memory, see fpSup's `HOOKS_AT_POWER_OFF.md`). Claiming it after `c00d6ec0`, so the tick is gone first, fixed it. Recording tests do not reach this path.
- **Do not call firmware functions from the driver task** unless you are sure of their arguments.

## Tests

`test_tcaudio.py` runs the real v4 core and this module under Unicorn with synthetic LTC. They cover decoding, channel detection, cloning, meters, rates, patches and power-off restore. Several tests, including the power-off one, were broken on purpose to check they can fail. The emulator does not run the firmware's own re-jam. Camera results are in the README.

## Open questions

- Why a short take can start 2 frames off when recording right after locking. **Hypothesis:** the re-jam, which runs at most about once per second, had not acted yet.
- Message `1474` on cold boot: it is the text key of "Now Connecting" in `SYS_WarningMsg`. It also appears without TCAudio. **Hypothesis:** the text lookup at `C05E5B58`, which the loader hooks.
- Recording the camera mic with the Deity TC-1 on A-OUT (TC on channel 1, audio on channel 2). It would need a codec register 5 value that pairs the mics with jack channel 1 alone, and none has been found: 0x28 cuts channel 1 and 0x29 sums it with channel 2.
