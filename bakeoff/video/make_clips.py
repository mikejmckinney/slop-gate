"""Build the labeled test clips from drawn scenes and public files in docs/media.

    python bakeoff/video/make_clips.py OUT_DIR

Each scene is rendered twice from the same seed, once with its defect planted
and once clean. Writes OUT_DIR/*.mp4, labels.json next to this script, and
clips.sha256 with the hash of each file and of its decoded frames. Anything
that fails to decode or doesn't match the recipe stops the build.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import tempfile
import wave
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
TOUR = REPO / "docs" / "media" / "hero-tour.mp4"
SPEECH = REPO / "docs" / "media" / "before-after" / "speech-after.ogg"
FONT = "/usr/share/fonts/opentype/inter/Inter-Medium.otf"

W, H, FPS, SR = 640, 360, 24, 48000
PICTURE_TOL = 2  # max pixel difference treated as "the same" when auditing rendered frames


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT, size)


def rng(seed: int) -> np.random.Generator:
    return np.random.default_rng(seed)


def noise_layer(seed: int, w: int, h: int, cell: int, amp: float) -> np.ndarray:
    """Soft noise in [-amp, amp], blown up from a coarse grid."""
    cw, ch = max(1, w // cell), max(1, h // cell)
    coarse = rng(seed).uniform(-1, 1, (ch, cw))
    im = Image.fromarray(np.uint8((coarse + 1) * 127.5), "L").resize((w, h), Image.BILINEAR)
    return (np.asarray(im, float) / 127.5 - 1) * amp


def textured(seed: int, w: int, h: int, rgb: tuple[int, int, int], amp: float) -> np.ndarray:
    out = np.empty((h, w, 3), float)
    layer = noise_layer(seed, w, h, 6, amp) + noise_layer(seed + 1, w, h, 2, amp / 2)
    for c in range(3):
        out[:, :, c] = rgb[c] + layer
    return out.clip(0, 255)


def lerp(a: float, b: float, p: float) -> float:
    return a + (b - a) * p


def mix(a: tuple[int, int, int], b: tuple[int, int, int], p: float) -> tuple[int, int, int]:
    return tuple(int(lerp(x, y, p)) for x, y in zip(a, b, strict=True))  # type: ignore[return-value]


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def to_image(arr: np.ndarray) -> Image.Image:
    return Image.fromarray(np.uint8(arr.clip(0, 255)), "RGB")


@dataclass
class Scene:
    id: str
    seconds: float
    brief: str
    render: Callable[[int, float, bool], Image.Image]
    pattern: str | None = None  # the antipattern row the plant belongs to; None for a clean control
    start: float = 0.0
    end: float = 0.0
    note: str = ""
    audio: Callable[[bool], np.ndarray] | None = None  # mono float samples at SR
    needs_audio: bool = False  # the defect can only be found by hearing the track
    affected: tuple[float, float] | None = None  # span where the pair may differ; None means the label span
    tags: list[str] = field(default_factory=list)


def flicker(i: int, t: float, defect: bool) -> Image.Image:
    wall = textured(11, W, 260, (128, 110, 96), 22)
    arr = np.empty((H, W, 3), float)
    arr[:260] = wall
    arr[260:] = (70, 66, 62)
    for y in range(0, 260, 40):
        arr[y : y + 2, :] = 90
    for row, y in enumerate(range(0, 260, 40)):
        for x in range(0 if row % 2 else 50, W, 100):
            arr[y : y + 40, x : x + 2] = 90
    if defect and 3.0 <= t < 7.0:
        for c in range(3):
            arr[50:210, 80:420, c] += noise_layer(1000 + i, 340, 160, 4, 40)
    im = to_image(arr)
    d = ImageDraw.Draw(im)
    x = -60 + 76 * t
    d.rounded_rectangle((x, 268, x + 70, 306), 6, fill=(40, 90, 200))
    d.ellipse((x + 8, 298, x + 28, 318), fill=(25, 25, 30))
    d.ellipse((x + 44, 298, x + 64, 318), fill=(25, 25, 30))
    return im


def drift(i: int, t: float, defect: bool) -> Image.Image:
    arr = textured(21, W, H, (54, 62, 78), 10)
    im = to_image(arr)
    d = ImageDraw.Draw(im)
    d.rectangle((0, 300, W, H), fill=(44, 44, 52))
    p = clamp01((t - 4.0) / 2.0) if defect else 0.0
    body = mix((88, 176, 188), (214, 122, 48), p)
    bob = 5 * math.sin(t * 2.2)
    head_w = lerp(60, 40, p)
    cx, y = 320, 150 + bob
    d.rounded_rectangle((cx - 45, y + 44, cx + 45, y + 150), 10, fill=body)
    d.rounded_rectangle((cx - head_w, y - 40, cx + head_w, y + 38), 16, fill=body)
    d.rectangle((cx - 4, y - 40 - lerp(40, 12, p), cx + 4, y - 40), fill=(220, 220, 225))
    eye = mix((120, 240, 150), (240, 70, 70), p)
    for sx in (-24, 24):
        if p > 0.5:
            d.rectangle((cx + sx - 11, y - 14, cx + sx + 11, y + 8), fill=eye)
        else:
            d.ellipse((cx + sx - 12, y - 14, cx + sx + 12, y + 10), fill=eye)
    arm = math.sin(t * 3.0) * 0.6 - 0.8
    ax, ay = cx + 46, y + 60
    d.line((ax, ay, ax + 80 * math.cos(arm), ay + 80 * math.sin(arm)), fill=body, width=14)
    return im


def occluder(i: int, t: float, defect: bool) -> Image.Image:
    arr = textured(31, W, H, (92, 138, 92), 12)
    im = to_image(arr)
    d = ImageDraw.Draw(im)
    d.rectangle((0, 280, W, H), fill=(80, 96, 72))
    x = -40 + 74 * t
    hidden_for_good = defect and x > 320
    if not hidden_for_good:
        d.ellipse((x - 24, 240, x + 24, 288), fill=(210, 50, 50))
    d.rectangle((270, 60, 370, 300), fill=(120, 124, 134))
    return im


def road(i: int, t: float, defect: bool) -> Image.Image:
    arr = textured(41, W, H, (132, 168, 204), 8)
    im = to_image(arr)
    d = ImageDraw.Draw(im)
    d.rectangle((0, 240, W, 300), fill=(74, 74, 80))
    d.rectangle((0, 300, W, H), fill=(86, 120, 72))
    for x in range(10, W, 70):
        d.rectangle((x, 268, x + 34, 272), fill=(230, 230, 120))
    x = 10 + 44 * t + (170 if defect and t >= 4.0 else 0)
    d.rounded_rectangle((x, 216, x + 90, 262), 8, fill=(46, 150, 80))
    d.ellipse((x + 12, 252, x + 34, 274), fill=(24, 24, 28))
    d.ellipse((x + 58, 252, x + 80, 274), fill=(24, 24, 28))
    return im


def train_x(t: float, defect: bool) -> float:
    forward = 46
    if not defect or t < 4.0:
        return 10 + forward * t
    if t < 5.5:
        return 10 + forward * 4.0 - 90 * (t - 4.0)
    return 10 + forward * 4.0 - 135 + forward * (t - 5.5)


def train(i: int, t: float, defect: bool) -> Image.Image:
    arr = textured(51, W, H, (176, 158, 126), 10)
    im = to_image(arr)
    d = ImageDraw.Draw(im)
    d.rectangle((0, 262, W, 270), fill=(60, 60, 64))
    for tie in range(6, W, 28):
        d.rectangle((tie, 262, tie + 6, 276), fill=(90, 66, 44))
    x = train_x(t, defect)
    for k, car in enumerate((x, x - 86, x - 172)):
        d.rectangle((car, 198, car + 78, 258), fill=(160, 40, 40) if k == 0 else (120, 70, 50))
        d.ellipse((car + 10, 250, car + 30, 270), fill=(24, 24, 28))
        d.ellipse((car + 48, 250, car + 68, 270), fill=(24, 24, 28))
    return im


def tower_blocks(t: float, fall_start: float) -> list[tuple[float, float, float, float]]:
    """Four blocks, stacked, then tipping to the right over a second and lying flat."""
    p = clamp01((t - fall_start) / 1.0)
    out = []
    for k in range(4):
        base_x, base_y = 430.0, 272.0 - k * 44
        fx, fy = base_x + (60 + k * 52) * p, lerp(base_y, 282.0, p * p)
        out.append((fx, fy, fx + 48, fy + 42))
    return out


def domino(i: int, t: float, defect: bool) -> Image.Image:
    arr = textured(61, W, H, (152, 168, 188), 8)
    im = to_image(arr)
    d = ImageDraw.Draw(im)
    d.rectangle((0, 316, W, H), fill=(92, 84, 76))
    ball_end = 408.0  # its right edge touches the tower's left edge at 5.0 s
    x = min(ball_end, 20 + 77.6 * t)
    d.ellipse((x - 22, 270, x + 22, 314), fill=(70, 110, 220))
    fall_start = 3.8 if defect else 5.0
    for k, (a, b, c, e) in enumerate(tower_blocks(t, fall_start)):
        d.rectangle((a, b, c, e), fill=mix((230, 150, 50), (200, 70, 60), k / 3))
    return im


def _decode(start: float, seconds: float) -> np.ndarray:
    raw = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            str(start),
            "-t",
            str(seconds),
            "-i",
            str(TOUR),
            "-vf",
            f"fps={FPS},scale={W}:{H}",
            "-an",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        check=True,
        capture_output=True,
    ).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(-1, H, W, 3)
    if len(frames) < int(seconds * FPS) - 1:
        raise SystemExit(f"short decode of tour at {start}: {len(frames)} frames")
    return frames


_cache: dict[tuple[float, float], np.ndarray] = {}


def tour_frame(start: float, seconds: float, i: int) -> Image.Image:
    key = (start, seconds)
    if key not in _cache:
        _cache[key] = _decode(start, seconds)
    frames = _cache[key]
    return Image.fromarray(frames[min(i, len(frames) - 1)], "RGB")


CAPTION = "Checking the loop point"
MUTATIONS = ["Checkimg the lpoo point", "Chacking tha loop poimt", "Ch3cking the 1oop p0int"]


def text_overlay(i: int, t: float, defect: bool) -> Image.Image:
    """The recording scaled down, with the caption in its own bar so it covers no interface text."""
    im = Image.new("RGB", (W, H), (10, 12, 16))
    im.paste(tour_frame(62.0, 9.0, i).resize((562, 316), Image.BICUBIC), (39, 0))
    d = ImageDraw.Draw(im)
    text = CAPTION
    if defect and 4.0 <= t < 6.5:
        text = MUTATIONS[int((t - 4.0) * 4) % len(MUTATIONS)]
    d.text((320, 338), text, font=font(20), fill=(240, 240, 240), anchor="mm")
    return im


def screen(i: int, t: float, defect: bool) -> Image.Image:
    return tour_frame(24.0, 8.0, i)


CONTACTS = [0.75 + 1.5 * k for k in range(6)]


def bounce(i: int, t: float, defect: bool) -> Image.Image:
    arr = textured(71, W, H, (64, 58, 72), 8)
    im = to_image(arr)
    d = ImageDraw.Draw(im)
    d.rectangle((0, 300, W, H), fill=(140, 100, 60))
    phase = ((t - 0.75) / 1.5) % 1.0
    height = 4 * phase * (1 - phase)  # 0 at the contact, 1 at the top
    y = 278 - 190 * height
    x = 60 + 52 * t
    d.ellipse((x - 22, y - 22, x + 22, y + 22), fill=(240, 180, 60))
    return im


def bounce_audio(defect: bool) -> np.ndarray:
    n = int(10 * SR)
    a = np.zeros(n)
    click = np.exp(-np.linspace(0, 40, int(0.08 * SR))) * np.sin(
        2 * np.pi * 180 * np.linspace(0, 0.08, int(0.08 * SR))
    )
    click += 0.4 * rng(77).uniform(-1, 1, click.size) * np.exp(-np.linspace(0, 60, click.size))
    for at in CONTACTS:
        placed = at + 0.5 if defect and abs(at - 5.25) < 0.01 else at
        k = int(placed * SR)
        a[k : k + click.size] += click * 0.8
    return a


def _speech(lead: float, seconds: float) -> np.ndarray:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(SPEECH), "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
        check=True,
        capture_output=True,
    ).stdout
    s = np.frombuffer(raw, np.float32).astype(float)
    if s.size < SR:
        raise SystemExit("speech file decoded to under a second")
    out = np.zeros(int(seconds * SR))
    k = int(lead * SR)
    out[k : k + s.size] = s[: out.size - k]
    return out


def narrated(i: int, t: float, defect: bool) -> Image.Image:
    im = to_image(textured(81, W, H, (22, 28, 44), 6))
    d = ImageDraw.Draw(im)
    if t < 4.3:
        d.text((320, 90), "Tap and hold", font=font(30), fill=(235, 235, 235), anchor="mm")
        charge = clamp01((t - 1.0) / 1.5)
        d.rectangle((170, 150, 470, 182), outline=(200, 200, 200), width=2)
        d.rectangle((172, 152, 172 + 296 * charge, 180), fill=(90, 200, 120))
        if t >= 2.6:
            d.text((320, 240), "Release to fire", font=font(30), fill=(250, 200, 80), anchor="mm")
    else:
        wave_txt, dist = "WAVE 3", "400 m" if defect else "1,250 m"
        if not defect:
            wave_txt = "WAVE 7"
        d.text((320, 120), wave_txt, font=font(54), fill=(240, 240, 240), anchor="mm")
        d.text((320, 210), dist, font=font(40), fill=(120, 200, 250), anchor="mm")
    return im


def door(i: int, t: float, defect: bool) -> Image.Image:
    arr = textured(91, W, H, (168, 152, 132), 8)
    im = to_image(arr)
    d = ImageDraw.Draw(im)
    d.rectangle((0, 320, W, H), fill=(90, 70, 56))
    p = clamp01((t - 4.0) / 1.0)
    d.rectangle((240, 90, 400, 320), fill=(30, 30, 36))
    width = lerp(160, 24, p)
    d.polygon(
        [(240, 90), (240 + width, 90 + 30 * p), (240 + width, 320 - 30 * p), (240, 320)], fill=(170, 40, 40)
    )
    if 3.5 <= t < 7.5:
        cap = "The door stays shut." if defect else "The door swings open."
        d.rectangle((150, 330, 490, 356), fill=(0, 0, 0))
        d.text((320, 343), cap, font=font(18), fill=(255, 255, 255), anchor="mm")
    return im


def meadow_frame(t: float) -> np.ndarray:
    arr = np.empty((H, W, 3), float)
    grad = np.linspace(0, 1, 240)[:, None]
    for c, (top, low) in enumerate(zip((96, 150, 214), (190, 216, 238), strict=True)):
        arr[:240, :, c] = lerp(top, low, 0) * (1 - grad) + low * grad
    arr[240:] = textured(101, W, 120, (92, 148, 74), 14)
    im = to_image(arr)
    d = ImageDraw.Draw(im)
    for cx, cy in ((60, 70), (240, 110), (430, 60), (560, 120)):
        x = (cx + 18 * t) % (W + 120) - 60
        for dx, r in ((-34, 22), (-8, 32), (26, 24)):
            d.ellipse((x + dx - r, cy - r, x + dx + r, cy + r), fill=(245, 245, 248))
    x = 40 + 36 * t
    d.rectangle((x, 250, x + 30, 300), fill=(120, 70, 40))
    return np.asarray(im, float)


def seam(i: int, t: float, defect: bool) -> Image.Image:
    arr = meadow_frame(t)
    if defect and t >= 5.0:
        arr = arr * np.array((1.22, 1.08, 0.82)) + noise_layer(2000 + i, W, H, 1, 9)[:, :, None]
    return to_image(arr)


def star(i: int, t: float, defect: bool) -> Image.Image:
    arr = textured(111, W, H, (20, 24, 56), 6)
    im = to_image(arr)
    d = ImageDraw.Draw(im)
    for k in range(40):
        x = (37 * k + 11) % W
        y = (53 * k + 7) % 240
        d.ellipse((x, y, x + 2, y + 2), fill=(200, 200, 220))
    x = -30 + 70 * t
    y = 150 + 40 * math.sin(t * 1.3)
    if not (defect and 80 <= i <= 82):
        d.polygon(
            [
                (
                    x + 26 * math.cos(math.pi * a / 5) * (1 if a % 2 == 0 else 0.45),
                    y + 26 * math.sin(math.pi * a / 5) * (1 if a % 2 == 0 else 0.45),
                )
                for a in range(10)
            ],
            fill=(250, 214, 80),
        )
    return im


def park(i: int, t: float, defect: bool) -> Image.Image:
    arr = textured(121, W, H, (116, 176, 212), 8)
    im = to_image(arr)
    d = ImageDraw.Draw(im)
    d.rectangle((0, 250, W, H), fill=(90, 150, 80))
    d.rectangle((70, 150, 92, 260), fill=(90, 60, 40))
    d.ellipse((30, 70, 130, 180), fill=(44, 110, 60))
    x = 120 + 44 * t
    d.ellipse((x, 256, x + 34, 290), fill=(230, 90, 120))
    d.rectangle((x + 8, 290, x + 26, 330), fill=(60, 60, 110))
    if defect and 102 <= i <= 104:
        d.rectangle((300, 130, 360, 190), fill=(40, 200, 90))
    return im


def stopmotion(i: int, t: float, defect: bool) -> Image.Image:
    stepped = (i // 4) / 6
    arr = textured(131, W, H, (196, 176, 150), 8)
    im = to_image(arr)
    d = ImageDraw.Draw(im)
    d.rectangle((0, 300, W, H), fill=(120, 96, 70))
    x = 40 + 44 * stepped
    d.rectangle((x, 220, x + 70, 300), fill=(200, 80, 60))
    d.ellipse((x + 12, 180, x + 58, 226), fill=(240, 210, 170))
    return im


def jumpcut(i: int, t: float, defect: bool) -> Image.Image:
    arr = textured(141, W, H, (180, 196, 150), 8)
    im = to_image(arr)
    d = ImageDraw.Draw(im)
    d.rectangle((0, 290, W, H), fill=(100, 110, 80))
    shot_b = t >= 5.0
    x = (200 + 26 * (t - 5.0) + 180) if shot_b else 60 + 26 * t
    d.ellipse((x - 26, 236, x + 26, 288), fill=(230, 130, 40))
    d.rectangle((x - 40, 150, x - 30, 236), fill=(70, 60, 50))
    return im


def handheld(i: int, t: float, defect: bool) -> Image.Image:
    arr = meadow_frame(t)
    dx = int(5 * math.sin(i * 0.73) + 3 * math.sin(i * 1.9))
    dy = int(3 * math.sin(i * 0.51) + 2 * math.cos(i * 1.3))
    arr = np.roll(arr, (dy, dx), axis=(0, 1))
    arr = arr + noise_layer(3000 + i, W, H, 1, 7)[:, :, None]
    return to_image(arr)


SCENES = [
    Scene(
        "flicker",
        10,
        "A blue cart rolls left to right in front of a static brick wall. The camera doesn't move.",
        flicker,
        "Flicker or texture boil",
        3.0,
        7.0,
        "A 340x160 patch of the wall boils for four seconds.",
    ),
    Scene(
        "identity_drift",
        10,
        "A robot stands in a room and waves one arm. It is the same robot throughout.",
        drift,
        "Identity or background drift",
        4.0,
        6.0,
        "Head and body color, eye shape and antenna morph over two seconds and stay changed.",
        affected=(4.0, 10.0),
    ),
    Scene(
        "object_vanish",
        10,
        "A red ball rolls left to right, passes behind a grey pillar, and keeps going to the right edge.",
        occluder,
        "Objects appear, vanish, or merge",
        5.2,
        8.0,
        "The ball goes behind the pillar and never comes out.",
        affected=(5.0, 10.0),
    ),
    Scene(
        "action_skip",
        10,
        "A green car drives left to right along a road at a constant speed.",
        road,
        "Action stalls, reverses, or skips",
        3.75,
        4.25,
        "The car jumps 170 px forward between two frames at 4.0 s, and keeps going from there.",
        affected=(4.0, 10.0),
    ),
    Scene(
        "action_reverse",
        10,
        "A train drives left to right along a track without stopping.",
        train,
        "Action stalls, reverses, or skips",
        4.0,
        5.5,
        "The train runs backward for 1.5 s, then forward again from further back.",
        affected=(4.0, 10.0),
    ),
    Scene(
        "cause_effect",
        9,
        "A blue ball rolls right, hits a tower of orange blocks, and the tower falls over from the hit.",
        domino,
        "Broken cause and effect",
        3.8,
        5.0,
        "The tower falls a second before the ball arrives at 5.0 s.",
        affected=(3.8, 6.0),
    ),
    Scene(
        "text_mutate",
        8,
        "A screen recording of a music waveform tool with one caption at the bottom that stays the same.",
        text_overlay,
        "Unstable or unreadable overlays",
        4.0,
        6.5,
        "The caption's letters change for 2.5 s on a real recording from docs/media.",
    ),
    Scene(
        "sound_off",
        10,
        "A ball bounces across a wooden floor.",
        bounce,
        "Sound event misses picture",
        5.25,
        5.85,
        "One bounce sounds 0.5 s after the ball lands. The other five are in sync.",
        audio=bounce_audio,
        needs_audio=True,
        affected=(5.25, 5.85),
    ),
    Scene(
        "narration_mismatch",
        9,
        "A narrated game tutorial slide.",
        narrated,
        "Narration-picture mismatch",
        4.7,
        8.35,
        "Narration says wave seven in 1,250 meters; the picture shows wave 3 and 400 m.",
        audio=lambda defect: _speech(1.0, 9.0),
        needs_audio=True,
        affected=(4.3, 9.0),
    ),
    Scene(
        "caption_mismatch",
        9,
        "A red door swings open in a hallway, with a one-line caption describing it.",
        door,
        "Narration-picture mismatch",
        4.0,
        7.5,
        'The caption says "The door stays shut." while the door opens.',
        affected=(3.5, 7.5),
    ),
    Scene(
        "clip_seam",
        10,
        "One continuous shot: clouds drift over a meadow while a small cart crosses it.",
        seam,
        "Visible clip seams",
        4.75,
        5.25,
        "Exposure, color and grain jump at 5.0 s, as at a poor extension join.",
        affected=(5.0, 10.0),
    ),
    Scene(
        "brief_gap",
        10,
        "A yellow star moves across a night sky and stays on screen until it leaves.",
        star,
        "Objects appear, vanish, or merge",
        3.333,
        3.458,
        "The star is gone for frames 80-82 (3 frames, 125 ms), between the 4 fps samples.",
        tags=["brief"],
    ),
    Scene(
        "brief_dense",
        10,
        "A child in pink walks across a park past a tree.",
        park,
        "Objects appear, vanish, or merge",
        4.25,
        4.375,
        "A green cube is visible for frames 102-104 (3 frames, 125 ms), on one 4 fps sample.",
        tags=["brief"],
    ),
]

CONTROLS = [
    Scene(
        "ctl_stopmotion",
        8,
        "Stop-motion puppet animation. The stepped timing, about 6 poses a second, is deliberate.",
        stopmotion,
    ),
    Scene(
        "ctl_jumpcut",
        10,
        "One subject shown in two shots with a deliberate jump cut at 5 seconds.",
        jumpcut,
    ),
    Scene(
        "ctl_handheld",
        9,
        "A meadow shot with deliberate handheld camera shake and film grain.",
        handheld,
    ),
    Scene("ctl_screen", 8, "A silent terminal screen recording.", screen),
]


def encode(frames: np.ndarray, audio: np.ndarray | None, out: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "a.wav"
        cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24"]
        cmd += ["-s", f"{W}x{H}", "-r", str(FPS), "-i", "-"]
        if audio is not None:
            pcm = (audio * 32767).clip(-32768, 32767).astype(np.int16)
            with wave.open(str(wav), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(SR)
                wf.writeframes(pcm.tobytes())
            cmd += ["-i", str(wav), "-c:a", "aac", "-b:a", "96k", "-flags:a", "+bitexact"]
        cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p"]
        cmd += ["-threads", "1", "-x264-params", "threads=1:sliced-threads=0"]
        cmd += ["-map_metadata", "-1", "-fflags", "+bitexact", "-flags:v", "+bitexact", str(out)]
        subprocess.run(cmd, input=frames.tobytes(), check=True)


def render(scene: Scene, defect: bool) -> np.ndarray:
    n = int(round(scene.seconds * FPS))
    return np.stack([np.asarray(scene.render(i, i / FPS, defect), np.uint8) for i in range(n)])


def audit(scene: Scene, bad: np.ndarray, good: np.ndarray) -> dict:
    """Stop if the planted difference isn't where the recipe says it is."""
    per_frame = np.abs(bad.astype(int) - good.astype(int)).max(axis=(1, 2, 3))
    differ = np.flatnonzero(per_frame > PICTURE_TOL)
    if not differ.size and scene.audio is None:
        raise SystemExit(f"{scene.id}: defect render equals the clean render")
    info = {"picture_differs_s": None}
    if differ.size:
        span = (differ[0] / FPS, (differ[-1] + 1) / FPS)
        info["picture_differs_s"] = [round(span[0], 3), round(span[1], 3)]
        lo, hi = scene.affected or (scene.start, scene.end)
        if span[0] < lo - 1 / FPS or span[1] > hi + 1 / FPS:
            raise SystemExit(f"{scene.id}: picture differs over {span}, recipe says {lo}-{hi}")
    return info


