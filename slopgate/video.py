"""Deterministic video checks from the video antipattern list.

These read stream metadata and frame statistics: whether the file decodes,
whether its streams agree with each other and with the manifest, and, when the
manifest asks, where the picture freezes or goes black and whether the last
frame runs into the first. Flicker, drift, motion, physics and lip-sync need a
person watching at playback speed, and nothing here looks at them.

Only eight real videos were available to set thresholds, too few to trust a
threshold enough to block on it. So only an undecodable file and a broken
manifest declaration are errors; every statistic is a warning.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .audio_defects import clock
from .findings import Finding

VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv", ".m4v"}
# `size`, `loop` and `max_duration_s` already exist for images or audio and mean the same thing here.
VIDEO_KEYS = {"fps", "codec", "max_freeze_s", "max_black_s"}
AUDIO_KEYS_SHARED_WITH_VIDEO = {"loop", "max_duration_s"}

FRAME_RATE_TOLERANCE = 0.01
STREAM_LENGTH_TOLERANCE_S = 0.5
CODEC_ALIASES = {"avc": "h264", "avc1": "h264", "h265": "hevc"}
FREEZE_NOISE = "-60dB"
BLACK_PIXEL_THRESHOLD = 0.1
LOOP_FRAME_SIZE = (96, 54)
LOOP_STEP_PERCENTILE = 95
LOOP_JUMP_RATIO, LOOP_JUMP_FLOOR = 2.5, 4.0  # the shape of the tile-seam test in media.py

# Only the containers VIDEO_EXTS names, and only from disk. Left to itself
# ffmpeg picks a demuxer from the file's bytes, and a playlist or concat script
# renamed .mp4 can make it open whatever the script lists.
INPUT_GUARD = ["-format_whitelist", "mov,matroska", "-protocol_whitelist", "file"]

# Anchored to the start of a line so that a file's own metadata tag can't pose as ffmpeg's output.
_SUMMARY = re.compile(r"^(?:\[[^\]]*\] )?video:\s*\d+", re.M)
_FREEZE = re.compile(
    r"^\[[^\]]*freezedetect[^\]]*\] lavfi\.freezedetect\.(freeze_start|freeze_end): (\S+)", re.M
)
_BLACK = re.compile(
    r"^\[[^\]]*blackdetect[^\]]*\] black_start:(\S+) black_end:(\S+) black_duration:(\S+)", re.M
)


def validate_rule(rule: dict) -> None:
    """Reject malformed video keys when the manifest loads, as an unknown key is."""
    for key in ("fps", "max_duration_s", "max_freeze_s", "max_black_s"):
        value = rule.get(key)
        if key in rule and (isinstance(value, bool) or not isinstance(value, int | float) or value <= 0):
            raise ValueError(
                f"media rule {rule.get('glob')!r}: {key} must be a positive number, got {value!r}"
            )
    if "codec" in rule and not (isinstance(rule["codec"], str) and rule["codec"].strip()):
        raise ValueError(
            f"media rule {rule.get('glob')!r}: codec must be a codec name, got {rule['codec']!r}"
        )


def _run(cmd: list[str], *, text: bool = True) -> subprocess.CompletedProcess:
    try:
        if text:  # ffmpeg logs a file's own tags, and those can be any bytes
            return subprocess.run(cmd, capture_output=True, text=True, errors="replace", check=False)
        return subprocess.run(cmd, capture_output=True, check=False)
    except OSError as exc:
        raise RuntimeError(f"{cmd[0]} could not start ({exc})") from exc


def _first_line(stderr: str, fallback: str) -> str:
    lines = stderr.strip().splitlines()
    return lines[0][:200] if lines else fallback


def _ffprobe_json(cmd: list[str]) -> tuple[dict, str]:
    """(parsed output, stderr). Raises RuntimeError unless ffprobe answered with a JSON object."""
    out = _run(cmd)
    if out.returncode != 0:
        raise RuntimeError(_first_line(out.stderr, f"ffprobe exited {out.returncode}"))
    try:
        data = json.loads(out.stdout)
    except ValueError as exc:
        raise RuntimeError(f"ffprobe printed something other than JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise RuntimeError("ffprobe printed JSON that is not an object")
    return data, out.stderr


def _rate(value) -> float | None:
    """A rate such as "30000/1001"; None for "0/0", a missing field or garbage."""
    m = re.fullmatch(r"(\d+)/(\d+)", str(value))
    return int(m.group(1)) / int(m.group(2)) if m and int(m.group(1)) and int(m.group(2)) else None


def _number(value, kind=float):
    """value as a finite number; None for "N/A", a missing field or garbage."""
    try:
        parsed = kind(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _seconds(stream: dict) -> float | None:
    """A stream's length. Matroska and WebM only carry it as a DURATION tag."""
    direct = _number(stream.get("duration"))
    if direct is not None:
        return direct
    m = re.fullmatch(r"(\d+):(\d+):(\d+(?:\.\d+)?)", str((stream.get("tags") or {}).get("DURATION", "")))
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else None


