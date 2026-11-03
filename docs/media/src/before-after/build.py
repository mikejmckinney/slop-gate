"""Build the README's before-and-after media from the files the checks ran on.

    python build.py RAW_DIR OUT_DIR

RAW_DIR holds:
  sprites/<model>.png      the five image models' "32x32 pixel art" knights, as returned
  sprite-after.png         the sunburst knight after the report's fixes
  arena-before.ogg         the Mureka take as first committed (loop: true)
  arena-after.ogg          the 16-bar loop cut from it
  midi-before.mid          Basic Pitch's transcription of the loop
  midi-after.mid           the same after merging fragments with no new attack

OUT_DIR gets sprites/*.webp, sprite-after.webp, loop-wrap.webp, loop-wrap.mp4,
arena-{before,after}-wrap.ogg and midi.webp. How each input was made is in
README.md in this folder.
"""

import subprocess
import sys
import tempfile
from pathlib import Path

import mido
import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONT = "/usr/share/fonts/opentype/inter/Inter-SemiBold.otf"
BG, PANEL, INK, DIM, MARK = (13, 17, 23), (22, 27, 34), (230, 237, 243), (125, 133, 144), (248, 81, 73)
WAVE, PLAY = (88, 166, 255), (255, 255, 255)
W = 880
SR = 44100
SIDE = 6.0  # seconds either side of the loop point


def font(size):
    return ImageFont.truetype(FONT, size)


def sprites(raw: Path, out: Path):
    (out / "sprites").mkdir(parents=True, exist_ok=True)
    for p in sorted((raw / "sprites").glob("*.png")):
        im = Image.open(p).convert("RGB")
        im.thumbnail((200, 200), Image.LANCZOS)
        im.save(out / "sprites" / f"{p.stem}.webp", quality=85)
    after = Image.open(raw / "sprite-after.png").convert("RGBA")
    assert after.size == (32, 32), after.size
    after.resize((192, 192), Image.NEAREST).save(out / "sprite-after.webp", lossless=True)


def decode(path: Path) -> np.ndarray:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "2", "-ar", str(SR), "-f", "f32le", "-"],
        capture_output=True,
        check=True,
    ).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, 2)


def loudness(path: Path) -> tuple[float, float]:
    err = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-af",
            "ebur128=peak=true",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    ).stderr
    summary = err.rsplit("Summary:", 1)[1]
    lufs = float(summary.split("I:")[1].split("LUFS")[0])
    peak = float(summary.split("Peak:")[1].split("dBFS")[0])
    return lufs, peak


def wrap(pcm: np.ndarray) -> np.ndarray:
    """The last SIDE seconds then the first SIDE seconds: what plays when the game loops it.

    The excerpt's own edges are faded (5 ms in, 100 ms out) so starting and stopping the clip
    doesn't click; the loop point in the middle is left exactly as the game plays it.
    """
    n = int(SIDE * SR)
    clip = np.concatenate([pcm[-n:], pcm[:n]]).copy()
    fade_in, fade_out = int(0.005 * SR), int(0.100 * SR)
    clip[:fade_in] *= np.linspace(0, 1, fade_in)[:, None]
    clip[-fade_out:] *= np.linspace(1, 0, fade_out)[:, None]
    return clip


def end_silence(pcm: np.ndarray) -> float:
    loud = np.flatnonzero(np.abs(pcm).max(1) > 10 ** (-60 / 20))
    return (len(pcm) - 1 - loud[-1]) / SR


def write_ogg(pcm: np.ndarray, dst: Path):
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "f32le",
            "-ar",
            str(SR),
            "-ac",
            "2",
            "-i",
            "-",
            "-c:a",
            "libvorbis",
            "-q:a",
            "5",
            str(dst),
        ],
        input=pcm.astype(np.float32).tobytes(),
        check=True,
    )


def envelope(pcm: np.ndarray, width: int) -> tuple[np.ndarray, np.ndarray]:
    mono = pcm.mean(1)
    cols = np.array_split(mono, width)
    return np.array([c.min() for c in cols]), np.array([c.max() for c in cols])


