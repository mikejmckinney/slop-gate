"""Cut the raw recordings into the README hero and gallery, as animated WebP.

    python encode.py RAW_DIR OUT_DIR [NAME ...]

RAW_DIR holds the VHS terminal clips (residue.mp4, scope.mp4) and the screen
recordings with their timing marks (<scene>.raw.mp4, <scene>.marks.json).
Name outputs (hero, pr-blocked-then-fixed, residue, scope, footer) to build
only those.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

FONT = "/usr/share/fonts/opentype/inter/Inter-SemiBold.otf"
FPS = 15
XF = 0.35  # crossfade between segments, seconds
# Lossy frames that libwebp stores as diffs against the previous frame left
# faint ghosts of the old content after each cut. A key frame at least every
# three frames clears them within 0.2 s.
KMIN, KMAX = 2, 3


def marks(raw: Path, scene: str) -> dict:
    return json.loads((raw / f"{scene}.marks.json").read_text())


def seg(src, start, end, crop=None, scale=None, caption=None, bar=0, hold=0.0):
    """One segment: a time range of a source, cropped, scaled, captioned.

    hold freezes the last frame for that many extra seconds.
    """
    return dict(src=src, start=start, end=end, crop=crop, scale=scale, caption=caption, bar=bar, hold=hold)


def esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’").replace("%", "\\%")


def build(segments, out: Path, quality=72, loop_fade=True, fps=FPS, lossless=False):
    inputs, chains, labels = [], [], []
    for i, s in enumerate(segments):
        inputs += ["-ss", f"{s['start']:.2f}", "-to", f"{s['end']:.2f}", "-i", str(s["src"])]
        f = [f"fps={fps}"]
        if s["crop"]:
            w, h, x, y = s["crop"]
            f.append(f"crop={w}:{h}:{x}:{y}")
        if s["scale"]:
            f.append(f"scale={s['scale']}:-2:flags=lanczos")
        if s["bar"]:
            f.append(f"pad=iw:ih+{s['bar']}:0:0:color=0x0d1117")
        if s["caption"]:
            f.append(
                f"drawtext=fontfile={FONT}:text='{esc(s['caption'])}':fontcolor=0xe6edf3:fontsize=21:"
                f"x=24:y=h-{s['bar']}+({s['bar']}-th)/2"
            )
        if s["hold"]:
            f.append(f"tpad=stop_mode=clone:stop_duration={s['hold']}")
        f += ["format=yuv420p", "setsar=1", "settb=AVTB", "setpts=PTS-STARTPTS"]
        chains.append(f"[{i}:v]{','.join(f)}[v{i}]")
        labels.append((f"v{i}", s["end"] - s["start"] + s["hold"]))
    # Crossfade the segments together.
    cur, cur_len = labels[0]
    for j, (lab, length) in enumerate(labels[1:], 1):
        nxt = f"x{j}"
        chains.append(f"[{cur}][{lab}]xfade=transition=fade:duration={XF}:offset={cur_len - XF:.2f}[{nxt}]")
        cur, cur_len = nxt, cur_len + length - XF
    if loop_fade:
        # Fade in from and out to the page background so the loop point is soft.
        chains.append(
            f"[{cur}]fade=t=in:st=0:d=0.3:color=0x0d1117,"
            f"fade=t=out:st={cur_len - 0.35:.2f}:d=0.35:color=0x0d1117[final]"
        )
        cur = "final"
    # ffmpeg renders PNG frames; libwebp, through Pillow, assembles the
    # animation, because Pillow passes the key-frame settings above.
    with tempfile.TemporaryDirectory() as tmp:
        cmd = [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            *inputs,
            "-filter_complex",
            ";".join(chains),
            "-map",
            f"[{cur}]",
            f"{tmp}/%05d.png",
        ]
        subprocess.run(cmd, check=True)
        files = sorted(Path(tmp).glob("*.png"))
        first = Image.open(files[0])
        first.save(
            out,
            save_all=True,
            append_images=(Image.open(f) for f in files[1:]),
            duration=round(1000 / fps),
            loop=0,
            quality=quality,
            method=6,
            lossless=lossless,
            # minimize_size turns key frames off, so only lossless gets it.
            **({"minimize_size": True} if lossless else {"kmin": KMIN, "kmax": KMAX}),
        )
    print(f"{out.name}: {len(files) / fps:.1f}s, {out.stat().st_size / 1e6:.2f} MB")


def main():
    raw, out = Path(sys.argv[1]), Path(sys.argv[2])
    only = set(sys.argv[3:])  # optional: build just these outputs

    def want(name):
        return not only or name in only

    out.mkdir(parents=True, exist_ok=True)
    b, d, f, x = (marks(raw, s) for s in ("blocked", "diff", "fixed", "footer"))
    blocked, diff, fixed, footer = (raw / f"{s}.raw.mp4" for s in ("blocked", "diff", "fixed", "footer"))

    # Hero: the PR loop in four captioned beats, cropped to the timeline column.
    bar = 46
    if want("hero"):
        build(
            [
                seg(
                    blocked,
                    b["ready"] + 2.0,
                    b["ready"] + 6.6,
                    crop=(880, 560, 10, 150),
                    caption="1  slop-gate blocks the PR and lists a fix for every finding",
                    bar=bar,
                ),
                seg(
                    diff,
                    d["ready"] + 3.4,
                    d["ready"] + 6.4,
                    crop=(880, 560, 340, 230),
                    caption="2  The author rewrites the flagged lines",
                    bar=bar,
                ),
                seg(
                    fixed,
                    f["ready"] + 2.9,
                    f["passed_end"] + 0.2,
                    crop=(880, 560, 10, 250),
                    caption="3  On the next push, the same comment turns green",
                    bar=bar,
                ),
                seg(
                    fixed,
                    f["passed_end"] - 0.15,
                    f["end"] - 0.3,
                    crop=(880, 560, 10, 250),
                    caption="4  The fix commit gets a green check, and review can finish",
                    bar=bar,
                ),
            ],
            out / "hero.webp",
        )

    # Gallery 1: the whole PR, with the browser's address bar, scaled down.
    if want("pr-blocked-then-fixed"):
        build(
            [
                seg(blocked, b["ready"] + 2.0, b["end"] - 0.3, scale=1000),
                seg(diff, d["ready"] + 3.3, d["ready"] + 8.5, scale=1000),
                seg(fixed, f["ready"] + 1.6, f["end"] - 0.3, scale=1000),
            ],
            out / "pr-blocked-then-fixed.webp",
            quality=62,
            fps=12,
        )

    # Gallery 2 and 3: terminal recordings, already trimmed by VHS.
    for name in [n for n in ("residue", "scope") if want(n)]:
        src = raw / f"{name}.mp4"
        length = float(
            subprocess.check_output(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(src)]
            )
        )
        # Lossless: terminal frames are flat colors, and lossy frame diffs
        # left faint ghosts of cleared text behind.
        build([seg(src, 0, length, scale=1000)], out / f"{name}.webp", quality=80, lossless=True)

    # Gallery 4: the footer arrives, the workflow removes it.
    if want("footer"):
        build(
            [
                seg(
                    footer,
                    x["ready"],
                    x["ready"] + 3.0,
                    scale=1000,
                    caption="Posted from a cloud session: the tool footer is appended",
                    bar=bar,
                ),
                seg(
                    footer,
                    x["stripped"] - 0.2,
                    x["end"] - 0.3,
                    hold=2.0,
                    scale=1000,
                    caption=f"{x['stripped'] - x['ready']:.0f} s later: the footer workflow has removed it",
                    bar=bar,
                ),
            ],
            out / "footer.webp",
        )


if __name__ == "__main__":
    main()