def probe(path: Path) -> dict:
    """Facts about the first real video stream and first audio stream. Raises RuntimeError
    when ffprobe fails, prints something unusable, or finds no video stream."""
    data, _ = _ffprobe_json(
        [
            "ffprobe",
            "-v",
            "error",
            *INPUT_GUARD,
            "-show_entries",
            "stream=index,codec_type,codec_name,pix_fmt,width,height,r_frame_rate,avg_frame_rate,duration"
            ":stream_tags=DURATION:stream_disposition=attached_pic:format=duration",
            "-of",
            "json",
            str(path),
        ]
    )
    streams = data.get("streams")
    if not isinstance(streams, list):
        raise RuntimeError("ffprobe listed no streams")
    streams = [s for s in streams if isinstance(s, dict)]
    # Cover art is a video stream to ffmpeg, and a song with a cover is not a video.
    pictures = [
        s
        for s in streams
        if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")
    ]
    if not pictures:
        raise RuntimeError("no video stream")
    v = pictures[0]
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if _number(v.get("index"), int) is None:
        raise RuntimeError("ffprobe gave no stream index")
    return {
        "index": int(v["index"]),
        "codec": v.get("codec_name"),
        "pix_fmt": v.get("pix_fmt"),
        "width": _number(v.get("width"), int),
        "height": _number(v.get("height"), int),
        "r_fps": _rate(v.get("r_frame_rate")),
        "avg_fps": _rate(v.get("avg_frame_rate")),
        "video_s": _seconds(v),
        "audio_index": _number(audio.get("index"), int) if audio else None,
        "has_audio": audio is not None,
        "audio_s": _seconds(audio) if audio else None,
        "duration": _number((data.get("format") or {}).get("duration")),
    }


def _decode_all_frames(path: Path, index: int) -> None:
    """Decode every frame. Headers alone say nothing: a truncated MP4 with its index
    at the front probes clean, exits 0 here, and only logs what it couldn't read."""
    data, stderr = _ffprobe_json(
        [
            "ffprobe",
            "-v",
            "error",
            *INPUT_GUARD,
            "-count_frames",
            "-select_streams",
            str(index),
            "-show_entries",
            "stream=nb_read_frames",
            "-of",
            "json",
            str(path),
        ]
    )
    if stderr.strip():
        raise RuntimeError(_first_line(stderr, "decoder errors"))
    streams = data.get("streams")
    first = streams[0] if isinstance(streams, list) and streams else None
    frames = _number(first.get("nb_read_frames"), int) if isinstance(first, dict) else None
    if not frames:
        raise RuntimeError("ffprobe decoded no frames")


