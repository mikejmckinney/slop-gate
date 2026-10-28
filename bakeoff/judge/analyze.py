"""Per-defect catch table and two-model ensembles, written to results/analysis.json.

A defect counts as caught by a model in a run when that run raised a blocking
finding in one of the defect's categories. An ensemble catches a defect in run
N when either member's run N does, and costs the sum of both.
"""

import json
from itertools import combinations
from pathlib import Path

from score import load

HERE = Path(__file__).resolve().parent


def caught(raw, model, image, run, cats):
    r = raw[model].get(image, {}).get(run)
    if not r or r["verdict"] in ("error", "invalid"):
        return False
    return any(f["severity"] == "blocking" and f["category"] in cats for f in r["findings"])


def false_claims(raw, model, image, run, pairs):
    r = raw[model].get(image, {}).get(run)
    if not r or r["verdict"] in ("error", "invalid"):
        return 0
    cats = {f["category"] for f in r["findings"] if f["severity"] == "blocking"}
    return sum(pairs.get(f"{image}|{c}") == "false" for c in cats)


def main() -> None:
    pairs, defects, raw, _ = load()
    summary = json.loads((HERE / "results" / "summary.json").read_text())
    models = sorted(raw)
    per_defect = {
        key: {
            m: sum(caught(raw, m, key.split("/")[0], run, d["categories"]) for run in (1, 2)) for m in models
        }
        for key, d in defects.items()
    }
    ensembles = []
    for a, b in combinations(models, 2):
        hits = total = falses = 0
        for key, d in defects.items():
            image = key.split("/")[0]
            for run in (1, 2):
                total += 1
                hits += caught(raw, a, image, run, d["categories"]) or caught(
                    raw, b, image, run, d["categories"]
                )
        for image in {k.split("|")[0] for k in pairs}:
            for run in (1, 2):
                falses += false_claims(raw, a, image, run, pairs) + false_claims(raw, b, image, run, pairs)
        cost = (summary[a]["cost_per_image_usd"] or 0) + (summary[b]["cost_per_image_usd"] or 0)
        ensembles.append(
            {
                "models": [a, b],
                "recall": round(hits / total, 3),
                "false_claims": falses,
                "cost_per_image_usd": round(cost, 5),
            }
        )
    ensembles.sort(key=lambda e: (-e["recall"], e["cost_per_image_usd"]))
    out = {"per_defect": per_defect, "ensembles_top10": ensembles[:10]}
    (HERE / "results" / "analysis.json").write_text(json.dumps(out, indent=1))
    short = {m: m.split("/")[1][:14] for m in models}
    print(f"{'defect':40s} " + " ".join(f"{short[m]:>14s}" for m in models))
    for key, row in per_defect.items():
        print(f"{key:40s} " + " ".join(f"{row[m]:>14d}" for m in models))
    print()
    for e in ensembles[:10]:
        print(e)


if __name__ == "__main__":
    main()
