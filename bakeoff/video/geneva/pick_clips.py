"""Pick the GeneVA clips for the second video-review round and fetch them.

    python -I bakeoff/video/geneva/pick_clips.py LABELS.json CLIP_DIR

LABELS.json is https://geneva-annotations.s3.us-east-1.amazonaws.com/AnnotatedVideos.json;
its SHA-256 is checked, because every score depends on it. The first run fixes the
sample from SEED and writes selection.json. Later runs fetch the same clips and stop
on any hash mismatch. Clips and annotations are not committed. A candidate that
can't be fetched or read is listed in selection.json and the next one in the seeded
order takes its place, so a bad download is visible rather than a silent gap.

Each clip is remuxed without its metadata before any model sees it, so container
tags can't name the generator.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SELECTION = HERE / "selection.json"
BUCKET = "https://ai-generated-videos-icl.s3.us-east-1.amazonaws.com/"
LABELS_SHA256 = "3a69cb40ee35ae1e035946babded04c6ce2e4f2e967be65cd032c9cea0531160"
SEED = 20261109
PER_GENERATOR = {"annotated": 20, "unannotated": 10}
CLEAN_RATINGS = {"6", "7"}
GENERATORS = ("sora", "pika1", "vc2")
SEMANTIC = "Semantic Mismatch with Text"
CATEGORIES = ("Shape, Form, Geometry", "Visual Artifacts", "Motion", "Physics", "Other")
_CATEGORY = re.compile("|".join(re.escape(c) for c in (*CATEGORIES, SEMANTIC)))
# GeneVA's own files are MP4s; never let ffmpeg pick a demuxer from a downloaded file's bytes.
SAFE_INPUT = ["-protocol_whitelist", "file", "-f", "mov"]


def categories(label: str) -> set[str]:
    """The categories in a label such as 'Shape, Form, Geometry, Motion - deformed hand'."""
    head = label.split(" - ", 1)[0]
    if _CATEGORY.sub("", head).strip(" ,"):
        raise ValueError(f"unknown category in {head!r}")
    return set(_CATEGORY.findall(head))


def artifacts(annotation: list[dict], width: float, height: float) -> list[dict]:
    """One record per label: its categories, time range and boxes as fractions of the frame."""
    by_label: dict[str, list[dict]] = {}
    for frame in annotation:
        by_label.setdefault(frame["label"], []).append(frame)
    out = []
    for label, frames in by_label.items():
        kept = categories(label) - {SEMANTIC}
        if not kept:
            continue
        times = [float(f["time"]) for f in frames]
        boxes = [
            [
                float(f["time"]),
                float(f["x"]) / width,
                float(f["y"]) / height,
                float(f["w"]) / width,
                float(f["h"]) / height,
            ]
            for f in frames
        ]
        out.append({"categories": sorted(kept), "start_s": min(times), "end_s": max(times), "boxes": boxes})
    return out


def load_labels(path: Path) -> tuple[dict[str, dict], dict[str, int]]:
    """Every usable video by ID, and counts of the rows left out. A changed file is an error."""
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != LABELS_SHA256:
        raise SystemExit(f"{path}: SHA-256 is {digest}, expected {LABELS_SHA256}")
    rows = json.loads(raw)
    if not isinstance(rows, list) or not rows:
        raise SystemExit(f"{path}: expected a non-empty list of rows")
    counts = {"rows": len(rows), "no_annotation_field": 0, "unparsable_annotation": 0, "duplicate_rows": 0}
    counts["semantic_only_videos"] = 0
    seen: dict[str, int] = {}
    for row in rows:
        vid = str(row["videoID"]).strip('"')
        seen[vid] = seen.get(vid, 0) + 1
    videos: dict[str, dict] = {}
    for row in rows:
        vid = str(row["videoID"]).strip('"')
        if seen[vid] > 1:
            counts["duplicate_rows"] += 1
            continue
        if "annotation" not in row:
            counts["no_annotation_field"] += 1
            continue
        try:
            annotation = json.loads(row["annotation"])
            found = artifacts(annotation, float(row["videoWidth"]), float(row["videoHeight"]))
        except (TypeError, KeyError, ValueError, ZeroDivisionError):
            counts["unparsable_annotation"] += 1
            continue
        if annotation and not found:
            counts["semantic_only_videos"] += 1
            continue
        videos[vid] = {"generator": vid.split("/")[0], "artifacts": found, "rating": str(row.get("videoRating"))}
    return videos, counts


def candidates(videos: dict[str, dict]) -> dict[tuple[str, str], list[str]]:
    """Every eligible video per stratum, in the fixed seeded order."""
    rng = random.Random(SEED)
    out: dict[tuple[str, str], list[str]] = {}
    for gen in GENERATORS:
        ids = sorted(v for v, info in videos.items() if info["generator"] == gen)
        annotated = [v for v in ids if videos[v]["artifacts"]]
        clean = [v for v in ids if not videos[v]["artifacts"] and videos[v]["rating"] in CLEAN_RATINGS]
        rng.shuffle(annotated)
        rng.shuffle(clean)
        out[("annotated", gen)], out[("unannotated", gen)] = annotated, clean
    return out


def probe(path: Path) -> dict:
    """Stream facts for a downloaded clip. Raises when ffprobe can't read a video stream."""
    cmd = ["ffprobe", "-v", "error", *SAFE_INPUT, "-show_streams", "-show_format", "-of", "json", str(path)]
    info = json.loads(subprocess.run(cmd, check=True, capture_output=True, text=True).stdout)
    video = [s for s in info["streams"] if s["codec_type"] == "video"]
    seconds = float(info["format"]["duration"])
    if not video or seconds <= 0:
        raise ValueError(f"{path.name}: no video stream or no duration")
    return {
        "seconds": round(seconds, 3),
        "width": video[0]["width"],
        "height": video[0]["height"],
        "fps": video[0]["r_frame_rate"],
        "has_audio": any(s["codec_type"] == "audio" for s in info["streams"]),
    }


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fetch(video_id: str, source: Path, sent: Path) -> dict:
    """Download one clip and write its metadata-free copy. Raises on any failure."""
    with urllib.request.urlopen(BUCKET + video_id, timeout=120) as r:
        source.write_bytes(r.read())
    cmd = ["ffmpeg", "-v", "error", "-y", *SAFE_INPUT, "-i", str(source), "-map", "0"]
    cmd += ["-map_metadata", "-1", "-c", "copy", "-fflags", "+bitexact", str(sent)]
    subprocess.run(cmd, check=True, capture_output=True)
    return {**probe(sent), "sha256_source": sha256(source), "sha256_sent": sha256(sent)}