def _filter_log(path: Path, index: int, vf: str) -> str:
    """Run one video filter over the file and return ffmpeg's log. ffmpeg logs nothing
    extra when a filter finds nothing, so a clean result has to prove the run finished."""
    out = _run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-nostats",
            "-v",
            "info",
            *INPUT_GUARD,
            "-i",
            str(path),
            "-map",
            f"0:{index}",
            "-vf",
            vf,
            "-f",
            "null",
            "-",
        ]
    )
    if out.returncode != 0:
        raise RuntimeError(_first_line(out.stderr, f"ffmpeg exited {out.returncode}"))
    if not _SUMMARY.search(out.stderr):
        raise RuntimeError("ffmpeg finished without its end-of-run summary")
    return out.stderr


def freezes(path: Path, index: int, min_s: float, video_s: float | None) -> list[tuple[float, float | None]]:
    """(start s, length s) of each frozen stretch. freezedetect logs no end for a
    freeze that runs off the end of the file, so the stream's length closes it."""
    log = _filter_log(path, index, f"freezedetect=n={FREEZE_NOISE}:d={min_s}")
    found: list[tuple[float, float | None]] = []
    start = None
    try:
        for kind, value in _FREEZE.findall(log):
            if kind == "freeze_start":
                start = float(value)
            elif start is not None:
                found.append((start, float(value) - start))
                start = None
    except ValueError as exc:
        raise RuntimeError(f"ffmpeg logged an unreadable freeze time ({exc})") from exc
    if start is not None:
        found.append((start, video_s - start if video_s is not None else None))
    return found


def black_stretches(path: Path, index: int, min_s: float) -> list[tuple[float, float]]:
    """(start s, length s) of each black stretch at least min_s long."""
    log = _filter_log(path, index, f"blackdetect=d={min_s}:pix_th={BLACK_PIXEL_THRESHOLD}")
    try:
        return [(float(start), float(length)) for start, _, length in _BLACK.findall(log)]
    except ValueError as exc:
        raise RuntimeError(f"ffmpeg logged an unreadable black time ({exc})") from exc


def loop_seam(path: Path, index: int) -> tuple[float, float]:
    """(difference between last and first frame, the clip's normal frame-to-frame step).

    Both are the mean absolute difference of downscaled RGB frames, 0 to 255.
    A fixed PSNR or SSIM cut-off on the first and last frame can't separate a
    fast seamless loop from a jump, because both frames differ in either case.
    Comparing the wrap with the clip's own motion can, the same way tile-seam
    compares a texture's edge with its interior. The 95th percentile lets a few
    hard cuts inside the clip go without raising the bar much.
    """
    w, h = LOOP_FRAME_SIZE
    frame_bytes = w * h * 3
    out = _run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            *INPUT_GUARD,
            "-i",
            str(path),
            "-map",
            f"0:{index}",
            "-vf",
            f"scale={w}:{h},format=rgb24",
            "-f",
            "rawvideo",
            "-",
        ],
        text=False,
    )
    if out.returncode != 0:
        raise RuntimeError(
            _first_line(out.stderr.decode(errors="replace"), f"ffmpeg exited {out.returncode}")
        )
    if not out.stdout or len(out.stdout) % frame_bytes:
        raise RuntimeError(f"ffmpeg returned {len(out.stdout)} bytes, not whole {w}x{h} frames")
    frames = np.frombuffer(out.stdout, dtype=np.uint8).reshape(-1, frame_bytes).astype(np.int16)
    if len(frames) < 3:
        raise RuntimeError(f"only {len(frames)} frame(s), too few to compare")
    steps = np.abs(np.diff(frames, axis=0)).mean(axis=1)
    return float(np.abs(frames[-1] - frames[0]).mean()), float(np.percentile(steps, LOOP_STEP_PERCENTILE))


def _spans(found: list[tuple[float, float | None]]) -> str:
    shown = ", ".join(
        f"{clock(t)} ({f'{n:.1f} s' if n is not None else 'length unknown'})" for t, n in found[:3]
    )
    return shown + (f" and {len(found) - 3} more" if len(found) > 3 else "")


