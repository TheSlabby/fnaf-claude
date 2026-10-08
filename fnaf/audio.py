"""Procedurally synthesised sound effects and music.

Nothing is loaded from disk: every sound is computed in pure Python (stdlib
only, no numpy) when ``SoundBank.build()`` runs, then converted to whatever
format the mixer was actually opened with (8/16-bit signed or unsigned
integers or 32-bit float, any channel count, any sample rate).

Usage::

    bank = SoundBank()             # cheap; silent no-op if the mixer is off
    for _ in bank.build():         # generator: yields while synthesising
        draw_loading_screen(bank.progress)
    bank.loop("fan", "fan", 0.35)  # named loop on a reserved channel
    bank.play("door", 0.8)         # one-shot on the shared channel pool

Synthesis runs at the mixer rate divided by a small integer (22050 Hz for a
44100 Hz mixer, 24000 Hz for 48000 Hz) and is upsampled linearly. Loops are
built to be exactly periodic: oscillators run a whole number of cycles per
loop, noise is filtered circularly and event tails are folded back onto the
start, so there is no click at the loop point.

Requires Python 3.8+ (walrus operator, ``accumulate(initial=...)``).
"""

import math
import operator
import random
import sys
import types
import zlib
from array import array
from itertools import accumulate, chain, repeat

import pygame

TAU = 2.0 * math.pi
_sin = math.sin
_cos = math.cos
_exp = math.exp
_tanh = math.tanh
_add = operator.add
_mul = operator.mul

_NUM_CHANNELS = 32
_LOOP_CHANNELS = 10


def _midi(m):
    return 440.0 * 2.0 ** ((m - 69) / 12.0)


def _peak(x):
    return max(max(x), -min(x)) if x else 0.0


def _upsample(x, k, circular):
    """Linear-interpolation upsampling by an integer factor ``k``."""
    if k <= 1 or not x:
        return x
    nxt = x[1:]
    nxt.append(x[0] if circular else 0.0)
    out = [0.0] * (len(x) * k)
    out[0::k] = x
    for j in range(1, k):
        f = j / k
        g = 1.0 - f
        out[j::k] = [g * a + f * b for a, b in zip(x, nxt)]
    return out


# --------------------------------------------------------------------------
# DSP toolkit
# --------------------------------------------------------------------------

