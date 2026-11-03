"""Cut a 16-bar loop from inside a generated track, crossfade the seam, set loudness, export OGG.

The loop end is the tracked beat 64 beats after the start. The audio that follows the loop end is
crossfaded (equal power) into the loop's first XFADE seconds, so the wrap plays the music's own
continuation instead of a cut.
"""

import subprocess
import sys

import librosa
import numpy as np
import soundfile as sf

src, dst = sys.argv[1], sys.argv[2]
XFADE = 0.35
BEATS = 64
y, sr = sf.read(src, always_2d=True)
mono = y.mean(1)
_, beats = librosa.beat.beat_track(
    y=librosa.resample(mono, orig_sr=sr, target_sr=22050), sr=22050, units="time"
)
dur = len(mono) / sr
best = None
for k in range(len(beats) - BEATS):
    a, b = beats[k], beats[k + BEATS]
    if a < 30 or b > dur - 30:
        continue

    def feat(t):
        seg = mono[int(t * sr) : int((t + 2.0) * sr)]
        spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
        bands = np.add.reduceat(spec, np.linspace(0, len(spec) - 1, 25).astype(int))
        return 20 * np.log10(bands + 1e-9)

    score = np.abs(feat(a) - feat(b)).mean()
    if best is None or score < best[0]:
        best = (score, a, b)
score, a, b = best
s, e, x = int(round(a * sr)), int(round(b * sr)), int(XFADE * sr)


# snap both cut points to the nearest zero crossing of the mono mix within 2 ms
def snap(i):
    w = int(0.002 * sr)
    seg = mono[i - w : i + w]
    zc = np.flatnonzero(np.diff(np.signbit(seg)))
    return i - w + zc[np.argmin(np.abs(zc - w))] if len(zc) else i


s, e = snap(s), snap(e)
loop = y[s:e].copy()
t = np.linspace(0, np.pi / 2, x)[:, None]
loop[:x] = loop[:x] * np.sin(t) + y[e : e + x] * np.cos(t)
tmp = dst + ".wav"
sf.write(tmp, loop, sr, subtype="FLOAT")
# loudness: measure, then one linear gain to -16 LUFS (true peak checked by the gate)
out = subprocess.run(
    ["ffmpeg", "-hide_banner", "-nostats", "-i", tmp, "-af", "ebur128=peak=true", "-f", "null", "-"],
    capture_output=True,
    text=True,
).stderr
lufs = float(out.split("Integrated loudness:")[1].split("I:")[1].split("LUFS")[0])
gain = -16.0 - lufs
subprocess.run(
    [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-i",
        tmp,
        "-af",
        f"volume={gain:.2f}dB",
        "-c:a",
        "libvorbis",
        "-q:a",
        "6",
        dst,
    ],
    check=True,
)
print(
    f"loop {a:.3f}s-{b:.3f}s ({(e - s) / sr:.3f}s, {BEATS} beats, "
    f"seam spectral diff {score:.2f} dB), gain {gain:+.2f} dB"
)
