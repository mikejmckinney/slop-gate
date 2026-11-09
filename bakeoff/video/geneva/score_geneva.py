"""Score the GeneVA replies against the human annotations, in code, with no human input.

    python -I bakeoff/video/geneva/score_geneva.py LABELS.json

LABELS.json is GeneVA's AnnotatedVideos.json; pick_clips.py checks its SHA-256. Writes
results/summary.json and scores.txt.

The headline is discrimination: the share of annotated clips an approach flags minus the
share of unannotated clips it flags. A model that flags everything scores 0 on it. The
unannotated share is a flag rate and not a false-alarm rate, because one annotator marked
each video and an empty annotation doesn't prove a clip clean.

On annotated clips a finding can also match an annotated artifact by category, by time
(ranges overlap after widening by TOLERANCE_S) and by box (IoU of at least MIN_IOU against
an annotated box inside that time window). The shuffled baseline scores the same findings
against another annotated clip from the same generator, so the chance level is visible.

A missing, failed, empty or malformed reply is counted as such and left out of the
denominators. It is never read as "no findings".
"""

from __future__ import annotations

import json
import random
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import score as base  # noqa: E402  (../score.py)
from pick_clips import CATEGORIES, GENERATORS, load_labels  # noqa: E402

RAW = HERE / "results" / "raw"
TOLERANCE_S = 0.5
MIN_IOU = 0.1
BOOTSTRAPS, SHUFFLES, SEED = 2000, 200, 20261110
APPROACHES = (
    "native:google/gemini-4-argon",
    "native:meta/muse-spark-open",
    "strip4:openai/gpt-6.1-terra",
    "strip4:openai/gpt-6.1-sol",
    "strip4:anthropic/claude-opus-5.5",
)
_NAMES = {base.norm(c) for c in CATEGORIES}
MATCHES = ("category", "time", "box", "full")


def read_reply(approach: str, clip: str, raw: Path = RAW) -> tuple[str, list[dict] | None]:
    """('ok', findings), or ('missing' | 'failed', None) when there is no usable reply."""
    path = raw / approach.replace(":", "__").replace("/", "__") / f"{clip}.run1.json"
    if not path.exists():
        return "missing", None
    try:
        rec = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return "failed", None
    if not isinstance(rec, dict) or rec.get("error"):
        return "failed", None
    findings = base.parse(rec.get("text"))
    return ("failed", None) if findings is None else ("ok", findings)


def usable(finding: dict) -> dict | None:
    """The finding's category, times and box, or None if any of them is malformed."""
    times = base.span(finding)
    box = finding.get("box")
    if times is None or base.norm(finding.get("category")) not in _NAMES:
        return None
    if not isinstance(box, list) or len(box) != 4:
        return None
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in box):
        return None
    if box[2] <= 0 or box[3] <= 0:
        return None
    return {"category": base.norm(finding["category"]), "start": times[0], "end": times[1], "box": box}


def iou(a: list[float], b: list[float]) -> float:
    width = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    height = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    if width <= 0 or height <= 0:
        return 0.0
    overlap = width * height
    return overlap / (a[2] * a[3] + b[2] * b[3] - overlap)


def match(finding: dict, artifact: dict) -> dict[str, bool]:
    """Which of category, time, box and all three a usable finding has against one artifact."""
    category = finding["category"] in {base.norm(c) for c in artifact["categories"]}
    early, late = finding["start"] - TOLERANCE_S, finding["end"] + TOLERANCE_S
    time = early <= artifact["end_s"] and late >= artifact["start_s"]
    box = any(
        early <= frame[0] <= late and iou(finding["box"], frame[1:]) >= MIN_IOU for frame in artifact["boxes"]
    )
    return {"category": category, "time": time, "box": box, "full": category and time and box}