class _Synth:
    """Pure-Python DSP primitives bound to one sample rate.

    Signals are plain lists of floats. Recipes (the ``r_*`` methods) return
    ``(data, div)`` where ``data`` is a list (mono) or a ``(left, right)``
    tuple and ``div`` says the data is at ``sr / div`` (it gets upsampled).
    Long recipes are generators that yield between chunks of work.
    """

    def __init__(self, sr, rng=None, tables=None):
        self.sr = float(sr)
        self.rng = rng if rng is not None else random.Random(1987)
        self._tabs = tables if tables is not None else {}

    def sub(self, d):
        """A synth running at ``sr / d`` sharing this one's RNG and tables."""
        return _Synth(self.sr / d, self.rng, self._tabs)

    def reseed(self, name):
        self.rng.seed(zlib.crc32(name.encode("utf-8")))

    def ns(self, sec):
        return max(1, int(sec * self.sr + 0.5))

    # -- sources -----------------------------------------------------------

    def noise(self, n, amp=1.0):
        """Uniform white noise in (-amp, amp), zero mean (8-bit resolution)."""
        rng = self.rng
        try:
            raw = rng.randbytes(n)
        except AttributeError:  # Python < 3.9
            raw = rng.getrandbits(8 * n).to_bytes(n, "little")
        k = amp / 128.0
        h = 0.5 * k
        return [b * k + h for b in array("b", raw)]

    def sine(self, f, n, amp=1.0, ph=0.0):
        w = TAU * f / self.sr
        s = _sin
        return [amp * s(w * i + ph) for i in range(n)]

    def lw(self, f, n):
        """Angular step for ``f`` snapped to a whole number of cycles in ``n`` samples."""
        return TAU * max(1, int(round(f * n / self.sr))) / n

    def lsine(self, f, n, amp=1.0, ph=0.0):
        w = self.lw(f, n)
        s = _sin
        return [amp * s(w * i + ph) for i in range(n)]

    def table(self, harms, size=2048):
        """Single-cycle wavetable from ``(harmonic, amp, phase)`` triples, peak 1."""
        key = (size, tuple(harms))
        tab = self._tabs.get(key)
        if tab is None:
            tab = [0.0] * size
            s = _sin
            for k, a, p in harms:
                w = TAU * k / size
                tab = [v + a * s(w * i + p) for i, v in enumerate(tab)]
            g = 1.0 / (_peak(tab) or 1.0)
            tab = [v * g for v in tab]
            self._tabs[key] = tab
        return tab

    def nh(self, fmax, cap=64):
        """Harmonic count that keeps a tone of ``fmax`` Hz under Nyquist."""
        return max(1, min(cap, int(0.45 * self.sr / fmax)))

    def saw_tab(self, n):
        return self.table(tuple((k, 1.0 / k, 0.0) for k in range(1, n + 1)))

    def square_tab(self, n):
        return self.table(tuple((k, 1.0 / k, 0.0) for k in range(1, n + 1, 2)))

    def glottal_tab(self, n):
        return self.table(tuple((k, 1.0 / k ** 1.25, 0.15 * k) for k in range(1, n + 1)))

    def pulse_tab(self, n, duty):
        return self.table(tuple((k, abs(_sin(math.pi * k * duty)) / k, 0.0)
                                for k in range(1, n + 1)))

    def buzz_tab(self):
        """Mains buzz on a 60 Hz base: strong even harmonics, rough top end."""
        rng = random.Random(60)
        return self.table(tuple((k, (1.0 if k % 2 == 0 else 0.5) / k ** 0.85, rng.random() * TAU)
                                for k in range(1, self.nh(60.0, 60) + 1)))

    def osc(self, tab, freq, n=0, ph=0.0, ratio=1.0):
        """Wavetable oscillator; ``freq`` is a constant or a per-sample list."""
        L = len(tab)
        m = L - 1
        k = L * ratio / self.sr
        p0 = (ph % 1.0) * L
        if isinstance(freq, (int, float)):
            inc = freq * k
            return [tab[int(p0 + inc * i) & m] for i in range(n)]
        out = [tab[int(p) & m] for p in accumulate(map(k.__mul__, freq), initial=p0)]
        out.pop()
        return out

    def losc(self, tab, f, n, ph=0.0):
        """Wavetable oscillator snapped to whole cycles over ``n`` samples (loops)."""
        L = len(tab)
        m = L - 1
        inc = L * max(1, int(round(f * n / self.sr))) / n
        out = [tab[int(p) & m] for p in accumulate(repeat(inc, n), initial=(ph % 1.0) * L)]
        out.pop()
        return out

    def lfo(self, n, cycles, ph=0.0, lo=0.0, hi=1.0, block=32):
        """Slow sine LFO with whole ``cycles`` over ``n`` samples, stepped per block."""
        nb = -(-n // block)
        w = TAU * cycles * block / n
        mid = 0.5 * (lo + hi)
        amp = 0.5 * (hi - lo)
        ctrl = [mid + amp * _sin(w * (j + 0.5) + ph) for j in range(nb)]
        out = list(chain.from_iterable(map(repeat, ctrl, repeat(block, nb))))
        del out[n:]
        return out

    # -- filters -------------------------------------------------------------

    def lp(self, x, fc, y=0.0):
        """One-pole low-pass."""
        b = _exp(-TAU * fc / self.sr)
        a = 1.0 - b
        return [(y := b * y + a * v) for v in x]

    def hp(self, x, fc):
        return [v - w for v, w in zip(x, self.lp(x, fc))]

    def lp_circ(self, x, fc):
        """One-pole low-pass of a periodic signal (state wrapped round)."""
        m = min(len(x), int(8.0 * self.sr / (TAU * fc)) + 1)
        return self.lp(x, fc, self.lp(x[-m:], fc)[-1])

    def hp_circ(self, x, fc):
        return [v - w for v, w in zip(x, self.lp_circ(x, fc))]

    def lp_var(self, x, fcs, circular=False):
        """One-pole low-pass with a per-sample cutoff list."""
        k = -TAU / self.sr
        e = _exp
        al = [1.0 - e(k * f) for f in fcs]
        y = 0.0
        if circular:
            m = min(len(x), 4000)
            for v, a in zip(x[-m:], al[-m:]):
                y += a * (v - y)
        return [(y := y + a * (v - y)) for v, a in zip(x, al)]

    def reson(self, x, f, bw, warm=None):
        """Two-pole resonator (band-pass) with unity gain at ``f``."""
        r = _exp(-math.pi * bw / self.sr)
        w = TAU * min(f, 0.49 * self.sr) / self.sr
        c1 = 2.0 * r * _cos(w)
        c2 = r * r
        g = (1.0 - r) * math.sqrt(max(1e-12, 1.0 - 2.0 * r * _cos(2.0 * w) + r * r))
        y1 = y2 = 0.0
        if warm:
            for v in warm:
                y = g * v + c1 * y1 - c2 * y2
                y2 = y1
                y1 = y
        out = []
        ap = out.append
        for v in x:
            y = g * v + c1 * y1 - c2 * y2
            ap(y)
            y2 = y1
            y1 = y
        return out

    def reson_tv(self, x, fs, bw, step=16):
        """Resonator whose centre follows the per-sample list ``fs`` (unity peak gain)."""
        n = len(x)
        r = _exp(-math.pi * bw / self.sr)
        c2 = r * r
        k = TAU / self.sr
        fc = fs[::step]
        c1c = [2.0 * r * _cos(k * f) for f in fc]
        gc = [(1.0 - r) * math.sqrt(max(1e-12, 1.0 - 2.0 * r * _cos(2.0 * k * f) + c2)) for f in fc]
        c1s = chain.from_iterable(map(repeat, c1c, repeat(step)))
        gs = chain.from_iterable(map(repeat, gc, repeat(step)))
        y1 = y2 = 0.0
        out = []
        ap = out.append
        for v, c1, g in zip(x, c1s, gs):
            y = g * v + c1 * y1 - c2 * y2
            ap(y)
            y2 = y1
            y1 = y
        return out

    def reson_circ(self, x, f, bw):
        m = min(len(x), int(8.0 * self.sr / (math.pi * bw)) + 1)
        return self.reson(x, f, bw, warm=x[-m:])

    def formants(self, x, specs, dry=0.0):
        """Parallel resonator bank: ``specs`` = ``(freq, bandwidth, gain)``."""
        out = [dry * v for v in x] if dry else [0.0] * len(x)
        for f, bw, g in specs:
            if f < 0.48 * self.sr:
                r = self.reson(x, f, bw)
                out = [a + g * b for a, b in zip(out, r)]
        return out

    def svf_bp(self, x, fcs, q, circular=False):
        """Chamberlin state-variable band-pass, per-sample cutoff list."""
        k = math.pi / self.sr
        s = _sin
        fl = [min(1.2, 2.0 * s(k * f)) for f in fcs]
        lo = bp = 0.0
        if circular:
            m = min(len(x), 3000)
            for v, f in zip(x[-m:], fl[-m:]):
                lo += f * bp
                bp += f * (v - lo - q * bp)
        out = []
        ap = out.append
        for v, f in zip(x, fl):
            lo += f * bp
            bp += f * (v - lo - q * bp)
            ap(bp)
        return out

    # -- envelopes & arithmetic ------------------------------------------------

    def env_exp(self, n, tau, amp=1.0):
        """``amp * exp(-t / tau)`` over ``n`` samples."""
        d = _exp(-1.0 / (tau * self.sr))
        return list(accumulate(repeat(d, n - 1), operator.mul, initial=amp))

    def env_lin(self, pts, n):
        """Piecewise-linear envelope through ``(seconds, value)`` points."""
        sr = self.sr
        out = []
        for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
            i0 = int(t0 * sr + 0.5)
            m = int(t1 * sr + 0.5) - i0
            if m > 0:
                st = (v1 - v0) / m
                out.extend([v0 + st * i for i in range(m)])
        if len(out) < n:
            out.extend([pts[-1][1]] * (n - len(out)))
        else:
            del out[n:]
        return out

    def curve(self, n, step, lo, hi, circular=False):
        """Random values every ``step`` seconds, linearly interpolated."""
        rng = self.rng
        K = max(1, int(round(n / max(2.0, step * self.sr))))
        vals = [rng.uniform(lo, hi) for _ in range(K + 1)]
        if circular:
            vals[K] = vals[0]
        out = []
        for j in range(K):
            a = (j * n) // K
            m = ((j + 1) * n) // K - a
            v0 = vals[j]
            st = (vals[j + 1] - v0) / m
            out.extend([v0 + st * i for i in range(m)])
        return out

    @staticmethod
    def mul(a, b):
        return list(map(_mul, a, b))

    @staticmethod
    def add_at(dst, src, off=0, g=1.0):
        """Mix ``src * g`` into ``dst`` starting at sample ``off`` (clipped to dst)."""
        if off < 0:
            src = src[-off:]
            off = 0
        end = min(len(dst), off + len(src))
        if end <= off:
            return dst
        if g == 1.0:
            dst[off:end] = map(_add, dst[off:end], src)
        else:
            dst[off:end] = [a + g * b for a, b in zip(dst[off:end], src)]
        return dst

    @staticmethod
    def fold(buf, n):
        """Wrap a linear buffer onto a loop of ``n`` samples (circular mix)."""
        out = buf[:n]
        for s in range(n, len(buf), n):
            seg = buf[s:s + n]
            out[:len(seg)] = [a + b for a, b in zip(out, seg)]
        return out

    @staticmethod
    def rotate(x, s):
        """Circular delay by ``s`` samples."""
        s %= len(x)
        return x[-s:] + x[:-s] if s else x[:]

    @staticmethod
    def drive(x, amount):
        """tanh saturation, ``amount`` relative to the signal's own peak."""
        k = amount / (_peak(x) or 1.0)
        t = _tanh
        return [t(k * v) for v in x]

    def finish(self, x, fin=0.0015, fout=0.012):
        """Short fade-in/out so one-shots start and end exactly at zero."""
        n = len(x)
        a = min(n // 2, self.ns(fin))
        b = min(n // 2, self.ns(fout))
        for i in range(a):
            x[i] *= i / a
        for i in range(b):
            x[n - 1 - i] *= i / b
        return x

    # -- building blocks -------------------------------------------------------

    def damped(self, f, tau, n, amp=1.0):
        """Exponentially decaying sine (starts at zero phase)."""
        r = _exp(-1.0 / (tau * self.sr))
        w = TAU * f / self.sr
        c = complex(r * _cos(w), r * _sin(w))
        z = complex(amp * _cos(-w), amp * _sin(-w))
        return [(z := z * c).imag for _ in repeat(None, n)]

    def modes(self, specs, dur, amp=1.0):
        """Sum of damped sines ``(freq, amp, tau)``: struck metal, bells, tines."""
        n = self.ns(dur)
        out = [0.0] * n
        top = 0.45 * self.sr
        for f, a, tau in specs:
            if 0.0 < f < top:
                m = min(n, self.ns(5.0 * tau))
                seg = self.damped(f, tau, m, a * amp)
                out[:m] = [o + s for o, s in zip(out, seg)]
        return out

    def thud(self, dur, f0, f1, tau, amp=1.0, glide=None):
        """Sine with an exponential pitch drop: kicks, booms, footfalls."""
        n = self.ns(dur)
        g = self.env_exp(n, glide or tau * 0.5)
        k = TAU / self.sr
        df = f0 - f1
        ph = accumulate([(f1 + df * e) * k for e in g])
        s = _sin
        return [e * s(p) for e, p in zip(self.env_exp(n, tau, amp), ph)]

    def burst(self, dur, tau, amp=1.0, lp=None, hp=None):
        """Decaying noise burst, optionally filtered."""
        n = self.ns(dur)
        x = self.mul(self.noise(n), self.env_exp(n, tau, amp))
        if lp:
            x = self.lp(x, lp)
        if hp:
            x = self.hp(x, hp)
        return x

    def _fbcomb(self, x, D, g):
        y = x[:D]
        for i in range(D, len(x), D):
            y.extend([a + g * b for a, b in zip(x[i:i + D], y[i - D:i])])
        return y

    def _allpass(self, x, D, g):
        y = [-g * v for v in x[:D]]
        for i in range(D, len(x), D):
            y.extend([b + g * (c - a) for a, b, c in zip(x[i:i + D], x[i - D:i], y[i - D:i])])
        return y

    def reverb(self, x, **kw):
        """Small Schroeder reverb (block-processed feedback combs + allpasses)."""
        g = self.reverb_g(x, **kw)
        try:
            while True:
                next(g)
        except StopIteration as stop:
            return stop.value

    def reverb_g(self, x, mix=0.25, t60=1.0, size=1.0, tail=0.0, combs=4, lpf=3500.0, aps=2):
        """Generator version of ``reverb`` (yields between stages)."""
        sr = self.sr
        src = x + [0.0] * self.ns(tail) if tail > 0 else x
        n = len(src)
        wet = [0.0] * n
        for ms in (29.7, 37.1, 41.1, 43.7)[:combs]:
            D = max(1, int(ms * size * sr / 1000.0))
            if D >= n:
                continue
            g = 10.0 ** (-3.0 * D / (t60 * sr))
            y = self._fbcomb(src, D, g)
            wet = list(map(_add, wet, y))
            yield
        for ms in (5.0, 1.7)[:aps]:
            D = max(1, int(ms * sr / 1000.0))
            if D < n:
                wet = self._allpass(wet, D, 0.7)
        yield
        wet = self.lp(wet, lpf)
        k = mix / combs
        return [a + k * b for a, b in zip(src, wet)]

    def mbnote(self, f, amp=1.0):
        """A music-box tine: bright ping, slow beating from a detuned twin."""
        tau = max(0.18, min(0.45, 0.32 * (600.0 / f) ** 0.5))
        n = self.ns(3.4 * tau)
        r1 = _exp(-1.0 / (tau * self.sr))
        r2 = _exp(-1.0 / (0.9 * tau * self.sr))
        w1 = TAU * f / self.sr
        w2 = w1 * 1.0046
        c1 = complex(r1 * _cos(w1), r1 * _sin(w1))
        c2 = complex(r2 * _cos(w2), r2 * _sin(w2))
        z1 = complex(0.0, -amp)
        z2 = complex(0.0, -0.32 * amp)
        # Fundamental and twin in one pass (real part starts at zero).
        out = [(z1 := z1 * c1).real + (z2 := z2 * c2).real for _ in repeat(None, n)]
        hi = self.modes(((2.0 * f, 0.13, 0.32 * tau), (5.4 * f, 0.3, 0.035),
                         (8.93 * f, 0.08, 0.016)), min(0.5, 3.4 * tau), amp)
        self.add_at(out, hi)
        self.add_at(out, self.burst(0.006, 0.0008, 0.18 * amp, hp=3000.0))
        m = min(n, self.ns(0.04))  # fade the (already quiet) tail to zero
        for i in range(m):
            out[n - 1 - i] *= i / m
        return out

    # ======================================================================
    # Loops
    # ======================================================================

    def r_fan(self):
        S = self.sub(2)
        N = S.ns(2.0)
        s = _sin
        w1, w2, w3, w4 = (S.lw(f, N) for f in (60.0, 120.0, 180.0, 240.0))
        hum = [0.20 * s(w1 * i) + 0.35 * s(w2 * i + 0.7) + 0.10 * s(w3 * i + 1.3)
               + 0.05 * s(w4 * i + 2.0) for i in range(N)]
        whirr = S.lp_circ(S.lp_circ(S.noise(N), 650.0), 650.0)
        swish = S.hp_circ(S.lp_circ(S.noise(N), 3500.0), 1200.0)
        wb = S.lw(10.0, N)
        ws = S.lw(0.5, N)
        mod = [(1.0 + 0.42 * s(wb * i) + 0.12 * s(2.0 * wb * i + 1.1)) * (1.0 + 0.08 * s(ws * i))
               for i in range(N)]
        out = [0.45 * h + (2.6 * a + 0.55 * b) * m for h, a, b, m in zip(hum, whirr, swish, mod)]
        return out, 2

    def r_buzz(self):
        S = self
        rng = S.rng
        N = S.ns(1.0)
        tone = S.drive(S.losc(S.buzz_tab(), 60.0, N), 1.8)
        env = [1.0] * N
        for _ in range(7):
            st = rng.randrange(N)
            m = S.ns(rng.uniform(0.008, 0.05))
            depth = rng.uniform(0.3, 0.75)
            for i in range(m):
                j = (st + i) % N
                env[j] *= 1.0 - depth * 0.5 * (1.0 - _cos(TAU * i / m))
        sizzle = S.hp_circ(S.noise(N), 3000.0)
        out = [0.75 * t * e + 0.35 * z * abs(t) * e for t, e, z in zip(tone, env, sizzle)]
        for _ in range(12):
            st = rng.randrange(N)
            pop = S.burst(0.006, rng.uniform(0.0005, 0.0018), rng.uniform(0.3, 0.8), hp=1500.0)
            for i, v in enumerate(pop):
                out[(st + i) % N] += v
        return out, 1

    def r_static(self):
        S = self
        rng = S.rng
        N = S.ns(2.0)
        hiss = S.lp_circ(S.noise(N), 6500.0)
        flutter = S.curve(N, 0.03, 0.45, 1.0, circular=True)
        w = S.lw(60.0, N)
        s = _sin
        out = [h * f + 0.025 * s(w * i) for i, (h, f) in enumerate(zip(hiss, flutter))]
        for _ in range(70):
            i = rng.randrange(N)
            a = rng.choice((-1.0, 1.0)) * rng.uniform(0.3, 0.9)
            out[i] += a
            out[(i + 1) % N] -= 0.5 * a
            out[(i + 2) % N] += 0.2 * a
        return out, 1

    def r_ambience(self):
        S = self.sub(4)
        N = S.ns(8.0)
        s = _sin
        soft = S.table(((1, 1.0, 0.0), (2, 0.35, 0.5), (3, 0.18, 1.0), (4, 0.08, 0.3),
                        (5, 0.05, 1.7)))
        drone = [0.0] * N
        for f, a, c, ph in ((55.0, 0.30, 1, 0.0), (55.375, 0.22, 2, 1.0), (77.75, 0.11, 3, 2.0),
                            (110.125, 0.10, 1, 3.0), (164.875, 0.06, 2, 4.5)):
            o = S.losc(soft, f, N, ph / TAU)
            drone = list(map(_add, drone, map(_mul, o, S.lfo(N, c, ph, 0.1 * a, a))))
        yield
        fc = S.lfo(N, 1, 0.0, 180.0, 680.0, 8)
        fc = list(map(_add, fc, S.lfo(N, 3, 1.0, -90.0, 90.0, 8)))
        wind = S.svf_bp(S.noise(N), fc, 0.6, circular=True)
        wind = list(map(_mul, wind, S.lfo(N, 2, 2.0, 0.1, 0.5)))
        rumble = S.lp_circ(S.lp_circ(S.noise(N, 1.6), 70.0), 70.0)
        whine = list(map(_mul, map(_add, S.lsine(1318.5, N), S.lsine(1325.0, N)),
                         S.lfo(N, 0.5, -0.5 * math.pi, 0.0, 0.012)))
        yield
        out = list(map(_add, map(_add, drone, wind), map(_add, rumble, whine)))
        return (out, S.rotate(out, S.ns(2.9))), 4

    def r_menu(self):
        S = self.sub(2)
        rng = S.rng
        lo = self.sub(4)
        N4 = lo.ns(10.0)
        N = 2 * N4
        s = _sin
        pad = lo.table(tuple((k, 1.0 / k ** 1.5, 0.37 * k) for k in range(1, lo.nh(300.0, 10) + 1)))
        drone = [0.0] * N4
        for f, a, c, ph in ((36.7, 0.12, 1, 0.0), (73.4, 0.30, 2, 1.0), (73.6, 0.22, 3, 2.0),
                            (110.0, 0.16, 1, 2.5), (146.8, 0.08, 2, 0.6), (174.6, 0.08, 3, 1.7),
                            (207.7, 0.05, 1, 3.9)):
            o = lo.losc(pad, f, N4, ph / TAU)
            drone = list(map(_add, drone, map(_mul, o, lo.lfo(N4, c, ph, 0.0, a))))
            if f == 110.0:
                yield
        drone = lo.lp_var(drone, lo.lfo(N4, 1, 1.0, 260.0, 1100.0, 8), circular=True)
        yield
        wind = lo.svf_bp(lo.noise(N4, 0.22), lo.lfo(N4, 2, 0.0, 180.0, 480.0, 8), 0.7,
                         circular=True)
        drone = _upsample(list(map(_add, drone, wind)), 2, True)
        yield
        # Sparse, detuned music-box notes with a long echo.
        buf = [0.0] * (N + S.ns(3.5))
        for t, m in ((0.5, 74), (1.9, 77), (3.1, 81), (4.4, 80), (6.0, 74), (7.2, 69), (8.4, 73)):
            f = _midi(m) * 2.0 ** ((-30.0 + rng.uniform(-12.0, 12.0)) / 1200.0)
            S.add_at(buf, S.mbnote(f), S.ns(t), rng.uniform(0.5, 0.8))
        buf = S._fbcomb(buf, S.ns(0.36), 0.45)
        notes = S.fold(buf, N)
        yield
        pn = 0.11 * _peak(drone) / (_peak(notes) or 1.0)
        notes = [pn * v for v in notes]
        L = list(map(_add, drone, notes))
        R = list(map(_add, S.rotate(drone, S.ns(1.7)), S.rotate(notes, S.ns(0.021))))
        return (L, R), 2

    def r_kitchen(self):
        S = self.sub(2)  # heard through a camera microphone: band-limited anyway
        rng = S.rng
        T = 4.0
        N = S.ns(T)
        buf = [0.0] * (N + S.ns(2.0))
        pot = ((1.0, 1.0, 1.0), (1.59, 0.8, 0.8), (2.14, 0.6, 0.62), (2.83, 0.45, 0.5),
               (3.61, 0.3, 0.38), (4.53, 0.2, 0.28))
        for i in range(7):
            t = (i + rng.uniform(0.0, 0.8)) * T / 7
            f = rng.uniform(230.0, 900.0)
            tau = rng.uniform(0.15, 0.38)
            g = rng.uniform(0.35, 1.0)
            hit = S.modes([(f * r, a * rng.uniform(0.6, 1.2), tau * d) for r, a, d in pot], 4.5 * tau)
            S.add_at(buf, hit, S.ns(t), g)
            S.add_at(buf, S.burst(0.03, 0.004, hp=800.0), S.ns(t), 0.7 * g)
            if i % 3 == 2:
                yield
        for _ in range(3):  # cutlery jingling
            t0 = rng.uniform(0.0, T)
            for _ in range(rng.randint(4, 7)):
                t0 += rng.uniform(0.02, 0.07)
                f = rng.uniform(1900.0, 4300.0)
                clink = S.modes(((f, 1.0, 0.05), (f * 1.48, 0.6, 0.035), (f * 2.1, 0.4, 0.02)), 0.25)
                S.add_at(buf, clink, S.ns(t0) % N, rng.uniform(0.12, 0.3))
        for _ in range(4):  # rummaging
            t0 = rng.uniform(0.0, T)
            m = S.ns(rng.uniform(0.15, 0.45))
            sw = S.reson(S.noise(m), rng.uniform(700.0, 2000.0), 1200.0)
            S.add_at(buf, [v * _sin(math.pi * i / m) ** 2 for i, v in enumerate(sw)], S.ns(t0),
                     rng.uniform(0.6, 1.0))
        for _ in range(2):  # something set down
            i0 = S.ns(rng.uniform(0.0, T))
            S.add_at(buf, S.thud(0.2, 160.0, 90.0, 0.04), i0, 0.5)
            S.add_at(buf, S.burst(0.05, 0.01, lp=900.0), i0, 1.2)
        yield
        buf = yield from S.reverb_g(buf, mix=0.35, t60=0.6, size=0.7, combs=2, aps=1)
        out = S.lp_circ(S.drive(S.fold(buf, N), 1.6), 4500.0)
        return out, 2

    # Toreador March refrain (Bizet, public domain), F major, (MIDI, beats).
    _TOREADOR = (
        (72, 1.5), (74, 0.5), (72, 1.0), (69, 1.0),
        (69, 1.0), (69, 0.5), (67, 0.5), (69, 0.5), (70, 0.5), (69, 1.0),
        (70, 1.5), (67, 0.5), (72, 1.0), (69, 1.0),
        (65, 1.5), (62, 0.5), (67, 1.0), (60, 1.0),
        (72, 1.5), (74, 0.5), (72, 1.0), (69, 1.0),
        (69, 1.0), (69, 0.5), (67, 0.5), (69, 0.5), (70, 0.5), (69, 1.0),
        (70, 1.5), (67, 0.5), (72, 1.0), (69, 1.0),
        (65, 3.0), (None, 1.0),
    )
    # Accompaniment: (beat, MIDI) per bar; bars 5-7 repeat bars 1-3.
    _TOREADOR_ACC = (
        ((0, 65), (2, 69)), ((0, 65), (2, 72)), ((0, 60), (2, 70)), ((0, 58), (2, 60)),
        ((0, 65), (2, 69)), ((0, 65), (2, 72)), ((0, 60), (2, 70)),
        ((0, 65), (1, 69), (2, 72), (3, 77)),
    )

    def r_musicbox(self):
        S = self
        rng = S.rng
        beat = 0.42
        N = S.ns(32 * beat)
        buf = [0.0] * (N + S.ns(2.0))
        tune = {}
        cache = {}

        def note(m, amp=1.0):
            if (m, amp) not in cache:
                if m not in tune:  # every tine is out of tune in its own way
                    tune[m] = 2.0 ** ((-22.0 + rng.uniform(-16.0, 16.0)) / 1200.0)
                cache[m, amp] = S.mbnote(_midi(m) * tune[m], amp)
            return cache[m, amp]

        pos = 0.0  # in beats (multiples of 0.5, exact in binary)
        for k, (m, b) in enumerate(self._TOREADOR):
            if m is not None:
                jit = 0.0 if k == 0 else rng.uniform(-0.012, 0.012)
                S.add_at(buf, note(m + 12, 1.0 if pos % 4 == 0 else 0.85),
                         S.ns(0.015 + pos * beat + jit))
            pos += b
            if k % 8 == 7:
                yield
        for bar, hits in enumerate(self._TOREADOR_ACC):
            for bt, m in hits:
                S.add_at(buf, note(m, 0.36),
                         S.ns(0.015 + (4 * bar + bt) * beat + rng.uniform(0.0, 0.015)))
        yield
        buf = yield from S.reverb_g(buf, mix=0.3, t60=0.9, size=0.8, combs=2, aps=1, lpf=5000.0)
        out = S.fold(buf, N)
        return out, 1

    # ======================================================================
    # One-shots
    # ======================================================================

    def r_click(self):
        S = self
        x = [0.0] * S.ns(0.07)
        S.add_at(x, S.burst(0.015, 0.0012, 0.9, hp=2500.0))
        S.add_at(x, S.modes(((2450.0, 0.5, 0.006), (4100.0, 0.3, 0.004), (1300.0, 0.25, 0.008)), 0.05))
        S.add_at(x, S.thud(0.05, 200.0, 140.0, 0.008, 0.3))
        return S.finish(x, 0.0005), 1

    def r_error(self):
        S = self.sub(2)
        n = S.ns(0.42)
        sq = S.square_tab(S.nh(100.0, 40))
        a = S.osc(sq, 92.0, n)
        b = S.osc(sq, 97.5, n, ph=0.3)
        x = S.lp(S.lp([u + v for u, v in zip(a, b)], 1300.0), 2000.0)
        x = S.drive(S.mul(x, S.env_lin(((0, 0), (0.01, 1), (0.30, 0.85), (0.38, 0)), n)), 2.0)
        S.add_at(x, S.thud(0.1, 140.0, 80.0, 0.03, 0.5))
        return S.finish(x), 2

    def r_door(self):
        S = self
        n = S.ns(0.55)
        x = [0.0] * n
        t0 = 0.07
        m = S.ns(t0)
        slide = S.reson(S.noise(m), 900.0, 900.0)
        S.add_at(x, [v * 0.35 * i / m for i, v in enumerate(slide)])
        ring = S.modes(((148.0, 0.5, 0.25), (337.0, 0.45, 0.22), (561.0, 0.4, 0.18),
                        (829.0, 0.35, 0.15), (1187.0, 0.3, 0.12), (1653.0, 0.22, 0.09),
                        (2213.0, 0.15, 0.07), (2950.0, 0.1, 0.05)), 0.5)
        for dt, g in ((0.0, 1.0), (0.075, 0.35)):
            i0 = S.ns(t0 + dt)
            S.add_at(x, S.thud(0.4, 110.0, 48.0, 0.09), i0, g)
            S.add_at(x, S.burst(0.08, 0.012, lp=3000.0), i0, 2.0 * g)
            S.add_at(x, ring, i0, 0.6 * g)
        x = S.drive(x, 1.3)
        return S.finish(x, 0.003, 0.04), 1

    def _cam(self, up):
        S = self
        n = S.ns(0.34)
        wt = 0.21 if up else 0.19
        m = S.ns(wt)
        f = S.env_lin(((0, 210.0), (wt, 560.0)) if up else ((0, 520.0), (wt, 190.0)), m)
        whir = S.lp(S.osc(S.saw_tab(S.nh(600.0)), f), 2200.0)
        nzb = S.reson(S.noise(m), 1600.0 if up else 1200.0, 1400.0)
        env = S.env_lin(((0, 0), (wt * 0.75, 1.0), (wt - 0.01, 0.7), (wt, 0)), m)
        x = [0.0] * n
        S.add_at(x, [(0.35 * a + 0.9 * b) * e for a, b, e in zip(whir, nzb, env)])
        i0 = S.ns(wt)
        k = 1.0 if up else 0.85
        S.add_at(x, S.burst(0.03, 0.003, hp=1200.0), i0, 0.8 * k)
        S.add_at(x, S.modes(((1150.0 * k, 0.45, 0.025), (2330.0 * k, 0.3, 0.018),
                             (3650.0 * k, 0.2, 0.012), (480.0 * k, 0.3, 0.03)), 0.15), i0, k)
        S.add_at(x, S.thud(0.08, 170.0, 110.0, 0.015), i0, 0.35 * k)
        return S.finish(x), 1

    def r_cam_up(self):
        return self._cam(True)

    def r_cam_down(self):
        return self._cam(False)

    def r_blip(self):
        S = self
        n = S.ns(0.12)
        m = S.ns(0.06)
        tone = [_tanh(2.5 * v) for v in S.sine(1240.0, m)]
        env = S.mul(S.env_exp(m, 0.02), S.env_lin(((0, 0), (0.002, 1)), m))
        stat = S.mul(S.hp(S.noise(n), 1800.0), S.env_exp(n, 0.035))
        stat = S.mul(stat, S.curve(n, 0.004, 0.0, 1.0))
        x = [0.8 * v for v in stat]
        S.add_at(x, S.mul(tone, env), 0, 0.5)
        return S.finish(x), 1

    def r_static_burst(self):
        S = self
        rng = S.rng
        n = S.ns(0.85)
        nz = S.noise(n)
        body = S.lp(S.noise(n), 1400.0)
        chop = S.curve(n, 0.018, 0.25, 1.0)
        hum = S.osc(S.buzz_tab(), 60.0, n)
        x = [(a + 1.6 * b) * c + 0.22 * h for a, b, c, h in zip(nz, body, chop, hum)]
        for _ in range(60):
            x[rng.randrange(n)] += rng.choice((-1.0, 1.0)) * rng.uniform(0.6, 1.4)
        held = [v for v in x[::3] for _ in (0, 1, 2)]
        x = [0.5 * a + 0.5 * b for a, b in zip(x, held)]
        x = S.mul(x, S.env_lin(((0, 0), (0.006, 1), (0.5, 0.9), (0.85, 0)), n))
        return S.finish(x), 1

    def _shriek(self, n, f0, voices, noise_bands, formants, drive, am, rm):
        """Detuned oscillator stack + rasp, roughened, formant-shaped, saturated."""
        S = self
        sr = S.sr
        mix = [0.0] * n
        for k, (ratio, amp, tab, ph) in enumerate(voices):
            v = S.osc(tab, f0, ph=ph, ratio=ratio)
            mix = [a + amp * b for a, b in zip(mix, v)]
            if k == 3:
                yield
        yield
        nz = S.noise(n)
        for f, bw, g in noise_bands:
            r = S.reson(nz, f, bw)
            mix = [a + g * b for a, b in zip(mix, r)]
        (af, ad), (rf, rd) = am, rm
        wa = TAU * af / sr
        wr = TAU * rf / sr
        s = _sin
        mix = [v * (1.0 - ad + ad * s(wa * i)) * (1.0 - rd + rd * s(wr * i))
               for i, v in enumerate(mix)]
        yield
        out = S.formants(mix, formants, dry=0.35)
        return S.drive(out, drive)

    def r_scream(self):
        S = self
        sr = S.sr
        dur = 1.55
        n = S.ns(dur)
        base = S.env_lin(((0, 470.0), (0.045, 780.0), (0.2, 860.0), (0.5, 800.0), (0.8, 880.0),
                          (1.15, 830.0), (dur, 640.0)), n)
        jit = S.curve(n, 0.035, -0.04, 0.04)
        w1 = TAU * 23.0 / sr
        w2 = TAU * 7.3 / sr
        s = _sin
        f0 = [b * (1.0 + j + 0.065 * s(w1 * i) + 0.03 * s(w2 * i + 1.0))
              for i, (b, j) in enumerate(zip(base, jit))]
        sa = S.saw_tab(S.nh(1000.0))
        sb = S.saw_tab(S.nh(2000.0))
        sq = S.square_tab(S.nh(500.0))
        voices = ((1.0, 1.0, sa, 0.0), (1.006, 0.8, sa, 0.3), (0.994, 0.7, sa, 0.6),
                  (1.335, 0.45, sa, 0.2), (1.414, 0.4, sa, 0.9), (2.013, 0.35, sb, 0.1),
                  (0.5, 0.55, sq, 0.5))
        x = yield from S._shriek(n, f0, voices, ((2900.0, 2000.0, 1.6), (1200.0, 700.0, 0.8)),
                      ((950.0, 450.0, 0.9), (2600.0, 900.0, 1.3), (3900.0, 1500.0, 0.7)),
                      10.0, (61.0, 0.35), (157.0, 0.35))
        yield
        held = [v for v in x[::2] for _ in (0, 1)]
        x = [0.65 * a + 0.35 * b for a, b in zip(x, held)]
        env = S.env_lin(((0, 0), (0.004, 1.0), (0.1, 1.0), (1.1, 0.92), (1.3, 0.55),
                         (1.45, 0.2), (dur, 0.0)), n)
        flutter = S.curve(n, 0.03, 0.82, 1.0)
        x = [v * e * f for v, e, f in zip(x, env, flutter)]
        S.add_at(x, S.thud(0.3, 110.0, 45.0, 0.08), 0, 0.6)
        S.add_at(x, S.burst(0.06, 0.01, lp=3000.0), 0, 1.4)
        t = _tanh
        x = S.hp([t(2.0 * v) for v in x], 90.0)  # absolute limiter: loud, dense
        return S.finish(x, 0.001, 0.02), 1

    def r_scream_gf(self):
        S = self.sub(2)  # low and dark: nothing worth keeping above 5 kHz
        sr = S.sr
        dur = 2.1
        n = S.ns(dur)
        base = S.env_lin(((0, 150.0), (0.06, 235.0), (0.3, 250.0), (0.9, 215.0), (1.5, 170.0),
                          (dur, 110.0)), n)
        jit = S.curve(n, 0.06, -0.03, 0.03)
        w1 = TAU * 6.1 / sr
        w2 = TAU * 29.0 / sr
        s = _sin
        f0 = [b * (1.0 + j + 0.05 * s(w1 * i) + 0.03 * s(w2 * i))
              for i, (b, j) in enumerate(zip(base, jit))]
        sa = S.saw_tab(S.nh(600.0))
        sq = S.square_tab(S.nh(300.0))
        voices = ((1.0, 1.0, sa, 0.0), (1.009, 0.8, sa, 0.4), (0.991, 0.7, sa, 0.8),
                  (0.5, 0.8, sq, 0.2), (0.333, 0.45, sa, 0.6), (1.5, 0.35, sa, 0.1),
                  (2.37, 0.25, sa, 0.7))
        x = yield from S._shriek(n, f0, voices, ((1500.0, 900.0, 1.0), (400.0, 300.0, 0.6)),
                      ((550.0, 200.0, 1.0), (1050.0, 300.0, 0.8), (2400.0, 600.0, 0.5)),
                      10.0, (37.0, 0.4), (47.0, 0.5))
        yield
        env = S.env_lin(((0, 0), (0.012, 1.0), (1.5, 0.9), (1.85, 0.45), (dur, 0.0)), n)
        x = S.mul(x, env)
        S.add_at(x, S.thud(0.6, 70.0, 32.0, 0.2), 0, 0.7)
        t = _tanh
        x = [t(1.8 * v) for v in x]
        x = yield from S.reverb_g(x, mix=0.3, t60=1.4, size=1.2, combs=3)
        x = S.hp(x, 45.0)
        return S.finish(x, 0.001, 0.05), 2

    def _step(self, x, t, amp, v=1.0, bright=1.0):
        """One heavy animatronic footfall mixed into ``x`` at ``t`` seconds."""
        S = self
        i0 = S.ns(t)
        S.add_at(x, S.thud(0.35, 85.0 * v, 42.0 * v, 0.075), i0, amp)
        S.add_at(x, S.burst(0.12, 0.03, lp=380.0 * bright), i0, 2.6 * amp)
        S.add_at(x, S.modes(((310.0 * v, 0.35, 0.05), (735.0 * v, 0.28, 0.04),
                             (1390.0 * v, 0.16, 0.03), (2210.0 * v, 0.08, 0.02)), 0.25),
                 i0 + S.ns(0.004), 0.6 * amp * bright)

    def r_footsteps(self):
        S = self.sub(2)
        n = S.ns(1.2)
        x = [0.0] * n
        sq = S.saw_tab(S.nh(2400.0))
        for k, t in enumerate((0.03, 0.43, 0.83)):
            S._step(x, t, 1.0 - 0.06 * k, 1.0 + 0.04 * (k % 2))
            if k < 2:  # servo whine as the leg swings
                m = S.ns(0.26)
                f = S.env_lin(((0, 900.0), (0.26, 1150.0)), m)
                w = S.osc(sq, f)
                S.add_at(x, [v * _sin(math.pi * i / m) ** 2 for i, v in enumerate(w)],
                         S.ns(t + 0.09), 0.05)
        x = S.hp(S.reverb(x, mix=0.18, t60=0.7, size=0.8, combs=2, aps=1)[:n], 30.0)
        return S.finish(x), 2

    def r_run(self):
        S = self.sub(2)
        n = S.ns(1.6)
        x = [0.0] * n
        t = 0.0
        k = 0
        while t < 1.45:
            p = t / 1.45
            S._step(x, t, 0.1 + 0.9 * p * p, 1.0 + 0.05 * (k % 2), 0.8 + 6.0 * p * p)
            t += 0.15 - 0.035 * p
            k += 1
        return S.finish(S.hp(x, 30.0), 0.001, 0.05), 2

    def r_bang(self):
        S = self
        rng = S.rng
        n = S.ns(1.55)
        x = [0.0] * n
        plate = S.modes(((117.0, 0.5, 0.35), (263.0, 0.55, 0.32), (409.0, 0.5, 0.3),
                         (587.0, 0.45, 0.26), (811.0, 0.4, 0.22), (1069.0, 0.3, 0.18),
                         (1433.0, 0.25, 0.15), (1877.0, 0.18, 0.12), (2491.0, 0.12, 0.09),
                         (3307.0, 0.07, 0.06)), 1.2)
        for t, a in ((0.02, 1.0), (0.48, 0.92), (0.93, 1.0)):
            i0 = S.ns(t)
            S.add_at(x, S.thud(0.5, 100.0, 42.0, 0.13), i0, a)
            S.add_at(x, S.burst(0.08, 0.012, lp=2600.0), i0, 2.2 * a)
            S.add_at(x, plate, i0, 0.8 * a)
            for j, dt in enumerate((0.03, 0.055, 0.085)):
                f = rng.uniform(1500.0, 1900.0)
                S.add_at(x, S.modes(((f, 0.3, 0.015), (f * 1.75, 0.2, 0.01)), 0.06),
                         i0 + S.ns(dt), a * 0.5 * 0.6 ** j)
        x = S.drive(x, 1.6)
        return S.finish(x, 0.001, 0.08), 1

    def r_laugh(self):
        S = self.sub(2)
        sr = S.sr
        rng = S.rng
        n = S.ns(1.45)
        f0 = [70.0] * n
        amp = [0.0] * n
        asp = [0.0] * n
        for t0, d, fa, fb in ((0.03, 0.30, 104.0, 82.0), (0.43, 0.30, 97.0, 75.0),
                              (0.83, 0.42, 90.0, 60.0)):
            i0 = S.ns(t0)
            m = S.ns(d)
            f0[i0:i0 + m] = [fa + (fb - fa) * (i / m) ** 0.7 for i in range(m)]
            amp[i0:i0 + m] = S.env_lin(((0, 0), (0.035, 0), (0.07, 1.0), (d - 0.08, 0.6), (d, 0)), m)
            asp[i0:i0 + m] = S.env_lin(((0, 0), (0.02, 0.6), (0.07, 0.15), (d - 0.05, 0.1), (d, 0)), m)
        jit = S.curve(n, 0.02, -0.02, 0.02)
        f0 = [f * (1.0 + j) for f, j in zip(f0, jit)]
        g = S.glottal_tab(S.nh(110.0, 40))
        src = S.osc(g, f0)
        sub = S.osc(S.square_tab(S.nh(60.0, 30)), f0, ratio=0.5, ph=rng.random())
        wa = TAU * 33.0 / sr
        s = _sin
        exc = [(a + 0.35 * b) * e * (0.8 + 0.2 * s(wa * i)) for i, (a, b, e) in
               enumerate(zip(src, sub, amp))]
        nz = S.noise(n)
        exc = [v + 0.5 * z * h for v, z, h in zip(exc, nz, asp)]
        y = S.formants(exc, ((260.0, 80.0, 0.4), (520.0, 120.0, 1.0), (900.0, 140.0, 0.9),
                             (2100.0, 220.0, 0.45), (2900.0, 260.0, 0.25)))
        yield
        y = S.hp(S.hp(S.drive(y, 2.2), 90.0), 90.0)
        y = S.reverb(y, mix=0.3, t60=0.9, size=1.3)
        return S.finish(y, 0.002, 0.03), 2

    def r_groan(self):
        S = self.sub(2)
        sr = S.sr
        dur = 2.1
        n = S.ns(dur)
        base = S.env_lin(((0, 58.0), (0.4, 64.0), (1.4, 58.0), (dur, 48.0)), n)
        jit = S.curve(n, 0.05, -0.015, 0.015)
        w = TAU * 3.6 / sr
        s = _sin
        f0 = [b * (1.0 + j + 0.025 * s(w * i)) for i, (b, j) in enumerate(zip(base, jit))]
        src = S.osc(S.glottal_tab(S.nh(70.0, 60)), f0)
        sub = S.osc(S.square_tab(S.nh(35.0, 40)), f0, ratio=0.5)
        exc = [a + 0.4 * b for a, b in zip(src, sub)]
        y = S.formants(exc, ((150.0, 60.0, 0.4), (320.0, 90.0, 1.0), (680.0, 120.0, 0.8),
                             (2300.0, 220.0, 0.3)))
        wg = TAU * 13.0 / sr
        grind = S.reson(S.noise(n), 450.0, 250.0)
        whine = S.osc(S.table(((1, 1.0, 0.0),)), S.env_lin(((0, 720.0), (dur, 640.0)), n))
        y = S.drive(y, 1.0)
        y = [v + 0.5 * gr * (0.5 + 0.5 * s(wg * i)) + 0.04 * wh
             for i, (v, gr, wh) in enumerate(zip(y, grind, whine))]
        yield
        y = S.hp(S.hp(S.drive(y, 2.0), 60.0), 60.0)
        y = S.mul(y, S.env_lin(((0, 0), (0.35, 1.0), (1.35, 0.85), (dur, 0.0)), n))
        y = S.reverb(y, mix=0.2, t60=1.0, combs=3)
        return S.finish(y), 2

    def r_chimes(self):
        S = self.sub(2)
        B = self.sub(4)
        sr = S.sr
        rng = S.rng
        dur = 5.2
        n = S.ns(dur)
        # Westminster-style clock chime, synthesised at a quarter rate.
        nb = n // 2
        bells_out = [0.0] * nb
        bells = {}
        for t, m in ((0.0, 68), (0.45, 66), (0.9, 64), (1.35, 59),
                     (2.0, 64), (2.45, 68), (2.9, 66), (3.35, 59)):
            if m not in bells:
                p = _midi(m)
                b = B.modes(((0.5 * p, 0.35, 0.9), (p, 0.5, 0.8), (1.19 * p, 0.35, 0.6),
                             (1.5 * p, 0.2, 0.5), (2.0 * p, 0.6, 0.5), (2.52 * p, 0.15, 0.3),
                             (3.0 * p, 0.2, 0.22), (4.07 * p, 0.08, 0.15)), 4.5)
                B.add_at(b, B.reson(B.burst(0.02, 0.0015), 1900.0, 1200.0), 0, 1.2)
                bells[m] = b
                yield
            B.add_at(bells_out, bells[m], B.ns(t), 0.9 if t < 3.3 else 1.0)
        out = _upsample(bells_out, 2, False)
        out.extend([0.0] * (n - len(out)))
        del out[n:]
        # Children cheering "yaaay!"
        # Shouting is a pressed, bright voice: flatter spectral tilt than speech.
        gt = S.table(tuple((k, 1.0 / k ** 0.6, 0.3 * k) for k in range(1, S.nh(600.0, 16) + 1)))
        k0 = S.ns(0.3)
        nk = S.ns(3.1)
        src = [0.0] * nk
        for v in range(8):
            t0 = rng.uniform(0.05, 0.45)
            d = rng.uniform(1.3, 2.1)
            p = rng.uniform(290.0, 430.0)
            m = S.ns(d)
            fl = S.env_lin(((0, p * 0.9), (0.22, p * 1.25), (d - 0.5, p * 1.18), (d, p * 0.85)), m)
            fl = list(map(_mul, fl, S.lfo(m, d * rng.uniform(5.0, 7.0), rng.random() * TAU,
                                          0.975, 1.025, 16)))
            sig = S.osc(gt, fl, ph=rng.random())
            env = S.env_lin(((0, 0), (0.08, 1.0), (d - 0.45, 0.85), (d, 0)), m)
            nz = S.noise(m, 0.25)
            i0 = S.ns(t0)
            seg = src[i0:i0 + m]
            src[i0:i0 + m] = map(_add, seg, map(_mul, map(_add, sig, nz), env))
            if v % 3 == 2:
                yield
        yield
        va = S.formants(src, ((1000.0, 300.0, 1.0), (1700.0, 350.0, 1.2), (3000.0, 500.0, 0.9)))
        vi = S.formants(src, ((450.0, 200.0, 0.5), (2600.0, 400.0, 1.2), (3400.0, 500.0, 0.7)))
        wa = S.env_lin(((0, 0), (0.15, 0.2), (0.3, 1.0), (1.6, 1.0), (2.3, 0.2), (3.1, 0.2)), nk)
        crowd = S.mul(S.reson(S.noise(nk), 1300.0, 900.0),
                      S.env_lin(((0, 0), (0.2, 0), (0.5, 0.6), (1.9, 0.36), (2.7, 0)), nk))
        kids = S.hp([b + (a - b) * w + c for a, b, w, c in zip(va, vi, wa, crowd)], 600.0)
        yield
        S.add_at(out, kids, k0, 1.5 * _peak(out) / (_peak(kids) or 1.0))
        out = yield from S.reverb_g(out, mix=0.22, t60=1.6, size=1.3)
        return S.finish(out, 0.001, 0.4), 2

    def r_powerdown(self):
        S = self.sub(2)
        n = S.ns(2.6)
        x = [0.0] * n
        S.add_at(x, S.thud(0.6, 85.0, 40.0, 0.13))
        S.add_at(x, S.burst(0.1, 0.012, lp=4500.0), 0, 1.8)
        S.add_at(x, S.modes(((220.0, 0.4, 0.12), (517.0, 0.3, 0.09), (944.0, 0.25, 0.06),
                             (1530.0, 0.15, 0.04)), 0.6), 0, 0.5)
        S.add_at(x, S.burst(0.06, 0.015, hp=2500.0), 0, 0.6)
        x = S.drive(x, 1.5)
        i0 = S.ns(0.02)
        m = n - i0
        end = m / S.sr
        g = S.env_exp(m, 0.75)
        hum = S.lp(S.osc(S.buzz_tab(), [60.0 * (0.22 + 0.78 * e) for e in g]), 1600.0)
        amp = S.env_lin(((0, 0), (0.03, 0.9), (0.6, 0.7), (1.6, 0.32), (end, 0)), m)
        whine = S.osc(S.table(((1, 1.0, 0.0), (2, 0.3, 0.0))),
                      [150.0 + 1650.0 * e for e in S.env_exp(m, 0.55)])
        wamp = S.env_lin(((0, 0), (0.05, 0.2), (1.2, 0.07), (end, 0)), m)
        S.add_at(x, [h * a + w * b for h, a, w, b in zip(hum, amp, whine, wamp)], i0)
        return S.finish(x, 0.001, 0.05), 2

    def r_sting(self):
        S = self
        rng = S.rng
        sr = S.sr
        n = S.ns(0.85)
        saw = S.saw_tab(S.nh(450.0))
        x = [0.0] * n
        for f in (65.41, 69.30, 92.50, 130.81, 138.59, 185.0, 196.0, 277.18, 293.66, 415.3):
            v = S.osc(saw, f * (1.0 + rng.uniform(-0.004, 0.004)), n, ph=rng.random())
            x = [a + b for a, b in zip(x, v)]
        x = S.drive(x, 1.8)
        x = S.mul(x, S.env_lin(((0, 0), (0.004, 1.0), (0.06, 0.62), (0.3, 0.3), (0.6, 0.1),
                                (0.85, 0)), n))
        wv = TAU * 9.0 / sr
        s = _sin
        k = TAU / sr
        ph1 = accumulate(1760.0 * k * (1.0 + 0.015 * s(wv * i)) for i in range(n))
        ph2 = accumulate(1864.7 * k * (1.0 + 0.015 * s(wv * i + 1.0)) for i in range(n))
        e = S.env_exp(n, 0.18, 0.18)
        S.add_at(x, [(s(a) + s(b)) * g for a, b, g in zip(ph1, ph2, e)])
        S.add_at(x, S.burst(0.1, 0.02, hp=1500.0), 0, 0.6)
        S.add_at(x, S.thud(0.5, 60.0, 40.0, 0.18), 0, 0.7)
        x = S.drive(x, 1.6)
        return S.finish(x, 0.001, 0.03), 1

    def r_honk(self):
        S = self
        sr = S.sr
        n = S.ns(0.36)
        w = TAU * 18.0 / sr
        f = [v * (1.0 + 0.015 * _sin(w * i)) for i, v in enumerate(
            S.env_lin(((0, 300.0), (0.03, 415.0), (0.22, 405.0), (0.3, 370.0), (0.36, 360.0)), n))]
        src = S.osc(S.pulse_tab(S.nh(430.0), 0.28), f)
        y = S.formants(src, ((1250.0, 280.0, 1.0), (2600.0, 500.0, 0.6)), dry=0.3)
        y = S.mul(y, S.env_lin(((0, 0), (0.015, 1.0), (0.25, 0.9), (0.33, 0)), n))
        y = S.hp(S.drive(y, 2.2), 60.0)
        return S.finish(y), 1

    def r_ring(self):
        S = self
        n = S.ns(3.1)
        D = S.ns(0.05)

        def gong(f):
            g = S.modes(((f, 1.0, 0.32), (f * 2.32, 0.5, 0.2), (f * 4.25, 0.3, 0.12),
                         (f * 6.63, 0.15, 0.07)), 1.6)
            S.add_at(g, S.burst(0.004, 0.0007, hp=2000.0), 0, 0.5)
            return g

        strikes = 19
        x = [0.0] * n
        for f, off in ((1180.0, 0), (1430.0, D // 2)):
            g = gong(f)
            S.add_at(x, g, off)
            S.add_at(x, g, off + strikes * D, -1.0)
            yield
        # Feedback comb with gain 1 turns one strike into an evenly spaced
        # train of ``strikes`` strikes (the negative copy ends the train).
        x = S._fbcomb(x, D, 1.0)
        burst = x[:S.ns(1.55)]
        S.add_at(x, burst, S.ns(1.5))
        x = S.drive(x, 1.4)
        return S.finish(x, 0.001, 0.2), 1

    def r_deep(self):
        S = self.sub(2)
        n = S.ns(1.1)
        boom = S.thud(1.1, 72.0, 36.0, 0.38, glide=0.25)
        rumble = S.mul(S.lp(S.lp(S.noise(n), 140.0), 140.0), S.env_exp(n, 0.3, 3.0))
        x = [a + b for a, b in zip(boom, rumble)]
        S.add_at(x, S.thud(0.5, 170.0, 75.0, 0.12), 0, 0.45)
        whoomp = S.mul(S.reson(S.noise(n), 260.0, 300.0), S.env_exp(n, 0.22, 2.5))
        x = list(map(_add, x, whoomp))
        S.add_at(x, S.burst(0.08, 0.015, lp=1200.0), 0, 1.4)
        x = S.drive(x, 2.4)
        return S.finish(x, 0.002, 0.1), 2


    # -- speech-like babble (phone calls) ----------------------------------------

    # Male vowel formants F1-F3: a, e, i, o, u, ae, uh, er.
    _VOWELS = ((730, 1090, 2440), (530, 1840, 2480), (390, 1990, 2550), (570, 840, 2410),
               (440, 1020, 2240), (660, 1720, 2410), (520, 1190, 2390), (490, 1350, 1690))

    def _babble(self, T, f_mid, slow=1.0, fscale=1.0, lead=0.15, trail=0.35):
        """Indistinct speech: phrases of syllables with consonants and pauses.

        Pulse train through three moving formants plus consonant noise. The
        result is silent for ``lead``/``trail`` seconds at the ends.
        """
        S = self
        rng = S.rng
        n = S.ns(T)
        f0 = [f_mid] * n
        amp = [0.0] * n
        asp = [0.0] * n
        F = [[500.0 * fscale] * n, [1500.0 * fscale] * n, [2500.0 * fscale] * n]
        cons = [0.0] * n
        t = lead
        end = T - trail
        while t < end - 0.3:
            syls = []
            for k in range(rng.randint(3, 8)):
                d = rng.uniform(0.11, 0.23) * slow
                if t + d > end:
                    break
                syls.append((t, d))
                t += d
            if not syls:
                break
            p0, p1 = syls[0][0], t
            question = rng.random() < 0.25
            for k, (ts, d) in enumerate(syls):
                prog = (ts - p0) / max(1e-3, p1 - p0)
                last = k == len(syls) - 1
                i0 = S.ns(ts)
                m = S.ns(d)
                c = rng.choice("ptkssfmnlh--")
                cl = 0.0
                if c in "ptk":
                    cl = 0.035 * slow
                    fb = {"p": 900.0, "t": 3000.0, "k": 1900.0}[c] * fscale
                    b = S.reson(S.burst(0.025 * slow, 0.006 * slow), fb, 1200.0)
                    S.add_at(cons, b, i0 + S.ns(0.022 * slow), 0.6)
                elif c in "sf":
                    cl = 0.07 * slow
                    mm = S.ns(cl)
                    fr = S.reson(S.noise(mm), (3100.0 if c == "s" else 1900.0) * fscale, 1500.0)
                    S.add_at(cons, [v * _sin(math.pi * i / mm) ** 2 for i, v in enumerate(fr)],
                             i0, 0.3 if c == "s" else 0.15)
                elif c in "mnl":
                    cl = 0.05 * slow
                    mm = S.ns(cl)
                    f2 = {"m": 1100.0, "n": 1600.0, "l": 1000.0}[c] * fscale
                    F[0][i0:i0 + mm] = [280.0 * fscale] * mm
                    F[1][i0:i0 + mm] = [f2] * mm
                    amp[i0:i0 + mm] = [0.3] * mm
                elif c == "h":
                    cl = 0.05 * slow
                    mm = S.ns(cl)
                    asp[i0:i0 + mm] = [0.5] * mm
                v = rng.choice(self._VOWELS)
                vi = i0 + S.ns(cl)
                vm = max(1, m - S.ns(cl) - (S.ns(0.02) if c == "-" else 0))
                for j in range(3):
                    F[j][vi:vi + vm] = [v[j] * fscale * rng.uniform(0.95, 1.05)] * vm
                stress = rng.random() < 0.35
                a = (1.0 if stress else 0.7) * (1.0 - 0.3 * prog)
                amp[vi:vi + vm] = [a] * vm
                asp[vi:vi + vm] = [0.05] * vm
                fp = f_mid * (1.12 - 0.25 * prog) * (1.1 if stress else 1.0)
                if last:
                    fp *= 1.25 if question else 0.9
                f0[i0:i0 + m] = [fp] * m
            t += rng.uniform(0.25, 0.55) * slow
        yield
        # Smooth the control tracks: intonation, coarticulation, no clicks.
        f0 = list(map(_mul, S.lp(f0, 4.0, f0[0]), S.curve(n, 0.03, 0.98, 1.02)))
        amp = S.lp(amp, 30.0)
        asp = S.lp(asp, 60.0)
        F = [S.lp(tr, 12.0, tr[0]) for tr in F]
        src = S.osc(S.glottal_tab(S.nh(f_mid * 1.6, 40)), f0)
        src = [a * u + h * z for a, u, h, z in zip(amp, src, asp, S.noise(n))]
        yield
        out = list(cons)
        for tr, bw, g in zip(F, (90.0, 110.0, 160.0), (1.0, 0.7, 0.35)):
            out = list(map(_add, out, map(g.__mul__, S.reson_tv(src, tr, bw))))
            yield
        return out

    def r_voice(self):
        S = self.sub(2)
        T = 5.0
        N = S.ns(T)
        v = yield from S._babble(T, 118.0)
        g = 1.0 / (_peak(v) or 1.0)
        x = [a * g + h for a, h in zip(v, S.noise(N, 0.025))]
        yield
        # Telephone line: 300-3400 Hz, a touch of saturation (filters run
        # circularly so the loop stays seamless).
        x = S.hp_circ(S.hp_circ(x, 300.0), 300.0)
        x = S.lp_circ(S.lp_circ(x, 3400.0), 3400.0)
        return S.drive(x, 1.5), 2

    def r_garble(self):
        S = self.sub(4)  # muffled to ~2.7 kHz anyway
        rng = S.rng
        T = 4.0
        N = S.ns(T)
        a = yield from S._babble(T, 74.0, slow=1.6, fscale=0.85, lead=0.1, trail=0.2)
        yield
        b = yield from S._babble(T, 52.0, slow=2.1, fscale=0.7, lead=0.3, trail=0.1)
        yield
        ga = 1.0 / (_peak(a) or 1.0)
        gb = 0.8 / (_peak(b) or 1.0)
        w = S.lw(27.0, N)
        s = _sin
        x = [(ga * u + gb * v) * (0.55 + 0.45 * s(w * i)) for i, (u, v) in enumerate(zip(a, b))]
        x = S.drive(x, 3.0)
        # Reverse reverb: tails folded onto the loop, then the whole thing is
        # played backwards so every syllable swells in out of nowhere.
        x = yield from S.reverb_g(x, mix=0.6, t60=1.5, size=1.4, tail=1.5, combs=3, aps=1)
        x = S.fold(x, N)
        x.reverse()
        yield
        st = S.mul(S.hp_circ(S.noise(N), 1200.0), S.curve(N, 0.03, 0.05, 0.6, circular=True))
        for _ in range(40):
            i = rng.randrange(N)
            st[i] += rng.choice((-1.0, 1.0)) * rng.uniform(0.5, 1.2)
        gx = 1.0 / (_peak(x) or 1.0)
        x = [gx * u + 0.3 * v for u, v in zip(x, st)]
        x = S.hp_circ(S.lp_circ(x, 2600.0), 150.0)
        return S.drive(x, 1.5), 4


# name: (is_loop, target peak, recipe). Ordered roughly by when they are needed.
_RECIPES = (
    ("menu", True, 0.62, "r_menu"),
    ("static", True, 0.6, "r_static"),
    ("blip", False, 0.5, "r_blip"),
    ("click", False, 0.6, "r_click"),
    ("deep", False, 0.95, "r_deep"),
    ("fan", True, 0.5, "r_fan"),
    ("ambience", True, 0.55, "r_ambience"),
    ("ring", False, 0.7, "r_ring"),
    ("honk", False, 0.6, "r_honk"),
    ("door", False, 0.85, "r_door"),
    ("error", False, 0.6, "r_error"),
    ("cam_up", False, 0.6, "r_cam_up"),
    ("cam_down", False, 0.6, "r_cam_down"),
    ("buzz", True, 0.45, "r_buzz"),
    ("sting", False, 0.85, "r_sting"),
    ("kitchen", True, 0.6, "r_kitchen"),
    ("static_burst", False, 0.9, "r_static_burst"),
    ("footsteps", False, 0.85, "r_footsteps"),
    ("laugh", False, 0.9, "r_laugh"),
    ("groan", False, 0.9, "r_groan"),
    ("run", False, 0.9, "r_run"),
    ("bang", False, 0.95, "r_bang"),
    ("powerdown", False, 0.85, "r_powerdown"),
    ("musicbox", True, 0.6, "r_musicbox"),
    ("scream", False, 0.99, "r_scream"),
    ("scream_gf", False, 0.97, "r_scream_gf"),
    ("chimes", False, 0.8, "r_chimes"),
    ("voice", True, 0.5, "r_voice"),
    ("garble", True, 0.6, "r_garble"),
)

SOUND_NAMES = tuple(r[0] for r in _RECIPES)
LOOP_NAMES = tuple(r[0] for r in _RECIPES if r[1])


def _run_recipe(synth, name):
    """Generator: synthesise one sound; returns ``(data, div, is_loop, peak)``."""
    for nm, is_loop, peak, meth in _RECIPES:
        if nm == name:
            break
    else:
        raise KeyError(name)
    synth.reseed(name)
    res = getattr(synth, meth)()
    if isinstance(res, types.GeneratorType):
        res = yield from res
    data, div = res
    return data, div, is_loop, peak


def render(name, rate=22050):
    """Synthesise one sound without a mixer (testing / WAV export).

    Returns ``(left, right, is_loop)`` as float lists at ``rate`` Hz,
    normalised to the sound's target peak.
    """
    up = max(1, int(round(rate / 22050.0)))
    synth = _Synth(rate / up)
    gen = _run_recipe(synth, name)
    try:
        while True:
            next(gen)
    except StopIteration as stop:
        data, div, is_loop, peak = stop.value
    stereo = isinstance(data, tuple)
    L, R = data if stereo else (data, data)
    g = peak / (max(_peak(L), _peak(R)) or 1.0)
    L = [v * g for v in _upsample(L, up * div, is_loop)]
    R = [v * g for v in _upsample(R, up * div, is_loop)] if stereo else L
    return L, R, is_loop


def _clamp01(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 1.0
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

class SoundBank:
    """All of the game's sounds, plus simple channel management.

    One-shots (``play``) go to a pool of unreserved channels; named loops
    (``loop``) each get one of ``_LOOP_CHANNELS`` reserved channels, so a
    burst of one-shots can never cut off the fan or the music. If the mixer
    is not initialised every method is a silent no-op.
    """

    def __init__(self):
        self.sounds = {}
        self.progress = 0.0
        self.ready = False
        self.enabled = False
        self._spec = None
        self._loops = {}          # key -> [channel, name or None, volume, seq]
        self._loop_free = []
        self._pool = []
        self._started = []
        self._seq = 0
        self._paused = False
        self._setup()

    # -- setup -------------------------------------------------------------

    def _setup(self):
        try:
            spec = pygame.mixer.get_init()
        except Exception:
            spec = None
        if not spec:
            self.enabled = False
            return
        freq, size, chans = spec
        if abs(size) not in (8, 16, 32) or chans < 1 or freq < 4000:
            self.enabled = False
            return
        try:
            if pygame.mixer.get_num_channels() < _NUM_CHANNELS:
                pygame.mixer.set_num_channels(_NUM_CHANNELS)
            total = pygame.mixer.get_num_channels()
            pygame.mixer.set_reserved(_LOOP_CHANNELS)
            self._loop_free = [pygame.mixer.Channel(i) for i in range(_LOOP_CHANNELS)]
            self._pool = [pygame.mixer.Channel(i) for i in range(_LOOP_CHANNELS, total)]
        except pygame.error:
            self.enabled = False
            return
        self._started = [0] * len(self._pool)
        self._loops = {}
        self._spec = spec
        self._up = max(1, int(round(freq / 22050.0)))
        self._rate = freq / self._up
        self.enabled = bool(self._pool)

    def build(self):
        """Generator: synthesise every sound, yielding after each step.

        ``progress`` goes from 0 to 1 and ``ready`` becomes True at the end.
        Sounds become playable one by one as they finish.
        """
        if not self.enabled:
            self._setup()
        if not self.enabled:
            self.progress = 1.0
            self.ready = True
            return
        synth = _Synth(self._rate)
        total = len(_RECIPES)
        for name, _loop, _peak_, _meth in _RECIPES:
            if name in self.sounds:
                continue
            try:
                data, div, is_loop, peak = yield from _run_recipe(synth, name)
                yield
                snd = self._make_sound(data, div, is_loop, peak)
            except Exception as exc:  # a broken sound must never stop the game
                print("audio: could not build %r: %s" % (name, exc), file=sys.stderr)
                snd = None
            if snd is not None:
                self.sounds[name] = snd
            self.progress = min(1.0, (SOUND_NAMES.index(name) + 1) / total)
            yield
        self.progress = 1.0
        self.ready = True

    def _make_sound(self, data, div, is_loop, peak):
        freq, size, chans = self._spec
        if isinstance(data, tuple):
            L, R = data
        else:
            L = R = data
        if chans == 1:
            R = L  # mono mixer: use the left channel (no comb filtering)
        pk = _peak(L) if R is L else max(_peak(L), _peak(R))
        g = peak / pk if pk > 1e-12 else 0.0
        k = self._up * div
        a, tc, zero = _encode(_upsample(L, k, is_loop), g, size)
        if chans == 1:
            buf = a
        else:
            b = a if R is L else _encode(_upsample(R, k, is_loop), g, size)[0]
            buf = array(tc, [zero]) * (len(a) * chans)
            buf[0::chans] = a
            buf[1::chans] = b
        return pygame.mixer.Sound(buffer=buf.tobytes())

    # -- playback ------------------------------------------------------------

    def has(self, name):
        return name in self.sounds

    def _pool_channel(self):
        pool = self._pool
        started = self._started
        best = 0
        for i, ch in enumerate(pool):
            if not ch.get_busy():
                best = i
                break
            if started[i] < started[best]:
                best = i
        self._seq += 1
        started[best] = self._seq
        return pool[best]

    def play(self, name, volume=1.0, loops=0, pan=None):
        """Play a one-shot. ``pan`` (-1 left .. 1 right) is optional.

        Returns the Channel, or None if the sound is unknown/not built yet.
        """
        if not self.enabled:
            return None
        snd = self.sounds.get(name)
        if snd is None:
            return None
        try:
            ch = self._pool_channel()
            v = _clamp01(volume)
            ch.set_volume(v)
            ch.play(snd, loops)
            if pan:
                p = max(-1.0, min(1.0, float(pan)))
                ch.set_volume(v * min(1.0, 1.0 - p), v * min(1.0, 1.0 + p))
            else:
                ch.set_volume(v)
            return ch
        except pygame.error:
            return None

    def _loop_channel(self):
        if self._loop_free:
            return self._loop_free.pop(0)
        loops = self._loops
        for k, st in list(loops.items()):
            if st[1] is None or not st[0].get_busy():
                del loops[k]
                return st[0]
        k = min(loops, key=lambda kk: loops[kk][3])
        return loops.pop(k)[0]

    def loop(self, key, name, volume=1.0, fade_ms=0):
        """Make sure loop ``name`` is playing on the channel reserved for ``key``.

        Cheap to call every frame: if it is already playing only the volume
        is updated.
        """
        if not self.enabled:
            return
        snd = self.sounds.get(name)
        if snd is None:
            return
        try:
            v = _clamp01(volume)
            st = self._loops.get(key)
            if st is None:
                st = self._loops[key] = [self._loop_channel(), None, -1.0, 0]
            ch = st[0]
            if st[1] == name and ch.get_busy():
                if v != st[2]:
                    ch.set_volume(v)
                    st[2] = v
                return
            ch.set_volume(v)
            if fade_ms and fade_ms > 0:
                ch.play(snd, -1, 0, int(fade_ms))  # loops, maxtime, fade_ms
            else:
                ch.play(snd, -1)
                ch.set_volume(v)
            self._seq += 1
            st[1] = name
            st[2] = v
            st[3] = self._seq
            if self._paused:
                ch.pause()
        except pygame.error:
            pass

    def set_volume(self, key, volume):
        st = self._loops.get(key)
        if not self.enabled or st is None:
            return
        try:
            v = _clamp01(volume)
            st[0].set_volume(v)
            st[2] = v
        except pygame.error:
            pass

    def stop(self, key, fade_ms=0):
        st = self._loops.get(key)
        if not self.enabled or st is None or st[1] is None:
            return
        st[1] = None
        try:
            if fade_ms and fade_ms > 0:
                st[0].fadeout(int(fade_ms))
            else:
                st[0].stop()
        except pygame.error:
            pass

    def stop_all(self):
        if not self.enabled:
            return
        for st in self._loops.values():
            st[1] = None
        try:
            pygame.mixer.stop()
        except pygame.error:
            pass

    def pause(self):
        if not self.enabled:
            return
        self._paused = True
        try:
            pygame.mixer.pause()
        except pygame.error:
            pass

    def unpause(self):
        if not self.enabled:
            return
        self._paused = False
        try:
            pygame.mixer.unpause()
        except pygame.error:
            pass

    def is_looping(self, key):
        st = self._loops.get(key)
        if not self.enabled or st is None or st[1] is None:
            return False
        try:
            return bool(st[0].get_busy())
        except pygame.error:
            return False


def _encode(x, gain, size):
    """Floats -> array in the mixer's sample format: ``(array, typecode, silence)``."""
    if size == -16:
        s = gain * 32767.0
        return array("h", [int(v * s) for v in x]), "h", 0
    if abs(size) == 32:
        return array("f", [v * gain for v in x]), "f", 0.0
    if size == 16:
        s = gain * 32767.0
        return array("H", [int(v * s) + 32768 for v in x]), "H", 32768
    if size == 8:
        s = gain * 127.0
        return array("B", [int(v * s) + 128 for v in x]), "B", 128
    s = gain * 127.0
    return array("b", [int(v * s) for v in x]), "b", 0
