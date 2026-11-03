"""Measurable audio defects from the audio-generation antipattern list.

Each check answers a technical question from the decoded samples: where the
sound drops out, whether it is cut off, whether a lossless file was decoded
from a lossy one, whether it collapses in mono, whether pauses carry hiss.
None of them judges musical or perceptual quality, and none says who or what
made the file; that needs listening.

Thresholds were set against 206 shipped game assets (Mureka and ElevenLabs
music and effects, plus a third-party effects pack) so that clean files stay
quiet, and against synthetic defects so that each check fires when it should.
"""

from __future__ import annotations

import numpy as np

# 50 ms analysis windows throughout: short enough to place a defect, long
# enough for a stable level.
WINDOW_S = 0.05
DROPOUT_MIN_S = 0.02
DIGITAL_SILENCE = 1e-4  # about -80 dBFS
SOUND_AROUND_DBFS = -35.0
CUTOFF_WITHIN_DB = 12.0
START_JUMP = 0.05
LOSSY_MIN_HZ, LOSSY_MAX_HZ = 11000.0, 21000.0
LOSSY_WALL_DB = 25.0
LOSSY_FLOOR_DB = -70.0
MONO_WARN_DB, MONO_ERROR_DB = 6.0, 12.0
PAUSE_DBFS = -45.0
HISS_MIN_DBFS = -72.0
HISS_FLATNESS = 0.25
HISS_MIN_PAUSE_S = 0.3


def _db(x: float) -> float:
    return 20.0 * float(np.log10(max(x, 1e-12)))


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x)))) if x.size else 0.0


def clock(seconds: float) -> str:
    m, s = divmod(seconds, 60)
    return f"{int(m)}:{s:04.1f}"


def dropouts(mono: np.ndarray, sr: int) -> list[tuple[float, float, float, float]]:
    """(start s, length s, dBFS before, dBFS after) for each run of digital
    silence of 20 ms or more with sound on both sides."""
    quiet = np.abs(mono) < DIGITAL_SILENCE
    edges = np.flatnonzero(np.diff(np.concatenate(([0], quiet.view(np.int8), [0]))))
    around = int(WINDOW_S * sr)
    out = []
    for s, e in zip(edges[::2], edges[1::2], strict=True):
        if s == 0 or e >= len(mono) or (e - s) / sr < DROPOUT_MIN_S:
            continue
        before, after = _db(_rms(mono[max(0, s - around) : s])), _db(_rms(mono[e : e + around]))
        if before > SOUND_AROUND_DBFS and after > SOUND_AROUND_DBFS:
            out.append((s / sr, (e - s) / sr, before, after))
    return out


def cut_off_ending(mono: np.ndarray, sr: int, last_sound: int) -> float | None:
    """dB between the last 20 ms of sound and the whole file's level, when the
    sound stops while still near full level; None when it decays."""
    span = int(0.02 * sr)
    if last_sound < span * 5:
        return None
    end = _db(_rms(mono[last_sound - span : last_sound + 1]))
    overall = _db(_rms(mono[: last_sound + 1]))
    gap = overall - end
    return gap if gap < CUTOFF_WITHIN_DB else None


def spectral_cutoff(mono: np.ndarray, sr: int) -> tuple[float, float, float] | None:
    """(cutoff Hz, dB just below it, dB above it) when the long-term spectrum
    ends in a wall below Nyquist, as an MP3 or AAC encoder's lowpass leaves it."""
    nfft = 8192
    if sr < 32000 or len(mono) < nfft * 4:
        return None
    starts = np.linspace(0, len(mono) - nfft, num=min(400, len(mono) // nfft)).astype(int)
    win = np.hanning(nfft)
    spec = np.zeros(nfft // 2 + 1)
    for s in starts:
        spec += np.abs(np.fft.rfft(mono[s : s + nfft] * win)) ** 2
    freqs = np.fft.rfftfreq(nfft, 1 / sr)
    ref = spec[(freqs > 1000) & (freqs < 4000)].mean()
    if ref <= 0:
        return None
    rel = 10 * np.log10(spec / ref + 1e-30)
    k = max(1, int(200 / (sr / nfft)))
    smooth = np.convolve(rel, np.ones(k) / k, "same")
    top = min(sr / 2, 24000)
    band = (freqs > 10000) & (freqs < top - 200)
    alive = np.flatnonzero(smooth[band] > LOSSY_FLOOR_DB)
    if not len(alive):
        return None
    cutoff = float(freqs[band][alive[-1]])
    above = smooth[(freqs > cutoff + 500) & (freqs < top - 100)]
    below = smooth[(freqs > cutoff - 2000) & (freqs < cutoff - 500)]
    if not above.size or not below.size:
        return None
    lo, hi = float(below.mean()), float(above.mean())
    if LOSSY_MIN_HZ <= cutoff <= min(LOSSY_MAX_HZ, sr / 2 - 1000) and lo - hi >= LOSSY_WALL_DB:
        return cutoff, lo, hi
    return None


def mono_loss(pcm: np.ndarray) -> tuple[float, float] | None:
    """(dB lost when the two channels are summed to mono, L/R correlation)."""
    if pcm.shape[1] != 2:
        return None
    left, right = pcm[:, 0].astype(np.float64), pcm[:, 1].astype(np.float64)
    both = np.sqrt((np.mean(left**2) + np.mean(right**2)) / 2)
    if both < 1e-6:
        return None
    mid = _rms((left + right) / 2)
    corr = float(np.corrcoef(left, right)[0, 1]) if left.std() and right.std() else 1.0
    return _db(both) - _db(mid), corr


def hiss(mono: np.ndarray, sr: int) -> tuple[float, float] | None:
    """(pause level dBFS, seconds of pauses) when the pauses carry steady
    broadband noise. Pauses are 50 ms windows below -45 dBFS that are not
    digital silence; a tonal decay or room tail is not flat enough to count."""
    w = int(WINDOW_S * sr)
    k = len(mono) // w
    if k < 6:
        return None
    blocks = mono[: k * w].reshape(k, w).astype(np.float64)
    level = 20 * np.log10(np.sqrt(np.mean(blocks**2, axis=1)) + 1e-12)
    pause = (level < PAUSE_DBFS) & (level > -100)
    if pause.sum() * WINDOW_S < HISS_MIN_PAUSE_S:
        return None
    power = np.mean(np.abs(np.fft.rfft(blocks[pause] * np.hanning(w), axis=1)) ** 2, axis=0)[1:]
    flatness = float(np.exp(np.mean(np.log(power + 1e-20))) / (np.mean(power) + 1e-20))
    floor = float(np.median(level[pause]))
    if floor > HISS_MIN_DBFS and flatness > HISS_FLATNESS:
        return floor, float(pause.sum() * WINDOW_S)
    return None