def score_clip(findings: list[dict], artifacts: list[dict]) -> dict[str, bool]:
    """Whether any usable finding matches any artifact, per kind of match."""
    out = dict.fromkeys(MATCHES, False)
    for finding in filter(None, map(usable, findings)):
        for artifact in artifacts:
            for key, hit in match(finding, artifact).items():
                out[key] = out[key] or hit
    return out


def rate(hits: int, total: int) -> dict:
    if not total:
        return {"k": 0, "n": 0, "rate": None, "low": None, "high": None}
    low, high = base.wilson(hits, total)
    return {
        "k": hits,
        "n": total,
        "rate": round(hits / total, 3),
        "low": round(low, 3),
        "high": round(high, 3),
    }


def share(flags: list[bool]) -> float:
    return sum(flags) / len(flags)


def discrimination(annotated: list[bool], unannotated: list[bool], rng: random.Random) -> dict:
    """Annotated flag rate minus unannotated flag rate, with a clip-bootstrap 95% interval."""
    if not annotated or not unannotated:
        return {"value": None, "low": None, "high": None}
    draws = sorted(
        share(rng.choices(annotated, k=len(annotated))) - share(rng.choices(unannotated, k=len(unannotated)))
        for _ in range(BOOTSTRAPS)
    )
    return {
        "value": round(share(annotated) - share(unannotated), 3),
        "low": round(draws[int(0.025 * BOOTSTRAPS)], 3),
        "high": round(draws[int(0.975 * BOOTSTRAPS) - 1], 3),
    }


def shuffled(
    rows: list[dict], artifacts: dict[str, list[dict]], rng: random.Random
) -> dict[str, float | None]:
    """Mean match rates when each clip's findings meet another same-generator clip's annotations."""
    if not rows:
        return dict.fromkeys(MATCHES)
    totals = dict.fromkeys(MATCHES, 0)
    for _ in range(SHUFFLES):
        for gen in GENERATORS:
            ring = [r for r in rows if r["generator"] == gen]
            rng.shuffle(ring)
            for i, row in enumerate(ring):
                if len(ring) < 2:
                    continue
                other = ring[(i + 1) % len(ring)]["clip"]
                for key, hit in score_clip(row["findings"], artifacts[other]).items():
                    totals[key] += hit
    return {key: round(totals[key] / (SHUFFLES * len(rows)), 3) for key in MATCHES}


def group_rates(rows: list[dict], artifacts: dict[str, list[dict]]) -> dict:
    """Flag rates and match rates for one set of clips."""
    annotated = [r for r in rows if r["group"] == "annotated"]
    unannotated = [r for r in rows if r["group"] == "unannotated"]
    matches = [score_clip(r["findings"], artifacts[r["clip"]]) for r in annotated]
    out = {
        "flagged_annotated": rate(sum(bool(r["findings"]) for r in annotated), len(annotated)),
        "flagged_unannotated": rate(sum(bool(r["findings"]) for r in unannotated), len(unannotated)),
    }
    for key in MATCHES:
        out[key] = rate(sum(m[key] for m in matches), len(matches))
    return out


def score_approach(
    approach: str, clips: list[dict], artifacts: dict[str, list[dict]], raw: Path = RAW
) -> dict:
    rng = random.Random(f"{SEED}/{approach}")
    rows, states = [], {"ok": 0, "failed": 0, "missing": 0}
    for clip in clips:
        state, findings = read_reply(approach, clip["clip"], raw)
        states[state] += 1
        if findings is not None:
            rows.append({**clip, "findings": findings})
    flags = {g: [bool(r["findings"]) for r in rows if r["group"] == g] for g in ("annotated", "unannotated")}
    claimed = [f for r in rows for f in r["findings"]]
    out = {
        "approach": approach,
        "replies": states,
        "discrimination": discrimination(flags["annotated"], flags["unannotated"], rng),
        **group_rates(rows, artifacts),
        "shuffled": shuffled([r for r in rows if r["group"] == "annotated"], artifacts, rng),
        "findings": len(claimed),
        "malformed_findings": sum(usable(f) is None for f in claimed),
        "by_generator": {
            g: group_rates([r for r in rows if r["generator"] == g], artifacts) for g in GENERATORS
        },
    }
    out.update(costs(approach, clips, raw))
    return out


