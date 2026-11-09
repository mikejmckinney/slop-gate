"""Send every GeneVA clip to every approach once and keep each raw reply.

    set -a; . openrouter.env; set +a
    python -I bakeoff/video/geneva/review.py CLIP_DIR native:google/gemini-4-argon strip4:openai/gpt-6.1-terra ...

An approach is native:<model> (the MP4 as a video_url part) or strip4:<model> (frames at
4 per second, long side at most 640 px, each preceded by its timestamp). Replies land in
results/<set>/<approach>/<clip>.run<N>.json and aren't repeated on a re-run. The cap covers
every reply under results/, using the attempt-level reservation in ../run.py.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import run as base  # noqa: E402  (../run.py)
from pick_clips import SAFE_INPUT  # noqa: E402

STRIP_FPS = 4
LONG_SIDE = 640

PROMPT = """You review one short video clip for visual artifacts.

{medium}

The text prompt or brief for this clip is not available, so do not judge whether it matches an
intended subject. Report only artifacts you can point to, each with the time range where it
occurs and one box around it.

Use only these category names: Shape/Form/Geometry; Visual Artifacts; Motion; Physics; Other.
Do not report deliberate style: jump cuts, handheld shake, stepped stop-motion timing, animation
smears, slow motion, film grain and stylized physics are fine. Do not guess how the video was
made. A generator's watermark or logo is not a defect, so do not report it. Compression and motion
blur can look like a fault in one frame: report an artifact only if it holds across consecutive
frames. If you see nothing wrong, return no findings.

For "basis" say how you know: "frame inspection" for what you saw in frames, or "inference".
Never claim that a person watched the clip.

"box" is [x, y, w, h] as fractions of the frame from 0 to 1, with x and y at the top-left corner.

Answer with JSON only, no prose:
{{"findings": [{{"start_s": number, "end_s": number, "category": "<one of the names>",
  "box": [x, y, w, h], "evidence": "<what you see, one sentence>",
  "confidence": "high|medium|low", "basis": "frame inspection|inference"}}]}}"""

MEDIUM_NATIVE = "You receive the video file. Times are seconds from the start of the file."


def frames_for(clip: Path, cache: Path) -> list[Path]:
    """The clip's frames at STRIP_FPS, named in order, so frame i is at i / STRIP_FPS seconds."""
    out = cache / clip.stem
    if not (out / ".done").exists():
        out.mkdir(parents=True, exist_ok=True)
        side = f"min({LONG_SIDE}\\,iw)", f"min({LONG_SIDE}\\,ih)"
        vf = f"fps={STRIP_FPS},scale=w='if(gte(iw\\,ih)\\,{side[0]}\\,-2)':h='if(gte(iw\\,ih)\\,-2\\,{side[1]})'"
        cmd = ["ffmpeg", "-v", "error", "-y", *SAFE_INPUT, "-i", str(clip), "-vf", vf]
        cmd += ["-q:v", "3", "-start_number", "0", str(out / "%05d.jpg")]
        subprocess.run(cmd, check=True, capture_output=True)
        if not list(out.glob("*.jpg")):
            raise RuntimeError(f"{clip}: no frames sampled")
        (out / ".done").write_text("")
    return sorted(out.glob("*.jpg"))


def native_content(clip: Path) -> list[dict]:
    b64 = base64.b64encode(clip.read_bytes()).decode()
    return [
        {"type": "text", "text": PROMPT.format(medium=MEDIUM_NATIVE)},
        {"type": "video_url", "video_url": {"url": f"data:video/mp4;base64,{b64}"}},
    ]


def strip_content(clip: Path, cache: Path, seconds: float) -> tuple[list[dict], int]:
    frames = frames_for(clip, cache)
    medium = base.MEDIUM_STRIP.format(n=len(frames), fps=STRIP_FPS, seconds=seconds)
    content: list[dict] = [{"type": "text", "text": PROMPT.format(medium=medium)}]
    for i, frame in enumerate(frames):
        content.append({"type": "text", "text": f"t={i / STRIP_FPS:.3f} s"})
        b64 = base64.b64encode(frame.read_bytes()).decode()
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    return content, len(frames)


def job(approach: str, item: dict, run: int, args, budget: base.Budget) -> str:
    kind, model = approach.split(":", 1)
    dest = args.out / approach.replace(":", "__").replace("/", "__") / f"{item['clip']}.run{run}.json"
    if dest.exists():
        return "cached"
    clip = args.clips / f"{item['clip']}.mp4"
    extra: dict = {}
    if kind == "native":
        content = native_content(clip)
    else:
        content, extra["frames"] = strip_content(clip, args.cache, item["seconds"])
    rec = base.call(model, content, budget, base.request_bound(content, item["seconds"]))
    if rec.get("refused"):
        return "skipped (cap)"
    rec.update(approach=approach, model=model, clip=item["clip"], run=run, **extra)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(rec, indent=1))
    return "error" if rec["error"] else "ok"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("clips", type=Path)
    ap.add_argument("approaches", nargs="+")
    ap.add_argument("--set", default="raw", help="results/<set> directory")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--only", help="comma-separated clip names")
    ap.add_argument("--cap", type=float, default=20.0)
    ap.add_argument("--cache", type=Path, default=Path("/tmp/gv/frames"))
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    if "OPENROUTER_API_KEY" not in os.environ:
        raise SystemExit("OPENROUTER_API_KEY is not set")
    args.out = HERE / "results" / args.set
    items = json.loads((HERE / "selection.json").read_text())["clips"]
    if args.only:
        wanted = set(args.only.split(","))
        items = [it for it in items if it["clip"] in wanted]
    budget = base.Budget(args.cap, HERE / "results")
    tasks = [(a, it, r) for a in args.approaches for it in items for r in range(1, args.runs + 1)]
    counts: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for status in pool.map(lambda t: job(t[0], t[1], t[2], args, budget), tasks):
            counts[status] = counts.get(status, 0) + 1
    print(f"spent on disk ${budget.spent:.4f}, unknown ${budget.unknown:.4f}, cap ${args.cap}", counts)
    if budget.stopped:
        raise SystemExit("stopped at the cap")


if __name__ == "__main__":
    main()
