"""Score raw replies against the planted labels, in code, with no human input.

    python bakeoff/video/score.py

A finding hits a planted defect when its pattern equals the label's pattern
(ignoring case and punctuation) and its time range overlaps the label's range
widened by TOLERANCE_S on each side. The hit rule and the labels were fixed
before any full-run reply was read. A hit that the same rule also makes on the
clip's clean sibling in the same run shows nothing about the plant, so the
ranked recall counts a hit only when the sibling doesn't also make it. That
check was added after the first results showed such hits; the raw numbers stay
in the output. Everything else a model claimed goes to
results/unadjudicated.json for a person to read and is not part of any metric,
except that every finding on a clean clip is counted as a claim on a clean clip:
those clips have no planted defect, so nothing needs judging to count them.

Writes results/summary.json, results/unadjudicated.json and scores.txt.
"""

from __future__ import annotations

import json
import math
import re
import statistics
from itertools import combinations
from pathlib import Path

HERE = Path(__file__).resolve().parent
RAW = HERE / "results" / "raw"
TOLERANCE_S = 0.5
RUNS = (1, 2)
AUDIO_INPUT = {
    "native:google/gemini-4-argon",
    "native:google/gemini-3.8-flash",
    "native:qwen/qwen3.8-omni-flash",
    "native:xiaomi/mimo-v2.6-pro",
    "native:meta/muse-spark-open",
    "native:perceptron/perceptron-mk1.5",
}


def norm(s: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


def parse(text: str | None) -> list[dict] | None:
    """The findings in a reply; None when the reply holds no valid JSON findings list."""
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    findings = data.get("findings") if isinstance(data, dict) else None
    if not isinstance(findings, list):
        return None
    return [f for f in findings if isinstance(f, dict)]


def span(f: dict) -> tuple[float, float] | None:
    try:
        a, b = float(f["start_s"]), float(f["end_s"])
    except (KeyError, TypeError, ValueError):
        return None
    return (min(a, b), max(a, b))


def overlaps(f: dict, label: dict) -> bool:
    s = span(f)
    if s is None:
        return False
    return s[0] <= label["end_s"] + TOLERANCE_S and s[1] >= label["start_s"] - TOLERANCE_S


def named(f: dict, label: dict) -> bool:
    return norm(f.get("pattern")) == norm(label["pattern"])


def is_hit(f: dict, label: dict) -> bool:
    return named(f, label) and overlaps(f, label)


def iou(s: tuple[float, float], lab: dict) -> float:
    lo, hi = lab["start_s"], lab["end_s"]
    inter = max(0.0, min(s[1], hi) - max(s[0], lo))
    union = max(s[1], hi) - min(s[0], lo)
    return inter / union if union > 0 else 0.0


def wilson(k: int, n: int) -> tuple[float, float]:
    if not n:
        return (0.0, 0.0)
    z = 1.96
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (c - m) / d), min(1.0, (c + m) / d))


def load() -> tuple[list[dict], dict[str, dict[tuple[str, int], dict]]]:
    labels = json.loads((HERE / "labels.json").read_text())
    replies: dict[str, dict[tuple[str, int], dict]] = {}
    for f in sorted(RAW.glob("*/*.run*.json")):
        rec = json.loads(f.read_text())
        rec["findings"] = parse(rec.get("text"))
        replies.setdefault(rec["approach"], {})[(rec["clip"], rec["run"])] = rec
    return labels, replies


def hit_runs(rec: dict | None, label: dict) -> tuple[bool, list[dict]]:
    findings = (rec or {}).get("findings") or []
    hits = [f for f in findings if is_hit(f, label)]
    return bool(hits), hits


