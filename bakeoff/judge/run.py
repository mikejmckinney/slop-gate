"""Run every candidate judge over the labeled set, twice, and cache raw results.

Usage: OPENROUTER_API_KEY=... python bakeoff/judge/run.py [model ...]
Results land in bakeoff/judge/results/raw/<model>/<image>.run<N>.json and are
reused on re-run, so an interrupted bakeoff resumes where it stopped.
"""

import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

from slopgate.judge import review  # noqa: E402

MODELS = [
    "google/gemini-4-argon",
    "openai/gpt-6.1-sol",
    "openai/gpt-6.1-terra",
    "anthropic/claude-opus-5.5",
    "anthropic/claude-sonnet-5.5",
    "x-ai/grok-4.7",
    "anthropic/claude-haiku-5.5",
    "openai/gpt-6-luna",
    "qwen/qwen3.8-omni-flash",
    "nvidia/nemotron-3.5-super-vl",
]
RUNS = 2


def job(model: str, item: dict, run: int) -> str:
    out = HERE / "results" / "raw" / model.replace("/", "__") / f"{item['id']}.run{run}.json"
    if out.exists():
        return "cached"
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = review(model, str(HERE / item["file"]), item["spec"])
    except Exception as exc:  # noqa: BLE001 - recorded per call so one outage doesn't stop the bakeoff
        result = {"verdict": "error", "findings": [], "error": str(exc), "seconds": None, "cost": None}
    result.update({"model": model, "image": item["id"], "run": run})
    out.write_text(json.dumps(result, indent=1))
    return result["verdict"]


def main() -> None:
    models = sys.argv[1:] or MODELS
    items = json.loads((HERE / "labels.json").read_text())
    tasks = [(m, it, r) for m in models for it in items for r in range(1, RUNS + 1)]
    done = 0
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(job, *t): t for t in tasks}
        for fut in as_completed(futures):
            done += 1
            m, it, r = futures[fut]
            verdict = fut.result()
            if done % 25 == 0 or verdict in ("error", "invalid"):
                print(f"[{done}/{len(tasks)}] {m} {it['id']} run{r}: {verdict}", flush=True)


if __name__ == "__main__":
    main()