def frame_md5(path: Path) -> str:
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True,
        capture_output=True,
    ).stdout
    if not out:
        raise SystemExit(f"{path}: no decoded frames")
    return hashlib.md5(out).hexdigest()


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return float(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    labels, lines = [], []

    def add(name: str, scene: Scene, kind: str, frames: np.ndarray, audio, extra: dict) -> None:
        path = args.out / f"{name}.mp4"
        encode(frames, audio, path)
        lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {frame_md5(path)}  {path.name}")
        entry = {"clip": name, "kind": kind, "group": scene.id, "seconds": probe_duration(path)}
        entry.update({"brief": scene.brief, "has_audio": audio is not None, "file": f"{name}.mp4"})
        entry.update(extra)
        labels.append(entry)

    for scene in SCENES:
        bad, good = render(scene, True), render(scene, False)
        info = audit(scene, bad, good)
        audio_bad = scene.audio(True) if scene.audio else None
        audio_good = scene.audio(False) if scene.audio else None
        defect = {
            "pattern": scene.pattern,
            "start_s": scene.start,
            "end_s": scene.end,
            "note": scene.note,
            "needs_audio": scene.needs_audio,
            "tags": scene.tags,
            **info,
        }
        add(f"{scene.id}_defect", scene, "defect", bad, audio_bad, defect)
        add(f"{scene.id}_clean", scene, "clean_sibling", good, audio_good, {})

    for scene in CONTROLS:
        add(scene.id, scene, "control", render(scene, False), None, {})

    (HERE / "labels.json").write_text(json.dumps(labels, indent=1) + "\n")
    (HERE / "clips.sha256").write_text("\n".join(sorted(lines)) + "\n")
    total = sum(p.stat().st_size for p in args.out.glob("*.mp4"))
    print(f"{len(labels)} clips, {total / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