@dataclass
class _Report:
    path: str
    findings: list[Finding]

    def add(self, rule: str, severity: str, message: str, fix: str = "") -> None:
        self.findings.append(Finding("video", rule, severity, self.path, None, message, fix))

    def not_checked(self, what: str, why: object) -> None:
        self.add(
            "video-check-failed",
            "warning",
            f"{what} was not checked: {why}. Treat it as unknown, not as a pass.",
            "Open the file with ffprobe locally, or re-export it.",
        )


def check_video(abs_path: Path, rel: str, r: dict) -> list[Finding]:
    report = _Report(rel, [])
    try:
        info = probe(abs_path)
        _decode_all_frames(abs_path, info["index"])
    except RuntimeError as exc:
        report.add(
            "video-unreadable",
            "error",
            f"Video does not decode: {str(exc).rstrip('.')}.",
            "Re-export the file as MP4, MOV, MKV or WebM and check it plays to the end.",
        )
        return report.findings
    _measured(report, info)
    _declared(report, info, r)
    _requested_filters(report, abs_path, info, r)
    _audio_track(report, abs_path, info, r)
    return report.findings


def _measured(report: _Report, info: dict) -> None:
    real, avg = info["r_fps"], info["avg_fps"]
    if real is None or avg is None:
        report.not_checked("Variable frame rate", "ffprobe gave no usable r_frame_rate and avg_frame_rate")
    elif abs(real - avg) / max(real, avg) > FRAME_RATE_TOLERANCE:
        report.add(
            "variable-frame-rate",
            "warning",
            f"r_frame_rate is {real:.3f} fps and avg_frame_rate {avg:.3f} fps, so the frame timing varies. "
            "It can judder and drift against the audio. Measured.",
            "Conform to a constant frame rate (`ffmpeg -i in -fps_mode cfr -r 30 out`).",
        )

    if info["has_audio"]:
        video_s, audio_s = info["video_s"], info["audio_s"]
        if video_s is None or audio_s is None:
            report.not_checked("Stream length", "ffprobe gave no duration for the video or audio stream")
        elif abs(video_s - audio_s) > STREAM_LENGTH_TOLERANCE_S:
            report.add(
                "stream-length-mismatch",
                "warning",
                f"The video stream runs {video_s:.2f} s and the audio {audio_s:.2f} s, so one ends early. "
                "Measured.",
                "Trim or pad them to the same length, then recheck sync from the last edit to the end.",
            )

    if info["codec"] == "h264" and info["pix_fmt"] != "yuv420p":
        report.add(
            "pixel-format",
            "warning",
            f"H.264 in pixel format {info['pix_fmt']}. Browsers reliably play H.264 only as yuv420p.",
            "Re-encode with `-pix_fmt yuv420p`, or ignore this if nothing plays it in a browser.",
        )


def _declared(report: _Report, info: dict, r: dict) -> None:
    from . import media  # imported here because media imports this module to dispatch to it

    update = "Re-export to match, or update the manifest if the design changed."
    if "fps" in r:
        fps = info["avg_fps"]
        if fps is None:
            report.add(
                "fps", "error", f"The manifest declares {r['fps']} fps; ffprobe gave no usable frame rate."
            )
        elif abs(fps - r["fps"]) / r["fps"] > FRAME_RATE_TOLERANCE:
            report.add("fps", "error", f"Video is {fps:.2f} fps; the manifest declares {r['fps']}.", update)
    if "size" in r:
        got = (info["width"], info["height"])
        if got != media._wh(r["size"]):
            shown = f"{got[0]}×{got[1]}" if None not in got else "of unknown size"
            report.add("size", "error", f"Video is {shown}; the manifest declares {r['size']}.", update)
    if "max_duration_s" in r:
        length = info["duration"] if info["duration"] is not None else info["video_s"]
        limit = r["max_duration_s"]
        if length is None:
            report.add("duration", "error", f"The manifest caps this at {limit} s; ffprobe gave no duration.")
        elif length > limit:
            report.add("duration", "error", f"{length:.2f}s is longer than the declared maximum {limit}s.")
    if "codec" in r:
        want = r["codec"].strip().lower()
        if info["codec"] != CODEC_ALIASES.get(want, want):
            report.add(
                "codec",
                "error",
                f"Video codec is {info['codec']}; the manifest declares {r['codec']}.",
                update,
            )


