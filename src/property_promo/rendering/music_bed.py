"""Locally synthesised music bed — no licensed audio, no network.

The default 音乐模式 is `synth`: a quiet, wordless pad played under the film.
It is generated here with numpy and cached by (track, duration), so a batch
of ten listings pays the synthesis cost once per distinct length.

Design rules (§12.2 decision): nothing copyrighted ships with the module and
nothing is downloaded at render time. The pad is deliberately plain — slow
harmonic drift, no percussion, no melody — because it must support the
photos rather than compete with them. Peak sits near -20 dBFS.
"""
from __future__ import annotations

import hashlib
import math
import wave
from pathlib import Path

import numpy as np

#: Tracks the UI offers. Keys must match `settings.music.track`.
TRACKS = ("warm", "calm")

_SR = 44100

# D major pentatonic — consonant under any of the four chords below.
_D3, _A3, _D4, _Fs4, _B3, _B4, _E4, _A4 = (
    146.83, 220.00, 293.66, 369.99, 246.94, 493.88, 329.63, 440.00)

_CHORDS = [
    [_D3, _A3, _D4, _Fs4],
    [_B3, _Fs4, _B4, _D4],
    [_A3, _E4, _A4, _D4],
    [_D3, _A3, _Fs4, _A4],
]

#: Pluck pattern: a slow arpeggio that keeps the film moving forward.
_PLUCKS = [_D4, _A4, _Fs4, _B4, _E4, _A4, _D4, _Fs4]


def cache_dir(base: Path) -> Path:
    d = Path(base) / "music"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _key(track: str, duration_s: float) -> str:
    raw = f"{track}|{round(float(duration_s), 2)}|{_SR}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def synthesize(track: str, duration_s: float, *, base_dir: Path) -> Path:
    """Return the WAV for `track` at `duration_s`, generating it on first use."""
    track = track if track in TRACKS else "warm"
    duration_s = max(1.0, float(duration_s))
    out = cache_dir(base_dir) / f"{track}_{_key(track, duration_s)}.wav"
    if out.is_file() and out.stat().st_size > 1024:
        return out

    n = int(duration_s * _SR)
    t = np.arange(n) / _SR
    mono = np.zeros(n, dtype=np.float64)

    # A warm track breathes slower and sits lower than a calm one.
    breath = 0.055 if track == "warm" else 0.075
    seg = duration_s / len(_CHORDS)
    for i, chord in enumerate(_CHORDS):
        blen = min(n - int(i * seg * _SR), int(seg * 1.55 * _SR))
        if blen <= 0:
            continue
        bt = np.arange(blen) / _SR
        block = np.zeros(blen)
        for fi, fq in enumerate(chord):
            voice = np.zeros(blen)
            # Three slightly detuned copies: beats gently, never chorus-y.
            for det, amp in ((-0.0035, 0.5), (0.0, 1.0), (0.0041, 0.45)):
                voice += amp * np.sin(2 * np.pi * fq * (1 + det) * bt)
            voice += 0.22 * np.sin(2 * np.pi * fq * 2 * bt)
            voice *= 0.88 + 0.12 * np.sin(2 * np.pi * breath * bt + 1.1)
            block += voice * (0.5 / len(chord)) * (1.0 + 0.1 * fi) / 2.17

        a, d, r = int(2.6 * _SR), int(1.0 * _SR), int(3.2 * _SR)
        s = max(1, blen - a - d - r)
        env = np.concatenate([
            np.linspace(0, 1, max(1, a), endpoint=False),
            np.linspace(1, 0.78, max(1, d), endpoint=False),
            np.full(s, 0.78),
            np.linspace(0.78, 0, max(1, r)),
        ])[:blen]
        off = int(i * seg * _SR)
        mono[off:off + blen] += block * env

    # Sparse plucks: forward motion without a tune to remember.
    pos, k = 2.2, 0
    while pos < duration_s - 3.0:
        fq = _PLUCKS[k % len(_PLUCKS)]
        npl = int(2.4 * _SR)
        pt = np.arange(npl) / _SR
        body = (np.sin(2 * np.pi * fq * pt)
                + 0.35 * np.sin(2 * np.pi * fq * 2.01 * pt)
                + 0.12 * np.sin(2 * np.pi * fq * 3.02 * pt))
        envp = np.exp(-pt * 2.6) * np.minimum(1.0, pt / 0.006)
        pluck = body * envp * (0.055 * (1.0 if k % 2 == 0 else 0.72))
        off = int(pos * _SR)
        end = min(n, off + npl)
        mono[off:end] += pluck[: end - off]
        k += 1
        pos += 0.72 * (1.0 if k % 4 else 1.35)

    left, right = mono.copy(), mono.copy()
    spread = int(0.011 * _SR)
    if spread < n:
        right[spread:] = 0.72 * right[spread:] + 0.28 * mono[:-spread]
        left[:-spread] = 0.72 * left[:-spread] + 0.28 * mono[spread:]
    stereo = np.stack([left, right])

    # Soften the top end. Everything the pad contains sits below ~1.5 kHz;
    # two box passes (O(n), pure numpy) are plenty and avoid a scipy import
    # that alone costs seconds on this machine.
    width = max(2, int(_SR / 2750))
    kernel = np.ones(width, dtype=np.float64) / width
    for ch in range(2):
        row = np.convolve(stereo[ch], kernel, mode="same")
        stereo[ch] = np.convolve(row, kernel, mode="same")

    fi, fo = min(int(3.0 * _SR), n // 2), min(int(5.5 * _SR), n // 2)
    if fi:
        stereo[:, :fi] *= np.linspace(0, 1, fi) ** 1.5
    if fo:
        stereo[:, -fo:] *= np.linspace(1, 0, fo) ** 1.2

    peak = float(np.max(np.abs(stereo))) or 1.0
    # The film has no narration, so the pad is the only sound: -12 dBFS peak
    # lands around -27 dB mean on a phone — audible without being loud.
    stereo *= (10 ** (-12 / 20)) / peak
    pcm = np.clip(stereo.T, -1, 1)

    tmp = out.with_suffix(".part")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(_SR)
        w.writeframes((pcm * 32767).astype(np.int16).tobytes())
    tmp.replace(out)
    return out
