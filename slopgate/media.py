"""Deterministic image and audio checks driven by the manifest in .slop-gate.yml.

Universal checks run on every changed media file. Checks that need intent
(exact size, palette, loop) run only when a manifest rule declares it.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import numpy as np
from PIL import Image, UnidentifiedImageError

from .findings import Finding

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
AUDIO_EXTS = {".wav", ".mp3", ".ogg", ".oga", ".opus", ".flac", ".m4a", ".aac"}
PIL_FORMATS = {
    ".png": {"PNG"},
    ".jpg": {"JPEG", "MPO"},
    ".jpeg": {"JPEG", "MPO"},
    ".webp": {"WEBP"},
    ".gif": {"GIF"},
    ".bmp": {"BMP"},
}

DEFAULTS = {
    "max_file_mb": 10,
    "max_dimension": 8192,
    "max_true_peak_dbtp": -1.0,
    "max_leading_silence_ms": 500,
    "max_trailing_silence_ms": 2000,
    "loudness_tolerance": 2.0,
    "silence_threshold_dbfs": -60.0,
}
IMAGE_KEYS = {"size", "max_size", "frame", "max_colors", "pixel_scale", "pixel_art", "alpha", "tileable"}
AUDIO_KEYS = {
    "loop",
    "loudness_lufs",
    "loudness_tolerance",
    "max_true_peak_dbtp",
    "max_leading_silence_ms",
    "max_trailing_silence_ms",
    "sample_rate",
    "channels",
    "min_duration_s",
    "max_duration_s",
}
COMMON_KEYS = {"glob", "skip", "max_file_mb", "note"}


def rules_for(path: str, media_cfg: dict) -> dict:
    """Merge every manifest rule whose glob matches, in order; later rules win."""
    merged = {k: v for k, v in DEFAULTS.items()}
    merged.update({k: v for k, v in media_cfg.items() if k != "rules"})
    p = PurePosixPath(path)
    for rule in media_cfg.get("rules") or []:
        unknown = set(rule) - IMAGE_KEYS - AUDIO_KEYS - COMMON_KEYS
        if unknown:
            raise ValueError(f"media rule {rule.get('glob')!r}: unknown keys {sorted(unknown)}")
        if p.full_match(rule["glob"]):
            merged.update({k: v for k, v in rule.items() if k != "glob"})
    return merged


def _wh(value: str) -> tuple[int, int]:
    m = re.fullmatch(r"\s*(\d+)\s*[xX×]\s*(\d+)\s*", str(value))
    if not m:
        raise ValueError(f"expected WIDTHxHEIGHT, got {value!r}")
    return int(m.group(1)), int(m.group(2))


@dataclass
class _Ctx:
    path: str
    findings: list[Finding]

    def add(self, rule: str, severity: str, message: str, fix: str = "") -> None:
        self.findings.append(Finding("media", rule, severity, self.path, None, message, fix))


# ------------------------------------------------------------------ images


def check_image(abs_path: Path, rel: str, r: dict) -> list[Finding]:
    ctx = _Ctx(rel, [])
    try:
        with Image.open(abs_path) as im:
            fmt = im.format
            im.load()
            img = im.convert("RGBA")
            has_alpha_channel = im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        ctx.add("image-unreadable", "error", f"Image does not decode: {exc}.", "Re-export the file.")
        return ctx.findings

    ext = PurePosixPath(rel).suffix.lower()
    if fmt not in PIL_FORMATS.get(ext, {fmt}):
        ctx.add(
            "format-mismatch",
            "error",
            f"File is {fmt} data with a `{ext}` extension.",
            "Re-encode to the extension's format or rename the file.",
        )

    w, h = img.size
    if max(w, h) > r["max_dimension"]:
        ctx.add("too-large", "error", f"{w}×{h} exceeds the {r['max_dimension']}px limit.")
    if "size" in r and (w, h) != _wh(r["size"]):
        ctx.add(
            "size",
            "error",
            f"Image is {w}×{h}; the manifest declares {r['size']}.",
            "Resize or crop to the declared size, or update the manifest if the design changed.",
        )
    if "max_size" in r:
        mw, mh = _wh(r["max_size"])
        if w > mw or h > mh:
            ctx.add("max-size", "error", f"Image is {w}×{h}; the manifest allows at most {r['max_size']}.")
    if "frame" in r:
        fw, fh = _wh(r["frame"])
        if w % fw or h % fh:
            ctx.add(
                "frame-grid",
                "error",
                f"{w}×{h} is not a whole number of {fw}×{fh} frames; the sheet will slice wrong.",
                "Pad or crop the sheet to a multiple of the frame size.",
            )

    px = np.asarray(img)
    alpha = px[..., 3]
    if r.get("alpha") == "required" and not (has_alpha_channel and (alpha < 255).any()):
        ctx.add(
            "alpha-missing",
            "error",
            "The manifest requires transparency, but every pixel is opaque.",
            "Export with an alpha channel and a transparent background.",
        )
    if r.get("alpha") == "forbidden" and (alpha < 255).any():
        ctx.add(
            "alpha-present", "error", "The manifest forbids transparency, but some pixels are see-through."
        )

    pixel_art = bool(r.get("pixel_art"))
    if pixel_art:
        soft = int(((alpha > 0) & (alpha < 255)).sum())
        if soft:
            ctx.add(
                "soft-alpha",
                "error",
                f"{soft} semi-transparent pixels. Pixel art needs hard edges (alpha 0 or 255).",
                "Threshold the alpha channel and clean edges at native size.",
            )
    if "max_colors" in r:
        visible = px[alpha > 0]
        n = len(np.unique(visible.view(np.uint32))) if len(visible) else 0
        if n > r["max_colors"]:
            ctx.add(
                "palette",
                "error",
                f"{n} distinct colors; the manifest allows {r['max_colors']}. "
                "Extra colors usually come from anti-aliasing, gradients, or a resampled upscale.",
                "Quantize to the project palette with no dithering, then hand-fix clusters.",
            )
    if "pixel_scale" in r:
        s = int(r["pixel_scale"])
        if w % s or h % s:
            ctx.add("pixel-grid", "error", f"{w}×{h} is not divisible by the pixel scale {s}.")
        else:
            blocks = px.reshape(h // s, s, w // s, s, 4)
            uniform = (blocks == blocks[:, :1, :, :1, :]).all(axis=(1, 3, 4))
            bad = 1 - uniform.mean()
            if bad > 0.01:
                ctx.add(
                    "off-grid",
                    "error",
                    f"{bad:.0%} of {s}×{s} cells are not a single color (mixels or smoothing).",
                    "Snap to the grid with a nearest-neighbor downscale and re-upscale, then hand-fix.",
                )
    if r.get("tileable"):
        rgb = px[..., :3].astype(np.float32)
        for axis, name in ((1, "left/right"), (0, "top/bottom")):
            first = np.take(rgb, 0, axis=axis)
            last = np.take(rgb, -1, axis=axis)
            seam = float(np.abs(first - last).mean())
            interior = float(np.abs(np.diff(rgb, axis=axis)).mean())
            if seam > 2.5 * interior + 4:
                ctx.add(
                    "tile-seam",
                    "error",
                    f"Visible {name} seam when tiled (edge difference {seam:.1f} vs {interior:.1f} inside).",
                    "Make the texture wrap (offset by half and paint over the seam), then check at 3×3.",
                )
    return ctx.findings


# ------------------------------------------------------------------ audio


def _probe(path: Path) -> dict:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=sample_rate,channels,codec_name:format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "ffprobe failed")
    data = json.loads(out.stdout)
    if not data.get("streams"):
        raise RuntimeError("no audio stream")
    s = data["streams"][0]
    return {
        "sample_rate": int(s["sample_rate"]),
        "channels": int(s["channels"]),
        "duration": float(data.get("format", {}).get("duration") or 0),
        "codec": s.get("codec_name"),
    }


def _decode(path: Path, sr: int, ch: int) -> np.ndarray:
    raw = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-f",
            "f32le",
            "-acodec",
            "pcm_f32le",
            "-ac",
            str(ch),
            "-ar",
            str(sr),
            "-",
        ],
        capture_output=True,
        check=False,
    )
    if raw.returncode != 0:
        raise RuntimeError(raw.stderr.decode(errors="replace").strip() or "ffmpeg decode failed")
    return np.frombuffer(raw.stdout, dtype="<f4").reshape(-1, ch)


def _loudness(path: Path) -> tuple[float | None, float | None]:
    out = subprocess.run(
        ["ffmpeg", "-nostats", "-v", "info", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True,
        text=True,
        check=False,
    )
    summary = out.stderr.rsplit("Summary:", 1)[-1]
    lufs = re.search(r"I:\s+(-?[\d.]+|-inf) LUFS", summary)
    peak = re.search(r"True peak:\s+Peak:\s+(-?[\d.]+|-inf) dBFS", summary)

    def num(m):
        if not m or m.group(1) == "-inf":
            return None
        return float(m.group(1))

    return num(lufs), num(peak)


def check_audio(abs_path: Path, rel: str, r: dict) -> list[Finding]:
    ctx = _Ctx(rel, [])
    try:
        info = _probe(abs_path)
        pcm = _decode(abs_path, info["sample_rate"], info["channels"])
    except RuntimeError as exc:
        ctx.add("audio-unreadable", "error", f"Audio does not decode: {exc}.", "Re-export the file.")
        return ctx.findings
    sr, n = info["sample_rate"], len(pcm)
    if n == 0:
        ctx.add("audio-empty", "error", "The file decodes to zero samples.")
        return ctx.findings
    duration = n / sr

    for key, actual in (("sample_rate", sr), ("channels", info["channels"])):
        if key in r and r[key] != actual:
            ctx.add(
                key.replace("_", "-"),
                "error",
                f"{key.replace('_', ' ')} is {actual}; the manifest declares {r[key]}.",
                "Re-export with the project's audio settings so the engine doesn't resample at runtime.",
            )
    if "min_duration_s" in r and duration < r["min_duration_s"]:
        ctx.add(
            "duration",
            "error",
            f"{duration:.2f}s is shorter than the declared minimum {r['min_duration_s']}s.",
        )
    if "max_duration_s" in r and duration > r["max_duration_s"]:
        ctx.add(
            "duration",
            "error",
            f"{duration:.2f}s is longer than the declared maximum {r['max_duration_s']}s.",
        )

    mono = np.abs(pcm).max(axis=1)
    clipped = mono >= 0.999
    runs = np.diff(np.flatnonzero(np.diff(np.concatenate(([0], clipped.view(np.int8), [0])))))[::2]
    if (runs >= 3).any():
        ctx.add(
            "clipping",
            "error",
            f"{int((runs >= 3).sum())} clipped regions (3+ consecutive full-scale samples).",
            "Re-render with lower gain or a true-peak limiter; clipping can't be repaired after the fact.",
        )

    lufs, true_peak = _loudness(abs_path)
    if true_peak is not None and true_peak > r["max_true_peak_dbtp"]:
        ctx.add(
            "true-peak",
            "error" if true_peak > 0 else "warning",
            f"True peak {true_peak:+.1f} dBTP exceeds {r['max_true_peak_dbtp']:+.1f} dBTP; lossy encoding "
            "and resampling will clip it.",
            "Normalize with a true-peak limiter (ffmpeg loudnorm TP=-1).",
        )
    if "loudness_lufs" in r:
        tol = r["loudness_tolerance"]
        if duration < 1.0 or lufs is None:
            pass  # integrated loudness is undefined for very short or silent clips
        elif abs(lufs - r["loudness_lufs"]) > tol:
            ctx.add(
                "loudness",
                "error",
                f"Integrated loudness {lufs:.1f} LUFS; the manifest targets {r['loudness_lufs']} ±{tol}.",
                f"Normalize with `ffmpeg -i in -af loudnorm=I={r['loudness_lufs']}:TP=-1 out`.",
            )

    thr = 10 ** (r["silence_threshold_dbfs"] / 20)
    loud = np.flatnonzero(mono > thr)
    if len(loud) == 0:
        ctx.add("silent", "error", "The file is silent.")
        return ctx.findings
    lead_ms = loud[0] / sr * 1000
    tail_ms = (n - 1 - loud[-1]) / sr * 1000

    if r.get("loop"):
        if lead_ms > 10 or tail_ms > 10:
            ctx.add(
                "loop-gap",
                "error",
                f"Loop has {lead_ms:.0f} ms of silence at the start and {tail_ms:.0f} ms at the end, "
                "which plays as a gap every time it wraps.",
                "Trim to the musical loop points or crossfade the tail into the head before export. "
                "Prefer Ogg Vorbis/Opus over MP3 for loops; MP3 encoder padding adds a gap.",
            )
        step = np.abs(np.diff(pcm, axis=0))
        typical = float(np.percentile(step, 99.5)) if len(step) else 0.0
        jump = float(np.abs(pcm[0] - pcm[-1]).max())
        if jump > max(4 * typical, 0.05):
            ctx.add(
                "loop-click",
                "error",
                f"Waveform jumps {jump:.2f} at the wrap point (normal sample-to-sample change is "
                f"under {typical:.3f}); it will click on every loop.",
                "Cut on zero crossings or apply a short crossfade across the loop point.",
            )
        win = max(1, int(0.05 * sr))

        def rms(x):
            return float(np.sqrt(np.mean(np.square(x)) + 1e-12))

        head, tail = rms(pcm[:win]), rms(pcm[-win:])
        db = 20 * np.log10(max(head, tail) / max(min(head, tail), 1e-9))
        if db > 6:
            ctx.add(
                "loop-level-jump",
                "error",
                f"Level changes {db:.1f} dB across the loop point (last 50 ms vs first 50 ms).",
                "Match the ending to the opening, or crossfade the tail into the head.",
            )
    else:
        if lead_ms > r["max_leading_silence_ms"]:
            ctx.add(
                "leading-silence",
                "warning",
                f"{lead_ms:.0f} ms of silence before the sound starts; it plays as input lag.",
                "Trim the head to the first transient.",
            )
        if tail_ms > r["max_trailing_silence_ms"]:
            ctx.add(
                "trailing-silence",
                "warning",
                f"{tail_ms:.0f} ms of trailing silence.",
                "Trim the tail after the decay finishes.",
            )
    return ctx.findings


# ------------------------------------------------------------------ entry


def check(repo: Path, changed, excluded, media_cfg: dict) -> tuple[list[Finding], int]:
    findings: list[Finding] = []
    checked = 0
    for rel in sorted(changed):
        ext = PurePosixPath(rel).suffix.lower()
        if ext not in IMAGE_EXTS | AUDIO_EXTS or excluded(rel) or not (repo / rel).is_file():
            continue
        r = rules_for(rel, media_cfg)
        if r.get("skip"):
            continue
        checked += 1
        size_mb = (repo / rel).stat().st_size / 1_000_000
        if size_mb > r["max_file_mb"]:
            findings.append(
                Finding(
                    "media",
                    "file-size",
                    "error",
                    rel,
                    None,
                    f"{size_mb:.1f} MB exceeds the {r['max_file_mb']} MB limit.",
                    "Compress or downscale it, or raise max_file_mb for this glob.",
                )
            )
        if ext in IMAGE_EXTS:
            findings += check_image(repo / rel, rel, r)
        else:
            if shutil.which("ffmpeg") is None:
                raise RuntimeError("ffmpeg is required for audio checks")
            findings += check_audio(repo / rel, rel, r)
    return findings, checked