def costs(approach: str, clips: list[dict], raw: Path) -> dict:
    folder = raw / approach.replace(":", "__").replace("/", "__")
    recs = []
    for clip in clips:
        path = folder / f"{clip['clip']}.run1.json"
        try:
            recs.append(json.loads(path.read_text()))
        except (OSError, json.JSONDecodeError):
            continue
    total = sum(r["cost"] for r in recs if isinstance(r.get("cost"), (int, float)))
    minutes = sum(c["seconds"] for c in clips) / 60
    seconds = [r["seconds"] for r in recs if isinstance(r.get("seconds"), (int, float))]
    return {
        "usd": round(total, 4),
        "usd_per_clip": round(total / len(clips), 4),
        "usd_per_minute": round(total / minutes, 4),
        "median_seconds": statistics.median(seconds) if seconds else None,
    }


def checked_clips(selection: dict, videos: dict[str, dict]) -> tuple[list[dict], dict[str, list[dict]]]:
    """The selected clips and their annotations. A clip that no longer fits its group is an error."""
    clips, artifacts = [], {}
    for item in selection["clips"]:
        info = videos.get(item["video_id"])
        if info is None or bool(info["artifacts"]) != (item["group"] == "annotated"):
            raise SystemExit(f"{item['clip']}: labels don't fit its group {item['group']!r}")
        artifacts[item["clip"]] = info["artifacts"]
        clips.append({k: item[k] for k in ("clip", "group", "generator", "seconds")})
    return clips, artifacts


def cell(r: dict) -> str:
    return "n/a" if r["rate"] is None else f"{r['k']}/{r['n']}"


def table(results: list[dict]) -> str:
    lines = [
        f"tolerance {TOLERANCE_S} s, minimum box IoU {MIN_IOU}, one run per clip",
        "",
        "approach | discrimination (95%) | annotated flagged | unannotated flagged | ok/failed/missing",
    ]
    for r in results:
        d, s = r["discrimination"], r["replies"]
        lines.append(
            f"{r['approach']} | {d['value']} ({d['low']} to {d['high']}) | {cell(r['flagged_annotated'])} | "
            f"{cell(r['flagged_unannotated'])} | {s['ok']}/{s['failed']}/{s['missing']}"
        )
    lines += ["", "matches on annotated clips, real / shuffled baseline"]
    lines.append(
        "approach | category | time | box | category+time+box | findings | malformed | $/clip | $/min"
    )
    for r in results:
        parts = [f"{cell(r[k])} / {r['shuffled'][k]}" for k in MATCHES]
        lines.append(
            f"{r['approach']} | " + " | ".join(parts) + f" | {r['findings']} | {r['malformed_findings']} | "
            f"{r['usd_per_clip']} | {r['usd_per_minute']}"
        )
    lines += ["", "by generator: annotated flagged, unannotated flagged, category+time+box"]
    for r in results:
        parts = []
        for g in GENERATORS:
            b = r["by_generator"][g]
            parts.append(
                f"{g} {cell(b['flagged_annotated'])}, {cell(b['flagged_unannotated'])}, {cell(b['full'])}"
            )
        lines.append(f"{r['approach']} | " + " | ".join(parts))
    return "\n".join(lines) + "\n"


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    videos, _ = load_labels(Path(sys.argv[1]))
    clips, artifacts = checked_clips(json.loads((HERE / "selection.json").read_text()), videos)
    results = [score_approach(a, clips, artifacts) for a in APPROACHES]
    (HERE / "results" / "summary.json").write_text(json.dumps(results, indent=1) + "\n")
    report = table(results)
    (HERE / "scores.txt").write_text(report)
    print(report)


if __name__ == "__main__":
    main()