def score_one(approach: str, labels: list[dict], recs: dict) -> dict:
    defects = [lab for lab in labels if lab["kind"] == "defect"]
    clean = [lab for lab in labels if lab["kind"] != "defect"]
    out: dict = {"approach": approach, "calls": len(recs)}

    per_defect: dict[str, list[int]] = {}
    per_defect_raw: dict[str, list[int]] = {}
    sibling_hits = 0
    ious, onsets = [], []
    named_n = located_n = 0
    for lab in defects:
        got, raw = [], []
        for run in RUNS:
            rec = recs.get((lab["clip"], run))
            hit, hits = hit_runs(rec, lab)
            sibling = hit_runs(recs.get((lab["group"] + "_clean", run)), lab)[0]
            sibling_hits += sibling
            raw.append(int(hit))
            got.append(int(hit and not sibling))
            fs = (rec or {}).get("findings") or []
            named_n += any(named(f, lab) for f in fs)
            located_n += any(overlaps(f, lab) for f in fs)
            for f in hits[:1]:
                s = span(f)
                ious.append(iou(s, lab))
                onsets.append(abs(s[0] - lab["start_s"]))
        per_defect[lab["group"]] = got
        per_defect_raw[lab["group"]] = raw

    def tally(sel: list[dict], prefix: str) -> None:
        hits = sum(sum(per_defect[d["group"]]) for d in sel)
        runs = 2 * len(sel)
        lo, hi = wilson(hits, runs)
        out[prefix + "defects_total"] = len(sel)
        out[prefix + "defect_runs_total"] = runs
        out[prefix + "defect_run_hits"] = hits
        out[prefix + "defects_found_any_run"] = sum(1 for d in sel if any(per_defect[d["group"]]))
        out[prefix + "defects_found_both_runs"] = sum(1 for d in sel if all(per_defect[d["group"]]))
        out[prefix + "recall_defect_runs"] = round(hits / runs, 3) if runs else None
        out[prefix + "recall_ci95"] = [round(lo, 2), round(hi, 2)]

    tally(defects, "")
    tally([d for d in defects if not d.get("needs_audio")], "picture_")
    tally(
        [d for d in defects if "brief" not in d.get("tags", []) and not d.get("needs_audio")],
        "picture_nobrief_",
    )
    tally([d for d in defects if "brief" in d.get("tags", [])], "brief_")
    out["per_defect"] = per_defect
    out["per_defect_raw"] = per_defect_raw
    out["raw_defect_run_hits"] = sum(sum(v) for v in per_defect_raw.values())
    out["sibling_hits"] = sibling_hits
    out["named_defect_runs"] = named_n
    out["located_defect_runs"] = located_n
    out["median_iou"] = round(statistics.median(ious), 2) if ious else None
    out["median_onset_error_s"] = round(statistics.median(onsets), 2) if onsets else None

    clean_runs = claims = med_high = flagged = 0
    by_clean: dict[str, list[int]] = {}
    for lab in clean:
        row = []
        for run in RUNS:
            rec = recs.get((lab["clip"], run))
            fs = (rec or {}).get("findings")
            if fs is None:
                row.append(None)
                continue
            clean_runs += 1
            claims += len(fs)
            med_high += sum(1 for f in fs if f.get("confidence") in ("high", "medium"))
            flagged += bool(fs)
            row.append(int(bool(fs)))
        by_clean[lab["clip"]] = row
    out["clean_runs_scored"] = clean_runs
    out["clean_claims"] = claims
    out["clean_claims_med_high"] = med_high
    out["clean_runs_flagged"] = flagged
    out["clean_flagged_rate"] = round(flagged / clean_runs, 3) if clean_runs else None
    out["by_clean_clip"] = by_clean

    agree = same_outcome = 0
    for lab in labels:
        a = recs.get((lab["clip"], 1))
        b = recs.get((lab["clip"], 2))
        if not a or not b or a["findings"] is None or b["findings"] is None:
            continue
        same_outcome += 1
        if lab["kind"] == "defect":
            sib = {run: hit_runs(recs.get((lab["group"] + "_clean", run)), lab)[0] for run in RUNS}
            agree += (hit_runs(a, lab)[0] and not sib[1]) == (hit_runs(b, lab)[0] and not sib[2])
        else:
            agree += bool(a["findings"]) == bool(b["findings"])
    out["agreement_clips"] = f"{agree}/{same_outcome}"
    out["agreement"] = round(agree / same_outcome, 3) if same_outcome else None

    empty = sum(1 for r in recs.values() if not r.get("text") and not r.get("maybe_billed"))
    errors = sum(1 for r in recs.values() if r.get("error") and r.get("text") is None)
    unparsed = sum(1 for r in recs.values() if r.get("text") and r["findings"] is None)
    out["failed_empty_replies"] = empty
    out["failed_http_or_timeout"] = errors
    out["unparsed_replies"] = unparsed
    secs = [r["seconds"] for r in recs.values() if r.get("seconds")]
    out["median_seconds"] = round(statistics.median(secs), 1) if secs else None
    costs = [r["cost"] for r in recs.values() if isinstance(r.get("cost"), (int, float))]
    out["total_cost"] = round(sum(costs), 4)
    seconds_of_video = sum(next(lab["seconds"] for lab in labels if lab["clip"] == c) for (c, _r) in recs)
    out["cost_per_clip"] = round(sum(costs) / max(len(costs), 1), 4)
    out["cost_per_video_minute"] = round(sum(costs) / max(seconds_of_video / 60, 1e-9), 4)

    has_audio = approach in AUDIO_INPUT
    wrong_basis = [0, 0]
    for (clip, _run), r in recs.items():
        lab = next(item for item in labels if item["clip"] == clip)
        for f in r.get("findings") or []:
            basis = norm(f.get("basis", ""))
            if basis == "listening" and (not has_audio or not lab.get("has_audio")):
                wrong_basis[0] += 1
            if "human" in basis or "playback" in basis or "watched" in basis:
                wrong_basis[1] += 1
    out["claims_listening_with_no_audio"] = wrong_basis[0]
    out["claims_human_playback"] = wrong_basis[1]
    return out