def build(videos: dict[str, dict], counts: dict[str, int], clips: Path) -> dict:
    picked, skipped = [], []
    with tempfile.TemporaryDirectory(dir=clips) as tmp:
        for (group, gen), ids in candidates(videos).items():
            need, n = PER_GENERATOR[group], 0
            for vid in ids:
                if n == need:
                    break
                sent = Path(tmp) / f"{len(picked):03d}.mp4"
                try:
                    facts = fetch(vid, Path(tmp) / "source.mp4", sent)
                except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
                    skipped.append({"video_id": vid, "reason": f"{type(exc).__name__}: {exc}"[:200]})
                    continue
                picked.append({"video_id": vid, "generator": gen, "group": group, "tmp": sent, **facts})
                n += 1
            if n != need:
                raise SystemExit(f"{group}/{gen}: only {n} of {need} clips could be fetched")
        random.Random(SEED + 1).shuffle(picked)
        for i, item in enumerate(picked, 1):
            item["clip"] = f"g{i:03d}"
            item.pop("tmp").rename(clips / f"{item['clip']}.mp4")
    return {
        "labels_sha256": LABELS_SHA256,
        "seed": SEED,
        "per_generator": PER_GENERATOR,
        "counts": counts,
        "eligible": {f"{g}/{m}": len(v) for (g, m), v in candidates(videos).items()},
        "skipped_candidates": skipped,
        "clips": picked,
    }


def refetch(selection: dict, clips: Path) -> None:
    """Fetch an existing selection and stop on the first clip that differs."""
    with tempfile.TemporaryDirectory(dir=clips) as tmp:
        for item in selection["clips"]:
            sent = clips / f"{item['clip']}.mp4"
            facts = fetch(item["video_id"], Path(tmp) / "source.mp4", sent)
            if facts["sha256_sent"] != item["sha256_sent"]:
                raise SystemExit(f"{item['clip']}: fetched file differs from selection.json")


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    labels, clips = Path(sys.argv[1]), Path(sys.argv[2])
    clips.mkdir(parents=True, exist_ok=True)
    videos, counts = load_labels(labels)
    if SELECTION.exists():
        refetch(json.loads(SELECTION.read_text()), clips)
        print("all clips match selection.json")
        return
    selection = build(videos, counts, clips)
    SELECTION.write_text(json.dumps(selection, indent=1) + "\n")
    print(json.dumps(counts), len(selection["clips"]), "clips;", len(selection["skipped_candidates"]), "skipped")


if __name__ == "__main__":
    main()