def _requested_filters(report: _Report, abs_path: Path, info: dict, r: dict) -> None:
    index = info["index"]
    if "max_freeze_s" in r:
        limit = r["max_freeze_s"]
        try:
            found = freezes(abs_path, index, limit, info["video_s"])
        except RuntimeError as exc:
            report.not_checked("Frozen picture", exc)
        else:
            if found:
                report.add(
                    "freeze",
                    "warning",
                    f"The picture holds still longer than the manifest's {limit} s at {_spans(found)}. "
                    "Measured with freezedetect, which can't tell a stall from a deliberate hold.",
                    "Cut or shorten the hold, or raise max_freeze_s if it is intended.",
                )
    if "max_black_s" in r:
        limit = r["max_black_s"]
        try:
            found = black_stretches(abs_path, index, limit)
        except RuntimeError as exc:
            report.not_checked("Black frames", exc)
        else:
            if found:
                report.add(
                    "black-frames",
                    "warning",
                    f"The picture is black longer than the manifest's {limit} s at {_spans(found)}. "
                    "Measured with blackdetect, and dark footage reads as black too.",
                    "Trim the black, or raise max_black_s if it is intended.",
                )
    if r.get("loop"):
        try:
            seam, step = loop_seam(abs_path, index)
        except RuntimeError as exc:
            report.not_checked("The loop point", exc)
        else:
            if seam > LOOP_JUMP_RATIO * step + LOOP_JUMP_FLOOR:
                report.add(
                    "loop-jump",
                    "warning",
                    f"The last frame differs from the first by {seam:.1f}, and the normal frame-to-frame "
                    f"change here is {step:.1f} (0 to 255 scale), so the picture jumps on repeat. Measured.",
                    "Make the last frame flow into the first without duplicating it, then watch 3 repeats.",
                )


def _audio_track(report: _Report, abs_path: Path, info: dict, r: dict) -> None:
    """Run the existing audio checks on the track when the rule declares audio settings."""
    from . import media  # imported here because media imports this module to dispatch to it

    if not r.get("_declared", set()) & (media.AUDIO_KEYS - AUDIO_KEYS_SHARED_WITH_VIDEO):
        return
    if info["audio_index"] is None:
        report.add(
            "no-audio-track",
            "warning",
            "The manifest declares audio settings for this file, and it has no audio track to check.",
            "Add the track, or drop the audio keys from this rule.",
        )
        return
    with tempfile.TemporaryDirectory(prefix="slopgate-video-") as tmp:
        wav = Path(tmp) / "track.wav"
        extract = _run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                *INPUT_GUARD,
                "-i",
                str(abs_path),
                "-map",
                f"0:{info['audio_index']}",
                "-vn",
                "-c:a",
                "pcm_f32le",
                str(wav),
            ]
        )
        if extract.returncode != 0 or not wav.is_file() or wav.stat().st_size == 0:
            report.not_checked("The audio track", _first_line(extract.stderr, "ffmpeg extracted nothing"))
            return
        # check_audio says nothing about loudness it can't measure; a declared target must not pass that way.
        if "loudness_lufs" in r and media._ebur128(wav)[0] is None:
            report.not_checked("Audio loudness", "ffmpeg reported no integrated loudness for the track")
        # The video's own length is checked against max_duration_s in _declared. The repo path
        # goes in, not the temp .wav, so the lossless-only check skips a track that was lossy.
        settings = {k: v for k, v in r.items() if k != "max_duration_s"}
        for f in media.check_audio(wav, report.path, settings):
            report.findings.append(
                Finding("video", f.rule, "warning", f.path, None, f"Audio track: {f.message}", f.fix)
            )