def ensembles(labels: list[dict], replies: dict, approaches: list[str]) -> list[dict]:
    defects = [lab for lab in labels if lab["kind"] == "defect"]
    clean = [lab for lab in labels if lab["kind"] != "defect"]
    rows = []
    for a, b in combinations(approaches, 2):
        ra, rb = replies[a], replies[b]
        hits = hits_all = 0
        for lab in defects:
            for run in RUNS:
                on_defect = (
                    hit_runs(ra.get((lab["clip"], run)), lab)[0]
                    or hit_runs(rb.get((lab["clip"], run)), lab)[0]
                )
                sib = lab["group"] + "_clean"
                on_sibling = hit_runs(ra.get((sib, run)), lab)[0] or hit_runs(rb.get((sib, run)), lab)[0]
                net = on_defect and not on_sibling
                hits_all += net
                hits += net and not lab.get("needs_audio")
        flags = 0
        for lab in clean:
            for run in RUNS:
                fa = (ra.get((lab["clip"], run)) or {}).get("findings") or []
                fb = (rb.get((lab["clip"], run)) or {}).get("findings") or []
                flags += bool(fa or fb)
        cost = 0.0
        for recs in (ra, rb):
            vals = [r["cost"] for r in recs.values() if isinstance(r.get("cost"), (int, float))]
            cost += sum(vals) / max(len(vals), 1)
        rows.append(
            {
                "a": a,
                "b": b,
                "picture_defect_run_hits": hits,
                "picture_defect_runs": 2 * sum(1 for lab in defects if not lab.get("needs_audio")),
                "all_defect_run_hits": hits_all,
                "all_defect_runs": 2 * len(defects),
                "clean_runs_flagged": flags,
                "clean_runs": 2 * len(clean),
                "cost_per_clip": round(cost, 4),
            }
        )
    rows.sort(key=lambda r: (-r["picture_defect_run_hits"], r["clean_runs_flagged"], r["cost_per_clip"]))
    return rows


def unadjudicated(labels: list[dict], replies: dict) -> list[dict]:
    """Every claim not matched to a planted label. No metric reads this file."""
    label_of = {lab["clip"]: lab for lab in labels}
    rows = []
    for approach, recs in sorted(replies.items()):
        for (clip, run), r in sorted(recs.items()):
            lab = label_of[clip]
            for f in r.get("findings") or []:
                matched = lab["kind"] == "defect" and is_hit(f, lab)
                if matched:
                    continue
                rows.append(
                    {
                        "approach": approach,
                        "clip": clip,
                        "run": run,
                        "clip_kind": lab["kind"],
                        "pattern": f.get("pattern"),
                        "start_s": f.get("start_s"),
                        "end_s": f.get("end_s"),
                        "confidence": f.get("confidence"),
                        "basis": f.get("basis"),
                        "evidence": f.get("evidence"),
                        "fix": f.get("fix"),
                    }
                )
    return rows


