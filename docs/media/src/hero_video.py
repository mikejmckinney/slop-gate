"""Build the README hero: a feature tour as MP4, from the recordings in docs/media.

    python hero_video.py OUT.mp4

Every scene is a recording or a report slop-gate wrote; nothing is mocked up.
The browser and terminal scenes are the animated WebPs in docs/media, the music
scene is before-after/loop-wrap.mp4 with its sound, and the writing, sprite,
speech and MIDI scenes lay out the real inputs and report text from
before-after/ (see before-after/README.md for how those were made).

Each scene is encoded to its own file with the same settings, then joined with
the concat demuxer; the only sound is the music scene's, laid on at its offset.
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
MEDIA = HERE.parent
W, H, FPS = 1280, 720, 30
HEAD = 72  # height of the title strip above each scene
BG, PANEL, LINE = (13, 17, 23), (22, 27, 34), (48, 54, 61)
FG, MUTED = (230, 237, 243), (139, 148, 158)
RED, AMBER, GREEN, BLUE = (248, 81, 73), (210, 153, 34), (63, 185, 80), (88, 166, 255)
INTER = "/usr/share/fonts/opentype/inter/Inter-{}.otf"
MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
FADE = 0.25  # dip to the background between scenes, seconds
X264 = ["-c:v", "libx264", "-crf", "20", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-g", "60"]


def font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(INTER.format(weight), size)


def mono(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(MONO, size)


def canvas(title: str | None = None) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    if title:
        d.text((40, HEAD // 2), title, font=font("SemiBold", 30), fill=FG, anchor="lm")
        d.text((W - 40, HEAD // 2), "slop-gate", font=font("Medium", 20), fill=MUTED, anchor="rm")
        d.line((40, HEAD - 1, W - 40, HEAD - 1), fill=LINE)
    return img, d


def fit(frame: Image.Image, box=(40, HEAD + 12, W - 40, H - 16)) -> Image.Image:
    """Scale a recording frame into the area under the title strip."""
    x0, y0, x1, y1 = box
    s = min((x1 - x0) / frame.width, (y1 - y0) / frame.height)
    return frame.resize((round(frame.width * s), round(frame.height * s)), Image.LANCZOS)


def paste_center(img: Image.Image, part: Image.Image, top: int) -> None:
    img.paste(part, ((W - part.width) // 2, top))


def wrap(d: ImageDraw.ImageDraw, text: str, f, width: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if d.textlength(trial, font=f) <= width:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    return lines + [cur] if cur else lines


def badge_center(d, y, text, color):
    f = font("SemiBold", 18)
    badge(d, (W / 2 - (d.textlength(text, font=f) + 20) / 2, y), text, color, f)


def badge(d, xy, text, color, f=None):
    f = f or font("SemiBold", 18)
    x, y = xy
    w = d.textlength(text, font=f)
    d.rounded_rectangle((x, y, x + w + 20, y + 30), radius=15, fill=color)
    d.text((x + 10, y + 15), text, font=f, fill=BG, anchor="lm")
    return x + w + 20


# ---------------------------------------------------------------- scene sources


def webp_frames(path: Path, max_hold: float | None, fast: float = 1.0):
    """(frame, seconds) pairs from an animated WebP: long holds capped, short frames sped up by fast."""
    im = Image.open(path)
    raw = path.read_bytes()
    # Pillow doesn't report per-frame durations for these files; read the ANMF chunks.
    durs, i = [], 12
    while i + 8 <= len(raw):
        cid, size = raw[i : i + 4], int.from_bytes(raw[i + 4 : i + 8], "little")
        if cid == b"ANMF":
            durs.append(int.from_bytes(raw[i + 20 : i + 23], "little") / 1000)
        i += 8 + size + (size & 1)
    for n, dur in enumerate(durs):
        im.seek(n)
        if max_hold:
            dur = min(dur, max_hold)
        yield im.convert("RGB"), dur / fast if dur < 0.25 else dur


def recording(title: str, path: Path, max_hold: float | None = None, fast: float = 1.0):
    for frame, dur in webp_frames(path, max_hold, fast):
        img, _ = canvas(title)
        paste_center(img, fit(frame), HEAD + 12)
        yield img, dur


def still(img: Image.Image, seconds: float):
    yield img, seconds


def blend(a: Image.Image, b: Image.Image, seconds: float = 0.4):
    n = round(seconds * FPS)
    for k in range(1, n + 1):
        yield Image.blend(a, b, k / n), 1 / FPS


# --------------------------------------------------------------------- scenes


def title_card():
    img, d = canvas()
    d.text((W // 2, 210), "slop-gate", font=font("Bold", 84), fill=FG, anchor="mm")
    d.text(
        (W // 2, 292),
        "A pull-request check that blocks what AI tools leave behind",
        font=font("Medium", 30),
        fill=MUTED,
        anchor="mm",
    )
    chips = ["Prose and PR text", "Code residue", "Hidden Unicode", "Credentials", "Images", "Audio", "MIDI"]
    f = font("SemiBold", 21)
    widths = [d.textlength(c, font=f) + 36 for c in chips]
    rows = [chips[:4], chips[4:]]
    y = 380
    for row in rows:
        total = sum(widths[chips.index(c)] for c in row) + 14 * (len(row) - 1)
        x = (W - total) / 2
        for c in row:
            w = widths[chips.index(c)]
            d.rounded_rectangle((x, y, x + w, y + 44), radius=22, fill=PANEL, outline=LINE)
            d.text((x + w / 2, y + 22), c, font=f, fill=FG, anchor="mm")
            x += w + 14
        y += 60
    d.text(
        (W // 2, 590),
        "Recordings of slop-gate running. Turn the sound on for the music and speech examples.",
        font=font("Regular", 22),
        fill=MUTED,
        anchor="mm",
    )
    return img


def writing_scene():
    src = MEDIA / "src/before-after/inputs"
    paras = [p.replace("**", "") for p in src.joinpath("writing-before.md").read_text().split("\n\n")]
    # The heading, then the first paragraph and the start of the second, as the README quotes it.
    cut = paras[2].index("and trims unnecessary silence.") + len("and trims unnecessary silence.")
    before = [paras[1], paras[2][:cut] + " […]"]
    after = " ".join(
        ln for ln in src.joinpath("writing-after.md").read_text().splitlines() if not ln.startswith("#")
    )
    flagged = {
        "This isn't just tedious; it's": RED,
        "meticulously": AMBER,
        "streamlines": AMBER,
    }
    body, rule = font("Regular", 21), font("Medium", 19)

    def text_block(d, paras, top, marks):
        y = top
        for para in paras:
            for line in wrap(d, " ".join(para.split()), body, W - 120):
                x = 60
                d.text((x, y), line, font=body, fill=FG)
                for phrase, color in marks.items():
                    at = line.find(phrase)
                    if at >= 0:
                        x0 = x + d.textlength(line[:at], font=body)
                        x1 = x0 + d.textlength(phrase, font=body)
                        d.rectangle((x0, y + 25, x1, y + 28), fill=color)
                y += 31
            y += 12
        return y

    a, d = canvas("Writing: a model's README section")
    d.text((60, HEAD + 22), "gemini-2.5-flash wrote:", font=font("Medium", 20), fill=MUTED)
    y = text_block(d, before, HEAD + 58, flagged)
    rows = [
        (RED, "blocks", "ContrastFormula", "'This isn't just tedious; it's' is a contrast formula."),
        (AMBER, "warns", "AIVocabulary", "'meticulously' is inflated vocabulary."),
        (AMBER, "warns", "CorporateVerbs", "'streamlines' names no concrete action."),
    ]
    y += 4
    for color, level, name, why in rows:
        d.rounded_rectangle((60, y, W - 60, y + 38), radius=6, fill=PANEL)
        badge(d, (70, y + 4), level, color, font("SemiBold", 16))
        d.text((170, y + 19), name, font=mono(18), fill=FG, anchor="lm")
        d.text((390, y + 19), why, font=rule, fill=MUTED, anchor="lm")
        y += 46

    b, d = canvas("Writing: the rewrite")
    d.text((60, HEAD + 22), "After following the fix text:", font=font("Medium", 20), fill=MUTED)
    y = text_block(d, [after], HEAD + 58, {})
    badge(d, (60, y + 10), "passes, 0 findings", GREEN)
    yield from still(a, 5.5)
    yield from blend(a, b)
    yield from still(b, 3.5)


def sprite_scene():
    sprites = [
        ("gpt-image-2.5-sunburst", "1024×1024 · 52,461 colors · soft alpha"),
        ("gemini-3-pro-image", "1024×1024 · 65,818 colors · no alpha"),
        ("gemini-3.1-flash-image", "1024×1024 · 65,759 colors · no alpha"),
        ("mai-image-2.5-pro", "1024×1024 · 77,346 colors · no alpha"),
        ("seedream-5-0-pro", "2048×2048 · JPEG named .png"),
    ]
    a, d = canvas("Pixel art: five models, one 32×32 sprite prompt")
    cell = (W - 80) // 5
    for k, (name, why) in enumerate(sprites):
        x = 40 + k * cell
        pic = Image.open(MEDIA / f"before-after/sprites/{name}.webp").convert("RGB").resize((200, 200))
        a.paste(pic, (x + (cell - 200) // 2, HEAD + 60))
        d.text((x + cell // 2, HEAD + 282), name, font=mono(15), fill=FG, anchor="mm")
        for j, part in enumerate(why.split(" · ")):
            d.text(
                (x + cell // 2, HEAD + 314 + j * 26), part, font=font("Regular", 17), fill=RED, anchor="mm"
            )
    d.text(
        (W // 2, HEAD + 450),
        "The manifest asks for 32×32, 16 colors, hard transparency.",
        font=font("Medium", 22),
        fill=MUTED,
        anchor="mm",
    )
    badge_center(d, HEAD + 490, "16 blocking findings", RED)

    b, d = canvas("Pixel art: after the fix text")
    pic = Image.open(MEDIA / "before-after/sprite-after.webp").convert("RGBA")
    pic = pic.resize((288, 288), Image.NEAREST)
    back = Image.new("RGBA", pic.size, PANEL + (255,))
    b.paste(Image.alpha_composite(back, pic).convert("RGB"), ((W - 288) // 2, HEAD + 70))
    d.text(
        (W // 2, HEAD + 400),
        "32×32 · 16 colors · hard alpha (shown at 9×)",
        font=font("Medium", 22),
        fill=FG,
        anchor="mm",
    )
    badge_center(d, HEAD + 440, "passes, 0 findings", GREEN)
    yield from still(a, 4.5)
    yield from blend(a, b)
    yield from still(b, 3.0)


MUSIC_CUTS = [(3.0, 9.0), (15.0, 21.0)]  # each 6 s window holds one loop point, 3 s in
SPEECH = MEDIA / "before-after"  # speech-before.ogg and speech-after.ogg, see before-after/README.md
SPEECH_LEAD, SPEECH_GAP = 0.6, 0.8  # silence before the first take, and between the takes


def duration(path: Path) -> float:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]
    return float(subprocess.run(cmd, check=True, capture_output=True, text=True).stdout)


def probe_size(path: Path) -> tuple[int, int]:
    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height"]
    out = subprocess.run([*cmd, "-of", "csv=p=0", str(path)], check=True, capture_output=True, text=True)
    w, h = out.stdout.strip().split(",")
    return int(w), int(h)


def music_frames():
    """Frames of loop-wrap.mp4 inside MUSIC_CUTS, with the report rows below."""
    src = MEDIA / "before-after/loop-wrap.mp4"
    rows_before = [
        ("true-peak", "True peak +0.9 dBTP exceeds −1.0 dBTP."),
        ("loudness", "−10.9 LUFS; the manifest targets −16 ±2."),
        ("loop-gap", "933 ms of silence at the end plays as a gap on every wrap."),
    ]
    vw, vh = probe_size(src)
    for n, (t0, t1) in enumerate(MUSIC_CUTS):
        proc = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-ss",
                str(t0),
                "-to",
                str(t1),
                "-i",
                str(src),
                "-vf",
                f"fps={FPS}",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-",
            ],
            check=True,
            capture_output=True,
        )
        size = vw * vh * 3
        frames = [proc.stdout[k : k + size] for k in range(0, len(proc.stdout), size)]
        for raw in frames[: round((t1 - t0) * FPS)]:
            img, d = canvas("Game music: does the loop wrap cleanly? (sound on)")
            wave = Image.frombytes("RGB", (vw, vh), raw)
            wave = wave.resize((1100, round(vh * 1100 / vw)), Image.LANCZOS)
            paste_center(img, wave, HEAD + 10)
            y = HEAD + 460
            if n == 0:
                for rule, why in rows_before:
                    d.rounded_rectangle((90, y, W - 90, y + 40), radius=6, fill=PANEL)
                    badge(d, (100, y + 5), "blocks", RED, font("SemiBold", 16))
                    d.text((200, y + 20), rule, font=mono(18), fill=FG, anchor="lm")
                    d.text((330, y + 20), why, font=font("Medium", 19), fill=MUTED, anchor="lm")
                    y += 48
            else:
                d.text(
                    (W // 2, y + 30),
                    "16 bars cut on the beat, seam crossfaded, −16 LUFS",
                    font=font("Medium", 22),
                    fill=FG,
                    anchor="mm",
                )
                badge_center(d, y + 64, "passes, 0 findings", GREEN)
            yield img, 1 / FPS


def music_audio(out: Path) -> None:
    src = MEDIA / "before-after/loop-wrap.mp4"
    parts = []
    for n, (t0, t1) in enumerate(MUSIC_CUTS):
        part = out.with_name(f"music{n}.wav")
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-ss",
                str(t0),
                "-to",
                str(t1),
                "-i",
                str(src),
                "-vn",
                "-af",
                f"afade=t=in:d=0.15,afade=t=out:st={t1 - t0 - 0.3}:d=0.3",
                "-ar",
                "48000",
                "-ac",
                "2",
                str(part),
            ],
            check=True,
        )
        parts.append(part)
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(parts[0]),
            "-i",
            str(parts[1]),
            "-filter_complex",
            "[0][1]concat=n=2:v=0:a=1",
            str(out),
        ],
        check=True,
    )


def speech_frame(after: bool) -> Image.Image:
    img, d = canvas("Spoken line: the transcript check (sound on)")
    y0 = HEAD + 40
    color, label = (
        (GREEN, "Playing: re-read from the script") if after else (RED, "Playing: the take as generated")
    )
    d.ellipse((60, y0 + 4, 76, y0 + 20), fill=color)
    d.text((88, y0 + 12), label, font=font("SemiBold", 22), fill=FG, anchor="lm")
    d.text((60, y0 + 56), "audio/intro.script.txt says:", font=font("Medium", 20), fill=MUTED)
    script = "… Tap and hold to charge, then release to fire. Wave seven begins in 1,250 meters."
    d.rounded_rectangle((60, y0 + 88, W - 60, y0 + 148), radius=8, fill=PANEL)
    d.text((80, y0 + 118), script, font=font("Regular", 24), fill=FG, anchor="lm")
    y = y0 + 190
    lead = "A model transcribes the take, and slop-gate compares its words with the script in code:"
    d.text((60, y), lead, font=font("Medium", 20), fill=MUTED)
    y += 40
    if after:
        badge(d, (60, y + 4), "no differences", GREEN)
        return img
    rows = [("after “hold to charge”", "then", "(nothing)"), ("after “seven begins in”", "1250", "12050")]
    for where, want, got in rows:
        d.rounded_rectangle((60, y, W - 60, y + 52), radius=6, fill=PANEL)
        d.text((80, y + 26), where, font=font("Medium", 21), fill=FG, anchor="lm")
        x = 420
        d.text((x, y + 26), "expected", font=font("Regular", 20), fill=MUTED, anchor="lm")
        x += 95
        d.text((x, y + 26), f"“{want}”", font=mono(21), fill=GREEN, anchor="lm")
        x += 170
        d.text((x, y + 26), "heard", font=font("Regular", 20), fill=MUTED, anchor="lm")
        x += 70
        d.text((x, y + 26), f"“{got}”", font=mono(21), fill=RED, anchor="lm")
        y += 64
    return img


def speech_cues() -> list[tuple[float, Path]]:
    """Where each take starts, in seconds from the start of the speech scene."""
    before, after = SPEECH / "speech-before.ogg", SPEECH / "speech-after.ogg"
    return [(SPEECH_LEAD, before), (SPEECH_LEAD + duration(before) + SPEECH_GAP, after)]


def speech_scene():
    _, (at_after, after) = speech_cues()
    a, b = speech_frame(False), speech_frame(True)
    yield from still(a, at_after - 0.4)
    yield from blend(a, b)
    yield from still(b, duration(after) + 0.6)


def midi_scene():
    img, d = canvas("MIDI: notes chopped into fragments")
    roll = Image.open(MEDIA / "before-after/midi.webp").convert("RGB")
    paste_center(img, fit(roll, (40, HEAD + 16, W - 40, H - 120)), HEAD + 16)
    d.text(
        (W // 2, H - 70),
        "restruck-note: 26 held notes chopped into fragments. "
        "Merging where the audio shows no new attack leaves 9 runs to check by ear.",
        font=font("Regular", 19),
        fill=MUTED,
        anchor="mm",
    )
    yield from still(img, 5.0)


def end_card():
    img, d = canvas()
    d.text((W // 2, 200), "Add it to a repo", font=font("Bold", 52), fill=FG, anchor="mm")
    code = [
        "jobs:",
        "  slop-gate:",
        "    uses: mikejmckinney/slop-gate/.github/workflows/slop-gate.yml@v1",
    ]
    d.rounded_rectangle((150, 270, W - 150, 410), radius=10, fill=PANEL, outline=LINE)
    for k, line in enumerate(code):
        d.text((180, 300 + k * 34), line, font=mono(22), fill=FG)
    d.text(
        (W // 2, 480), "github.com/mikejmckinney/slop-gate", font=font("SemiBold", 28), fill=BLUE, anchor="mm"
    )
    d.text(
        (W // 2, 530),
        "Checks only the lines a PR changes. Never commits to the branch.",
        font=font("Regular", 22),
        fill=MUTED,
        anchor="mm",
    )
    yield from still(img, 4.5)


# ------------------------------------------------------------------- encoding


def encode(frames, out: Path, fade_in=True, fade_out=True) -> float:
    """Write (image, seconds) pairs as one 30 fps scene file; return its length."""
    plan = [(img, max(1, round(sec * FPS))) for img, sec in frames]
    total = sum(n for _, n in plan) / FPS
    vf = []
    if fade_in:
        vf.append(f"fade=t=in:st=0:d={FADE}:color=0x0d1117")
    if fade_out:
        vf.append(f"fade=t=out:st={total - FADE:.3f}:d={FADE}:color=0x0d1117")
    tmp = out.with_name(out.stem + ".part.mp4")
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{W}x{H}",
        "-r",
        str(FPS),
        "-i",
        "-",
    ]
    cmd += (["-vf", ",".join(vf)] if vf else []) + X264 + [str(tmp)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for img, n in plan:
        raw = img.tobytes()
        for _ in range(n):
            proc.stdin.write(raw)
    proc.stdin.close()
    if proc.wait() != 0:
        sys.exit(f"ffmpeg failed on {out.name}")
    tmp.rename(out)
    return total


def scenes():
    yield "title", list(still(title_card(), 3.5)), False
    yield "pr", list(recording("A PR from blocked to green", MEDIA / "hero.webp")), True
    yield (
        "residue",
        list(recording("Hidden characters and generation residue", MEDIA / "residue.webp", 1.2, 2.0)),
        True,
    )
    yield "scope", list(recording("Only the lines a PR changes", MEDIA / "scope.webp", 1.2, 2.0)), True
    yield "footer", list(recording("Tool footers removed from comments", MEDIA / "footer.webp", 2.2)), True
    yield "writing", list(writing_scene()), True
    yield "sprite", list(sprite_scene()), True
    yield "music", list(music_frames()), True
    yield "speech", list(speech_scene()), True
    yield "midi", list(midi_scene()), True
    yield "end", list(end_card()), True


def main(out: Path, only: list[str]) -> None:
    work = Path(tempfile.mkdtemp(prefix="hero-"))
    timeline = []
    for name, frames, fade_in in scenes():
        if only and name not in only:
            continue
        length = encode(frames, work / f"{name}.mp4", fade_in=fade_in, fade_out=name != "end")
        timeline.append((name, length))
        print(f"{name:8} {length:5.1f} s", flush=True)
    if only:
        print(f"scene files in {work}")
        return
    (work / "list.txt").write_text("".join(f"file '{n}.mp4'\n" for n, _ in timeline))
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(work / "list.txt"),
            "-c",
            "copy",
            str(work / "video.mp4"),
        ],
        check=True,
    )
    # One audio track: each scene's sound laid on at that scene's start.
    starts, t = {}, 0.0
    for name, length in timeline:
        starts[name] = t
        t += length
    music_audio(work / "music.wav")
    cues = [(starts["music"], work / "music.wav")]
    cues += [(starts["speech"] + at, path) for at, path in speech_cues()]
    inputs, chains = [], []
    for k, (at, path) in enumerate(cues, 1):
        inputs += ["-i", str(path)]
        chains.append(
            f"[{k}]aresample=48000,aformat=channel_layouts=stereo,adelay={round(at * 1000)}:all=1[a{k}]"
        )
    mix = "".join(f"[a{k}]" for k in range(1, len(cues) + 1))
    chains.append(f"{mix}amix=inputs={len(cues)}:normalize=0:duration=longest,apad,atrim=0:{t:.3f}[a]")
    tmp = out.with_name(out.stem + ".part.mp4")
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(work / "video.mp4"), *inputs]
    cmd += ["-filter_complex", ";".join(chains), "-map", "0:v", "-map", "[a]", "-c:v", "copy"]
    cmd += ["-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(tmp)]
    subprocess.run(cmd, check=True)
    tmp.rename(out)
    json.dump({"scenes": timeline, "sound": [(round(at, 2), path.name) for at, path in cues]}, sys.stdout)
    print()
    shutil.rmtree(work)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(Path(sys.argv[1]), sys.argv[2:])
