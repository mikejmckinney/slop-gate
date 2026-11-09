"""Generate up to three short clips with a video model and review them. Unscored.

    python bakeoff/video/realism.py generate OUT_DIR
    python bakeoff/video/realism.py review OUT_DIR native:google/gemini-4-argon ...

These clips have no planted defect and no labels, so nothing here feeds a metric
in score.py. Replies go to results/realism and are listed for a person to read.
Generation is charged per second of output; the charge is in each job's
usage.cost and is written next to the clip's reply data.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

import run as runner

HERE = Path(__file__).resolve().parent
API = "https://openrouter.ai/api/v1/videos"
MODEL = "google/veo-3.1-lite"
SECONDS = 4
PROMPTS = {
    "gen_marble": "A glass marble rolls along a wooden table and knocks over a small stack of three coins.",
    "gen_street": "A person in a yellow raincoat walks past a shop window on a rainy street at dusk.",
    "gen_kettle": (
        "Close-up of a kettle on a stove, steam rising, a hand lifts it and pours water into a mug."
    ),
}


def api(path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        API + path,
        data=data,
        headers={
            "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def generate(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    costs = {}
    for name, prompt in PROMPTS.items():
        job = api(
            "",
            {
                "model": MODEL,
                "prompt": prompt,
                "duration": SECONDS,
                "resolution": "720p",
                "generate_audio": False,
            },
        )
        for _ in range(120):
            time.sleep(5)
            job = api(f"/{job['id']}")
            if job["status"] in ("completed", "failed"):
                break
        if job.get("status") != "completed":
            raise SystemExit(f"{name}: job did not complete: {json.dumps(job)[:300]}")
        url = job["unsigned_urls"][0]
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"}
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            (out / f"{name}.mp4").write_bytes(r.read())
        costs[name] = (job.get("usage") or {}).get("cost")
        print(name, "cost", costs[name])
    (HERE / "results" / "realism_generation.json").write_text(
        json.dumps({"model": MODEL, "seconds": SECONDS, "prompts": PROMPTS, "cost": costs}, indent=1) + "\n"
    )


def review(out: Path, approaches: list[str], cap: float) -> None:
    gen_dir = HERE / "results" / "realism"
    budget = runner.Budget(cap, HERE / "results")
    for name, prompt in PROMPTS.items():
        item = {"clip": name, "file": f"{name}.mp4", "brief": prompt, "seconds": float(SECONDS)}
        for approach in approaches:
            kind, model = approach.split(":", 1)
            for run in (1, 2):
                dest = gen_dir / approach.replace(":", "__").replace("/", "__") / f"{name}.run{run}.json"
                if dest.exists():
                    continue
                if not budget.reserve():
                    raise SystemExit("stopped at the cap")
                extra: dict = {}
                if kind == "native":
                    content = runner.native_content(item, out)
                else:
                    content, extra["frames"] = runner.strip_content(
                        item, out, Path("/tmp/sgvb/frames"), int(kind[5:])
                    )
                rec = runner.call(model, content)
                rec.update(approach=approach, model=model, clip=name, run=run, **extra)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(json.dumps(rec, indent=1))
                budget.add(rec)
                print(name, approach, run, "ok" if not rec["error"] else rec["error"][:120])
    print("spent on disk", round(budget.spent, 4))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["generate", "review"])
    ap.add_argument("out", type=Path)
    ap.add_argument("approaches", nargs="*")
    ap.add_argument("--cap", type=float, default=19.5)
    args = ap.parse_args()
    if "OPENROUTER_API_KEY" not in os.environ:
        raise SystemExit("OPENROUTER_API_KEY is not set")
    if args.mode == "generate":
        generate(args.out)
    else:
        review(args.out, args.approaches, args.cap)


if __name__ == "__main__":
    main()
