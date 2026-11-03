"""TCAudio con el core v4 real (unicorn): LTC sintético a 23,976 fps, entregado por bloques en un
anillo como el del DSP, debe aparecer en el buzón. SPDX-License-Identifier: GPL-3.0-or-later

    FPSUP_REPO=/ruta/a/fpSup python3 -B -m unittest -v test_tcaudio
"""
import os
import pathlib
import random
import array
import struct
import sys
import unittest

from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, os.environ['FPSUP_REPO'] + '/loader-v4')
import build_tcaudio as J       # noqa: E402
import test_v4 as T           # noqa: E402

RING, RING_LEN, BLOCK = 0x52B00580, 0x4000, 0x2000
FORCED_WORD = 35                    # struct tca .forced
DSP = 0x52B049C0
STOCK = {0xC00DA154: 0x2102D10D, 0xC00DAA14: 0xD10D2801, 0xC00DAA18: 0x20052102, 0xC00CEB94: 0xE3A03002, 0xC00960FC: 0xE3A03000, 0xC04A152C: 0x1A000001, 0xC00D6EC0: 0x8D04F84D,
         0xC00D6EC4: 0x71DCF241}


def pcm():
    """Ruido fuerte a la izquierda (hace de voz) y LTC a la derecha, 2002 muestras por trama."""
    tens = lambda n: (n // 10) << 4 | n % 10
    frames = [(0x16, 0x35, tens(s), tens(f)) for s in range(35, 44) for f in range(24)]
    data = bytearray(ltc_stream(frames, 25, 2))
    rnd = random.Random(1)
    data[0::4] = bytes(rnd.getrandbits(8) for _ in range(len(data) // 4))
    data[1::4] = bytes(rnd.getrandbits(5) for _ in range(len(data) // 4))
    return bytes(data)


def bcd(w):
    f = lambda v, m: (v & 15) + ((v >> 4) & m) * 10
    return f(w >> 24, 3), f(w >> 16, 7), f(w >> 8, 7), f(w, 3)


def ltc_bits(hh, mm, ss, ff):
    bits = [0] * 80
    def put(v, at, n):
        for i in range(n):
            bits[at + i] = (v >> i) & 1
    put(ff & 15, 0, 4); put(ff >> 4, 8, 2)
    put(ss & 15, 16, 4); put(ss >> 4, 24, 3)
    put(mm & 15, 32, 4); put(mm >> 4, 40, 3)
    put(hh & 15, 48, 4); put(hh >> 4, 56, 2)
    put(0xBFFC, 64, 16)
    return bits


def ltc_stream(frames, bit=25, pad=0):
    """Tramas LTC seguidas en bifase, 16 bits estéreo, con el canal R a ±20000."""
    out, level = [], 1
    for i, b in enumerate(sum((ltc_bits(*f) for f in frames), [])):
        if pad and i % 80 == 0:
            out += out[-pad:]                                   # 29,97: la trama dura algo más
        for half in range(2):
            n = (bit + 1) // 2 if half == 0 else bit // 2
            if half == 0 or b:
                level = -level
            out += [level * 20000] * n
    return b''.join(struct.pack('<hh', 0, s) for s in out)


def ltc_samples(hh, mm, ss, ff):
    """Tres tramas LTC consecutivas; la última es la pedida."""
    return ltc_stream([(hh, mm, ss, ff - 2), (hh, mm, ss, ff - 1), (hh, mm, ss, ff)])


class TcaudioTests(unittest.TestCase):
    def boot(self):
        cam = T.boot((J.NAME, J.sup()))
        self.assertIsNone(cam.error)
        for base, size in ((0x2AA01000, 0x1000), (0x52B00000, 0x10000)):
            try:
                cam.mu.mem_map(base, size)
            except Exception:
                pass
        cam.put(0xC31CAA80, 0)              # sin objeto de ajustes: refresh_settings no actúa
        cam.put(0xC3075230, 0xC3074BE8)
        cam.put(0xC3074BE8 + 0xE0, 1)       # audio ON
        for a, v in ((0xC31B954C, 0), (0xC31B3A80, 1)):     # valor guardado del usuario: ON
            try:
                cam.mu.mem_map(a & ~0xFFF, 0x1000)
            except Exception:
                pass
            cam.put(a, v)
        cam.put(0xC3768BB4, 1)              # exclusión ya en 1: sin aviso a la interfaz
        cam.put(0xC31CC408, 1)              # cámara a 23,98
        cam.put(0xC31F7D84, 1)              # con jack
        cam.put(0xC31ACC7C, 0)              # copia que sale a 0 en un arranque en frío
        return cam

    def test_patches_and_restores(self):
        cam = self.boot()
        for a, s in STOCK.items():
            self.assertEqual(struct.unpack_from('<I', cam.img, a - 0xC0000000)[0], s, hex(a))
        self.assertEqual(cam.w(0xC00960FC), 0xE1A03006)
        self.assertEqual(cam.w(0xC04A152C), 0xEA000001)
        self.assertEqual(cam.w(0xC00CEB94), 0xE3A03001)
        self.assertEqual(cam.w(0xC00D6EC0), 0xF000F8DF)
        self.assertEqual(cam.w(0xC00D6EC4) >> 28, 0xC)
        self.assertEqual(cam.w(0xC00D6EC4) & 1, 1)
        cam.power_off()
        for a, s in STOCK.items():
            self.assertEqual(cam.w(a), s, hex(a))

    def test_ltc_reaches_the_mailbox(self):
        cam = self.boot()
        cam.put(0xC31D17DC, 1)
        cam.put(0x2AA01244, DSP)
        cam.put(0xC31D23F8, RING)
        cam.put(0xC31D23F0, RING + RING_LEN)
        cam.put(DSP + 0x74, RING)
        data, off, seen = pcm(), 0, []
        noise = b''.join(struct.pack('<hh', 0, ((i * 7919) % 2001) - 1000) for i in range(0x800))
        data = noise * 20 + data        # primero ruido, como con el jack sin señal
        tick = cam.w(0xC00D6EC4) & ~1
        wp = 0
        self.assertEqual(cam.call(tick, thumb=True)[0], 1)     # anillo nuevo: se sitúa
        while off + BLOCK <= len(data) and off < BLOCK * 220:
            cam.mu.mem_write(RING + wp, data[off:off + BLOCK])
            off += BLOCK
            wp = (wp + BLOCK) % RING_LEN
            cam.put(DSP + 0x74, RING + wp)
            self.assertEqual(cam.call(tick, thumb=True)[0], 1)
            w = cam.w(0x2AA01200)
            if w and (not seen or seen[-1] != w):
                seen.append(w)
        self.assertGreater(len(seen), 150)
        self.assertTrue(all(w >> 31 for w in seen))
        self.assertEqual(bcd(seen[0])[:3], (16, 35, 35))
        for a, b in zip(seen, seen[1:]):
            d = (bcd(b)[2] * 24 + bcd(b)[3]) - (bcd(a)[2] * 24 + bcd(a)[3])
            self.assertIn(d % (60 * 24), (1, 2), (hex(a), hex(b)))

    def feed(self, cam, data, block=BLOCK):
        cam.put(0xC31D17DC, 1)
        cam.put(0x2AA01244, DSP)
        cam.put(0xC31D23F8, RING)
        cam.put(0xC31D23F0, RING + RING_LEN)
        cam.put(DSP + 0x74, RING)
        tick = cam.w(0xC00D6EC4) & ~1
        wp, seen = 0, []
        cam.call(tick, thumb=True)
        for off in range(0, min(len(data), BLOCK * 200) - block, block):
            cam.mu.mem_write(RING + wp, data[off:off + block])
            wp = (wp + block) % RING_LEN
            cam.put(DSP + 0x74, RING + wp)
            cam.call(tick, thumb=True)
            seen.append(cam.w(0x2AA01200))
        return seen

    def feed_more(self, cam, data):
        tick = cam.w(0xC00D6EC4) & ~1
        wp = (cam.w(DSP + 0x74) - RING) & ~(BLOCK - 1)
        for off in range(0, len(data) - BLOCK + 1, BLOCK):
            cam.mu.mem_write(RING + wp, data[off:off + BLOCK])
            wp = (wp + BLOCK) % RING_LEN
            cam.put(DSP + 0x74, RING + wp)
            cam.call(tick, thumb=True)

    def test_finds_the_ltc_on_the_left_channel(self):
        data = pcm()
        swapped = bytearray(len(data))
        swapped[0::4], swapped[1::4] = data[2::4], data[3::4]
        swapped[2::4], swapped[3::4] = data[0::4], data[1::4]
        seen = self.feed(self.boot(), bytes(swapped))
        self.assertEqual(bcd(seen[-1])[:2], (16, 35))
        self.assertTrue(seen[-1] >> 31)

    def test_right_channel_still_works_with_loud_speech_left(self):
        seen = self.feed(self.boot(), pcm())
        self.assertEqual(bcd(seen[-1])[:2], (16, 35))
        self.assertTrue(seen[-1] >> 31)

    def test_reads_a_very_weak_ltc(self):
        a = array.array('h')
        a.frombytes(pcm())
        for i in range(1, len(a), 2):
            a[i] = int(a[i] / 128)                  # canal del TC con la ganancia al mínimo
        seen = self.feed(self.boot(), a.tobytes())
        self.assertEqual(bcd(seen[-1])[:2], (16, 35))
        self.assertTrue(seen[-1] >> 31)

    def test_a_broken_last_frame_never_reaches_the_mailbox(self):
        good = [(0x11, 0x54, 0x20, f) for f in range(0x10)]
        # la última trama nunca llega a su flanco final: la rota es la última que se decodifica
        data = ltc_stream(good + [(0x04, 0x54, 0x20, 0x10), (0x11, 0x54, 0x20, 0x11)])
        data += bytes(BLOCK * 8)
        seen = self.feed(self.boot(), data + bytes(BLOCK * 8))
        words = [w for w in seen if w >> 31]
        self.assertTrue(words)
        self.assertEqual({bcd(w)[0] for w in words}, {11})

    def rate_test(self, fps, bit, rate, pad=0):
        tens = lambda n: (n // 10) << 4 | n % 10
        frames = [(0x10, 0x20, tens(s), tens(f)) for s in (30, 31, 32) for f in range(fps)]
        cam = self.boot()
        cam.put(0xC31CC408, rate)
        return self.feed(cam, ltc_stream(frames, bit, pad))

    def assert_locks(self, seen, fps):
        words = [w for w in seen if w >> 31]
        self.assertEqual(max(bcd(w)[3] for w in words), fps - 1)
        self.assertEqual(bcd(words[-1])[:2], (10, 20))
        self.assertIn(bcd(words[-1])[2], (31, 32))

    def test_25_fps_counts_to_25(self):
        self.assert_locks(self.rate_test(25, 24, 3), 25)

    def test_29_97_counts_to_30(self):
        self.assert_locks(self.rate_test(30, 20, 4, 2), 30)

    def test_24_locks_at_24(self):
        self.assert_locks(self.rate_test(24, 25, 2), 24)

    def test_another_rate_never_locks(self):
        self.assertEqual(self.rate_test(25, 24, 1)[-1], 0)

    def test_24_never_locks_at_23_98(self):
        self.assertEqual(self.rate_test(24, 25, 1)[-1], 0)

    def test_30_never_locks_at_29_97(self):
        self.assertEqual(self.rate_test(30, 20, 4)[-1], 0)

    def test_23_98_never_locks_at_24(self):
        cam = self.boot()
        cam.put(0xC31CC408, 2)
        self.assertEqual(self.feed(cam, pcm())[-1], 0)

    def test_two_different_timecodes_never_alternate(self):
        data = bytearray(pcm())
        other = bytes(ltc_samples(0x01, 0x02, 0x03, 0x04)) * 55     # otro TC por el izquierdo
        n = min(len(data), len(other))
        data[0::4], data[1::4] = other[2::4][:n // 4] + data[0::4][n // 4:], other[3::4][:n // 4] + data[1::4][n // 4:]
        seen = [w for w in self.feed(self.boot(), bytes(data)) if w]
        self.assertTrue(seen)
        self.assertTrue(all(bcd(w)[:2] == (16, 35) for w in seen), [hex(w) for w in seen[:5]])

    def test_clone_copies_the_audio_over_the_ltc_channel(self):
        cam = self.boot()
        data = pcm()
        self.feed(cam, data)
        ring = bytes(cam.mu.mem_read(RING, RING_LEN))
        left, right = ring[0::4] + ring[1::4], ring[2::4] + ring[3::4]
        self.assertEqual(left, right)
        self.assertNotEqual(ring, data[:RING_LEN])

    def test_meter_of_the_tc_channel_shows_the_audio(self):
        cam = self.boot()
        stub = RING + RING_LEN                              # bx lr: la pantalla de serie
        cam.mu.mem_write(stub, struct.pack('<H', 0x4770))
        cam.put(0xC31CC81C, stub | 1)
        self.feed(cam, pcm())
        mine = cam.w(0xC31CC81C)
        self.assertNotEqual(mine, stub | 1)
        def meter(left, right):
            cam.call(mine & ~1, r0=left, r1=right, thumb=True)
            return cam.mu.reg_read(UC_ARM_REG_R0), cam.mu.reg_read(UC_ARM_REG_R1)
        self.assertEqual(meter(10, 20), (10, 10))           # LTC a la derecha: copia la izquierda
        cam.put(0xC31D17DC, 0)
        cam.call(cam.w(0xC00D6EC4) & ~1, thumb=True)        # sin TC-IN, intacto
        self.assertEqual(meter(10, 20), (10, 20))

    def both_channels(self):
        data = bytearray(pcm())
        data[0::4], data[1::4] = data[2::4], data[3::4]
        return bytes(data)

    def test_ltc_on_both_channels_selects_the_mic_and_clones(self):
        cam = self.boot()
        self.assertEqual(self.feed(cam, self.both_channels())[-1] >> 31, 1)
        self.assertEqual(cam.w(0xC00DA154), 0x2128D10D)
        self.assertEqual(cam.w(0xC00DAA18), 0x20052128)
        cam.power_off()
        self.assertEqual(cam.w(0xC00DA154), 0x2102D10D)
        self.assertEqual(cam.w(0xC00DAA18), 0x20052102)

    def test_ltc_on_both_channels_with_small_blocks_selects_the_mic(self):
        # bloques de un octavo de trama: seis bloques no completan ninguna trama del otro canal
        cam = self.boot()
        self.feed(cam, self.both_channels(), block=BLOCK // 8)
        self.assertEqual(cam.w(0xC00DA154), 0x2128D10D)
        self.assertEqual(cam.w(0xC00DAA18), 0x20052128)

    def test_mic_is_left_when_the_ltc_goes(self):
        cam = self.boot()
        self.feed(cam, self.both_channels() [:BLOCK * 120] + bytes(BLOCK * 80))
        self.assertEqual(cam.w(0xC00DA154), 0x2102D10D)

    def test_one_channel_of_ltc_keeps_the_jack(self):
        cam = self.boot()
        self.feed(cam, pcm())
        self.assertEqual(cam.w(0xC00DAA18), 0x20052102)

    def forced_cam(self):
        """Fila Mic en Internal: el emulador no tiene interfaz, se escribe el campo."""
        cam = self.boot()
        tick = cam.w(0xC00D6EC4) & ~0xFFF
        ram = bytes(cam.mu.mem_read(tick, 0x10000))
        magic = ram.rindex(struct.pack('<I', 0x31414354))
        return cam, tick + magic + 4 * FORCED_WORD

    def test_internal_with_tc_on_the_right_uses_the_right_jack(self):
        cam, forced = self.forced_cam()
        cam.put(forced, 1)
        self.feed(cam, pcm()[:BLOCK * 60])
        self.assertEqual(cam.w(0xC00DAA18), 0x20052128)

    def test_internal_with_tc_on_the_left_crosses_the_jack(self):
        cam, forced = self.forced_cam()
        data = bytearray(pcm())
        data[0::4], data[1::4], data[2::4], data[3::4] = data[2::4], data[3::4], bytes(len(data) // 4), bytes(len(data) // 4)
        self.feed(cam, bytes(data[:BLOCK * 60]))
        self.assertEqual(cam.w(0xC00DAA18), 0x20052102)
        cam.put(forced, 1)
        self.feed_more(cam, bytes(data[BLOCK * 60:BLOCK * 64]))
        self.assertEqual(cam.w(0xC00DAA18), 0x20052129)

    def test_internal_without_tc_input_uses_both_mics(self):
        cam, forced = self.forced_cam()
        cam.put(forced, 1)
        cam.put(0xC31D17DC, 0)
        cam.call(cam.w(0xC00D6EC4) & ~1, thumb=True)
        self.assertEqual(cam.w(0xC00DA154), 0x2102E00D)
        self.assertEqual(cam.w(0xC00DAA14), 0xE00D2801)
        self.assertEqual(cam.w(0xC00DAA18), 0x20052102)
        cam.put(forced, 0)
        cam.call(cam.w(0xC00D6EC4) & ~1, thumb=True)
        self.assertEqual(cam.w(0xC00DA154), 0x2102D10D)
        self.assertEqual(cam.w(0xC00DAA14), 0xD10D2801)

    def test_int_ext_without_tc_input_keeps_the_jack_on_the_right(self):
        cam, forced = self.forced_cam()
        cam.put(forced + 4, 1)
        cam.put(0xC31D17DC, 0)
        cam.call(cam.w(0xC00D6EC4) & ~1, thumb=True)
        self.assertEqual(cam.w(0xC00DAA18), 0x20052128)

    def test_int_ext_with_tc_input_is_auto(self):
        cam, forced = self.forced_cam()
        cam.put(forced + 4, 1)
        self.feed(cam, pcm()[:BLOCK * 60])
        self.assertEqual(cam.w(0xC00DAA18), 0x20052102)

    def test_internal_back_to_auto_returns_to_the_jack(self):
        cam, forced = self.forced_cam()
        cam.put(forced, 1)
        self.feed(cam, pcm()[:BLOCK * 60])
        self.assertEqual(cam.w(0xC00DAA18), 0x20052128)
        cam.put(forced, 0)
        cam.put(forced + 12, 0)                     # .both, como hace mic_changed
        cam.put(forced - 4 * 18, 0)                 # .seen_at, lejos
        cam.put(forced - 4 * 17, 0)
        ticks = cam.w(forced - 4 * 34)
        cam.put(forced - 4 * 18, (ticks - 0x1000) & 0xFFFFFFFF)
        cam.put(forced - 4 * 17, (ticks - 0x1000) & 0xFFFFFFFF)
        self.feed_more(cam, pcm()[BLOCK * 60:BLOCK * 64])
        self.assertEqual(cam.w(0xC00DAA18), 0x20052102)

    def test_meter_is_restored_at_power_off(self):
        cam = self.boot()
        stub = (RING + RING_LEN) | 1
        before = cam.w(0xC31CC81C)
        cam.put(0xC31CC81C, stub)
        self.feed(cam, pcm()[:BLOCK * 4])
        self.assertNotEqual(cam.w(0xC31CC81C), stub)
        cam.power_off()
        self.assertEqual(cam.w(0xC31CC81C), before)

    def test_no_clone_once_the_ltc_is_gone(self):
        cam = self.boot()
        data = pcm()
        cam_data = data[:BLOCK * 60]
        rest = bytearray(data[BLOCK * 60:BLOCK * 140])
        rest[2::4], rest[3::4] = bytes(len(rest) // 4), bytes(len(rest) // 4)    # sin LTC
        rest[2::4] = bytes((i * 37) & 0xFF for i in range(len(rest) // 4))       # otro audio
        self.feed(cam, cam_data + bytes(rest))
        ring = bytes(cam.mu.mem_read(RING, RING_LEN))
        self.assertFalse(ring[0::4] == ring[2::4])

    def test_both_channels_clone_the_left_one(self):
        cam = self.boot()
        self.feed(cam, self.both_channels())
        ring = bytes(cam.mu.mem_read(RING, RING_LEN))
        self.assertEqual(ring[0::4], ring[2::4])

    def test_lead_carries_through_every_field(self):
        cam = self.boot()
        cam.put(0xC31D17DC, 1)
        cam.put(0x2AA01244, DSP)
        cam.put(0xC31D23F8, RING)
        cam.put(0xC31D23F0, RING + 0x8000)
        cam.put(DSP + 0x74, RING)
        tick = cam.w(0xC00D6EC4) & ~1
        cam.call(tick, thumb=True)
        wave = ltc_stream([(0x23, 0x59, 0x59, f) for f in (0x22, 0x23)] + [(0, 0, 0, 0)])
        wave += bytes(1200 * 4)                                          # ya va por el siguiente
        cam.mu.mem_write(RING, wave)
        cam.put(DSP + 0x74, RING + len(wave))
        cam.call(tick, thumb=True)
        w = cam.w(0x2AA01200)
        self.assertEqual(w >> 31, 1, hex(w))
        # la última trama no llega a su flanco final: 23 de la primera + 3200 muestras = 2 fot.
        self.assertEqual(bcd(w), (0, 0, 0, 1))

    def test_dsp_bit_is_cleared_only_with_audio_on(self):
        for audio, expected in ((1, 0x33400), (0, 0x33C00)):
            cam = self.boot()
            ctx = 0xC3074BE8
            cam.put(0xC31D17DC, 1)
            cam.put(0x2AA01244, DSP)
            cam.put(DSP + 0x1C, 0x33C00)
            cam.put(0xC31D23F8, RING)
            cam.put(0xC31D23F0, RING + RING_LEN)
            cam.put(DSP + 0x74, RING)
            tick = cam.w(0xC00D6EC4) & ~1
            cam.call(tick, thumb=True)                      # la primera vez
            self.assertEqual(cam.w(ctx + 0xE0) & 0xFF, 1)   # copia el valor del usuario
            cam.put(ctx + 0xE0, audio)                      # lo que pone la cámara al salir del menú
            cam.put(0xC31D17D8, 0x95E)
            cam.put(DSP + 0x1C, 0x33C00)
            cam.call(tick, thumb=True)
            self.assertEqual(cam.w(DSP + 0x1C), expected)

    def test_silent_when_tc_input_is_off(self):
        cam = self.boot()
        cam.put(0xC31D17DC, 0)
        cam.put(0x2AA01200, 0)
        tick = cam.w(0xC00D6EC4) & ~1
        self.assertEqual(cam.call(tick, thumb=True)[0], 0)
        self.assertEqual(cam.w(0x2AA01200), 0)


if __name__ == '__main__':
    unittest.main()