def main() -> None:
    labels, replies = load()
    approaches = sorted(replies)
    summary = {
        "clips": len(labels),
        "defects": sum(1 for lab in labels if lab["kind"] == "defect"),
        "clean_clips": sum(1 for lab in labels if lab["kind"] != "defect"),
        "tolerance_s": TOLERANCE_S,
        "approaches": [score_one(a, labels, replies[a]) for a in approaches],
    }
    pairs = ensembles(labels, replies, approaches)
    summary["ensembles"] = pairs
    (HERE / "results" / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    un = unadjudicated(labels, replies)
    (HERE / "results" / "unadjudicated.json").write_text(json.dumps(un, indent=1) + "\n")

    head = (
        f"{'approach':43} {'net':>6} {'pic':>6} {'raw':>6} {'sib':>4} {'any/13':>6} {'both':>5} {'brief':>5} "
        f"{'named':>5} {'locatd':>6} {'iou':>5} {'onset':>5} {'clean':>7} {'agree':>6} {'fail':>4} "
        f"{'sec':>6} {'$clip':>7} {'$/min':>6}"
    )
    lines = [
        f"{summary['clips']} clips: {summary['defects']} defects, {summary['clean_clips']} clean, "
        "2 runs each. net = defect-runs where the planted-pattern hit did not also fire on the clean "
        "sibling (of 26); pic = net picture-only (of 22); raw = before the sibling check (of 26); "
        "sib = same-rule hits on clean siblings; any/both = defects found net in either/both runs (of 13).",
        head,
    ]
    order = sorted(summary["approaches"], key=lambda r: -r["defect_run_hits"])
    for r in order:
        fails = r["failed_empty_replies"] + r["failed_http_or_timeout"] + r["unparsed_replies"]
        lines.append(
            f"{r['approach']:43} {r['defect_run_hits']:>3}/26 {r['picture_defect_run_hits']:>3}/22 "
            f"{r['raw_defect_run_hits']:>3}/26 {r['sibling_hits']:>4} "
            f"{r['defects_found_any_run']:>6} {r['defects_found_both_runs']:>5} "
            f"{r['brief_defect_run_hits']:>3}/4 "
            f"{r['named_defect_runs']:>5} {r['located_defect_runs']:>6} {str(r['median_iou']):>5} "
            f"{str(r['median_onset_error_s']):>5} {r['clean_runs_flagged']:>3}/{r['clean_runs_scored']:<3} "
            f"{r['agreement']:>6} {fails:>4} {str(r['median_seconds']):>6} {r['cost_per_clip']:>7} "
            f"{r['cost_per_video_minute']:>6}"
        )
    lines += ["", "Per defect, net hits in 2 runs (raw in summary.json):"]
    names = [lab["group"] for lab in labels if lab["kind"] == "defect"]
    lines.append(f"{'approach':43} " + " ".join(f"{n[:10]:>10}" for n in names))
    for r in order:
        lines.append(f"{r['approach']:43} " + " ".join(f"{sum(r['per_defect'][n]):>10}" for n in names))
    lines += ["", "Top pairs on net picture-only defect-runs (union of findings, same run):"]
    for p in pairs[:8]:
        lines.append(
            f"{p['a']} + {p['b']}: {p['picture_defect_run_hits']}/{p['picture_defect_runs']} picture, "
            f"{p['all_defect_run_hits']}/{p['all_defect_runs']} all, "
            f"clean flagged {p['clean_runs_flagged']}/{p['clean_runs']} ${p['cost_per_clip']}/clip"
        )
    total = sum(r["total_cost"] for r in summary["approaches"])
    probes = sum(
        json.loads(f.read_text()).get("cost") or 0 for f in (HERE / "results" / "probe").glob("*/*.run*.json")
    )
    realism = sum(
        json.loads(f.read_text()).get("cost") or 0
        for f in (HERE / "results" / "realism").glob("*/*.run*.json")
    )
    superseded = sum(
        json.loads(f.read_text()).get("cost") or 0
        for f in (HERE / "results" / "superseded").glob("*/*.run*.json")
    )
    gen = json.loads((HERE / "results" / "realism_generation.json").read_text())
    gen_cost = sum(v or 0 for v in gen["cost"].values())
    lines += [
        "",
        f"Spend from usage.cost: scored ${total:.2f}, superseded ${superseded:.2f}, probe ${probes:.2f}, "
        f"realism review ${realism:.2f}, realism generation ${gen_cost:.2f}, "
        f"total ${total + superseded + probes + realism + gen_cost:.2f}. "
        f"Unadjudicated claims: {len(un)}.",
    ]
    (HERE / "scores.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