def loop_frames(rows, fps):
    """rows: (title, detail, pcm) per version. Yields frames that play each row in turn."""
    row_h, top, x0, x1 = 150, 16, 24, W - 24
    h = top + len(rows) * (row_h + 16)
    envs = [envelope(pcm, x1 - x0) for _, _, pcm in rows]
    clip = 2 * SIDE
    for i in range(int(len(rows) * clip * fps)):
        t = i / fps
        active, at = int(t // clip), t % clip
        im = Image.new("RGB", (W, h), BG)
        d = ImageDraw.Draw(im)
        for r, ((title, detail, _), (lo, hi)) in enumerate(zip(rows, envs, strict=True)):
            y = top + r * (row_h + 16)
            on = r == active
            d.rounded_rectangle([8, y, W - 8, y + row_h], 8, fill=PANEL)
            d.text((x0, y + 10), title, font=font(17), fill=INK if on else DIM)
            d.text((x0, y + 34), detail, font=font(13), fill=INK if on else DIM)
            mid, amp = y + 100, 38
            colour = WAVE if on else DIM
            for k in range(x1 - x0):
                d.line([(x0 + k, mid - hi[k] * amp), (x0 + k, mid - lo[k] * amp)], fill=colour)
            seam = x0 + (x1 - x0) // 2
            for yy in range(y + 56, y + row_h - 6, 6):
                d.line([(seam, yy), (seam, yy + 3)], fill=MARK, width=2)
            d.text((seam + 6, y + row_h - 22), "loop point", font=font(12), fill=MARK)
            if on:
                px = x0 + int(at / clip * (x1 - x0))
                d.line([(px, y + 56), (px, y + row_h - 6)], fill=PLAY, width=2)
        yield im


def loop(raw: Path, out: Path):
    before, after = decode(raw / "arena-before.ogg"), decode(raw / "arena-after.ogg")
    (lb, pb), (la, pa) = loudness(raw / "arena-before.ogg"), loudness(raw / "arena-after.ogg")
    wb, wa = wrap(before), wrap(after)
    write_ogg(wb, out / "arena-before-wrap.ogg")
    write_ogg(wa, out / "arena-after-wrap.ogg")
    rows = [
        (
            "Before: the generated track, committed as a loop",
            f"{lb:.1f} LUFS, true peak {pb:+.1f} dBTP, "
            f"{end_silence(before) * 1000:.0f} ms of silence before it wraps",
            wb,
        ),
        (
            "After: 16 bars cut on the beat, seam crossfaded",
            f"{la:.1f} LUFS, true peak {pa:+.1f} dBTP, no gap",
            wa,
        ),
    ]
    frames = list(loop_frames(rows, fps=8))
    # Lossless: only the playhead moves, so frame diffs stay small and leave no ghosts.
    frames[0].save(
        out / "loop-wrap.webp",
        save_all=True,
        append_images=frames[1:],
        duration=125,
        loop=0,
        lossless=True,
        method=6,
    )
    with tempfile.TemporaryDirectory() as tmp:
        audio = Path(tmp) / "a.wav"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", "-", str(audio)],
            input=np.concatenate([wb, wa]).astype(np.float32).tobytes(),
            check=True,
        )
        first = next(loop_frames(rows, fps=25))
        enc = subprocess.Popen(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-s",
                f"{first.width}x{first.height}",
                "-r",
                "25",
                "-i",
                "-",
                "-i",
                str(audio),
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "160k",
                "-shortest",
                "-movflags",
                "+faststart",
                str(out / "loop-wrap.mp4"),
            ],
            stdin=subprocess.PIPE,
        )
        for im in loop_frames(rows, fps=25):
            enc.stdin.write(im.tobytes())
        enc.stdin.close()
        assert enc.wait() == 0


def notes(path: Path):
    m = mido.MidiFile(path)
    tempo = next((msg.tempo for t in m.tracks for msg in t if msg.type == "set_tempo"), 500000)
    out, open_ = [], {}

    def sec(tick):
        return mido.tick2second(tick, m.ticks_per_beat, tempo)

    for track in m.tracks:
        t = 0
        for msg in track:
            t += msg.time
            if msg.type == "note_on" and msg.velocity:
                open_[(msg.channel, msg.note)] = t
            elif msg.type in ("note_on", "note_off") and (msg.channel, msg.note) in open_:
                s = open_.pop((msg.channel, msg.note))
                out.append((sec(s), sec(t), msg.note))
    return out, 60 / mido.tempo2bpm(tempo) * 4


def midi(raw: Path, out: Path):
    (nb, bar), (na, _) = notes(raw / "midi-before.mid"), notes(raw / "midi-after.mid")
    span = 4 * bar
    shown = [n for n in nb + na if n[0] < span]
    lo, hi = min(n[2] for n in shown) - 1, max(n[2] for n in shown) + 1
    row_h, x0, x1 = 220, 24, W - 24
    im = Image.new("RGB", (W, 2 * row_h + 48), BG)
    d = ImageDraw.Draw(im)
    for r, (title, ns) in enumerate(
        [
            (f"Before: Basic Pitch transcription, {len(nb)} notes", nb),
            (f"After: fragments merged where the audio has no new attack, {len(na)} notes", na),
        ]
    ):
        y = 16 + r * (row_h + 16)
        d.rounded_rectangle([8, y, W - 8, y + row_h], 8, fill=PANEL)
        d.text((x0, y + 10), title, font=font(15), fill=INK)
        top, bottom = y + 40, y + row_h - 26
        for b in range(5):
            x = x0 + b * (x1 - x0) / 4
            d.line([(x, top), (x, bottom)], fill=(48, 54, 61))
            if b < 4:
                d.text((x + 4, bottom + 6), f"bar {b + 1}", font=font(11), fill=DIM)
        ph = (bottom - top) / (hi - lo)
        for s, e, p in ns:
            if s >= span:
                continue
            xa, xb = x0 + s / span * (x1 - x0), x0 + min(e, span) / span * (x1 - x0)
            yy = bottom - (p - lo) * ph
            d.rectangle([xa, yy - ph * 0.8, max(xa + 1, xb - 1), yy], fill=WAVE)
            d.rectangle([xa, yy - ph * 0.8, xa + 1, yy], fill=PLAY)  # each note's start
    im.save(out / "midi.webp", quality=90)


def main():
    raw, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    sprites(raw, out)
    loop(raw, out)
    midi(raw, out)


if __name__ == "__main__":
    main()
