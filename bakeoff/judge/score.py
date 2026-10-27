"""Score cached bakeoff results using the adjudicated ground truth.

Ground truth lives in adjudication.json: every blocking (image, category) pair
a model reported, marked true, false, or disputed after checking the image,
plus the list of confirmed defects (planted on purpose, or found by the models
and confirmed).

Metrics per model, both runs pooled:
- planted_recall: share of (planted defect, run) where the model raised a
  blocking finding in one of that defect's categories.
- natural_recall: the same for defects nobody planted (found and confirmed).
- precision: true / (true + false) over blocking findings; disputed excluded.
- false_alarm_rate: share of runs on defect-free images that produced a
  blocking finding adjudicated false.
- consistency: share of images where both runs gave the same verdict.
- invalid_rate: replies that were not usable JSON, or failed calls.
"""

import json
from collections import defaultdict
from pathlib import Path
from statistics import mean, median

HERE = Path(__file__).resolve().parent


def load():
    adj = json.loads((HERE / "adjudication.json").read_text())
    pairs = adj["pairs"]
    defects = {k: v for k, v in adj["defects"].items() if not k.startswith("_")}
    raw = defaultdict(lambda: defaultdict(dict))
    for f in (HERE / "results" / "raw").glob("*/*.json"):
        r = json.loads(f.read_text())
        raw[r["model"]][r["image"]][r["run"]] = r
    items = [i["id"] for i in json.loads((HERE / "labels.json").read_text())]
    return pairs, defects, raw, items


def score():
    pairs, defects, raw, items = load()
    defect_images = {k.split("/")[0] for k in defects}
    clean_images = [i for i in items if i not in defect_images]
    summary, unadjudicated = {}, set()
    for model, by_image in sorted(raw.items()):
        n = defaultdict(int)
        costs, secs = [], []
        for image_id in items:
            runs = by_image.get(image_id, {})
            verdicts = []
            for run in (1, 2):
                r = runs.get(run)
                if r is None:
                    continue
                n["calls"] += 1
                if r["verdict"] in ("error", "invalid"):
                    n["invalid"] += 1
                    usable = False
                else:
                    usable = True
                    verdicts.append(r["verdict"])
                    if r.get("cost") is not None:
                        costs.append(r["cost"])
                    if r.get("seconds") is not None:
                        secs.append(r["seconds"])
                cats = (
                    {f["category"] for f in r.get("findings", []) if f["severity"] == "blocking"}
                    if usable
                    else set()
                )
                for cat in cats:
                    status = pairs.get(f"{image_id}|{cat}")
                    if status is None:
                        unadjudicated.add(f"{image_id}|{cat}")
                    elif status in ("true", "false"):
                        n[status] += 1
                for key, d in defects.items():
                    if key.split("/")[0] != image_id:
                        continue
                    bucket = "planted" if d["planted"] else "natural"
                    n[f"{bucket}_total"] += 1
                    n[f"{bucket}_found"] += bool(cats & set(d["categories"]))
                if image_id in clean_images:
                    n["clean_runs"] += 1
                    n["false_alarms"] += any(pairs.get(f"{image_id}|{c}") == "false" for c in cats)
            if len(verdicts) == 2:
                n["pairs"] += 1
                n["consistent"] += verdicts[0] == verdicts[1]

        def rate(a, b, n=n):
            return round(n[a] / n[b], 3) if n[b] else None

        summary[model] = {
            "planted_recall": rate("planted_found", "planted_total"),
            "natural_recall": rate("natural_found", "natural_total"),
            "precision": round(n["true"] / (n["true"] + n["false"]), 3) if n["true"] + n["false"] else None,
            "false_alarm_rate": rate("false_alarms", "clean_runs"),
            "consistency": rate("consistent", "pairs"),
            "invalid_rate": rate("invalid", "calls"),
            "cost_per_image_usd": round(mean(costs), 5) if costs else None,
            "median_seconds": round(median(secs), 1) if secs else None,
            "calls": n["calls"],
        }
    return summary, sorted(unadjudicated)


def main() -> None:
    summary, unadjudicated = score()
    (HERE / "results" / "summary.json").write_text(json.dumps(summary, indent=1))
    cols = [
        "planted_recall",
        "natural_recall",
        "precision",
        "false_alarm_rate",
        "consistency",
        "invalid_rate",
        "cost_per_image_usd",
        "median_seconds",
    ]
    print(f"{'model':30s} " + " ".join(f"{c[:11]:>11s}" for c in cols))
    for m, v in sorted(
        summary.items(), key=lambda kv: -((kv[1]["planted_recall"] or 0) + (kv[1]["natural_recall"] or 0))
    ):
        print(f"{m:30s} " + " ".join(f"{str(v[c]):>11s}" for c in cols))
    if unadjudicated:
        print("\nNot yet adjudicated:", ", ".join(unadjudicated))


if __name__ == "__main__":
    main()
