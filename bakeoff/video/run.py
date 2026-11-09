"""Send every clip to every approach, twice, and keep each raw reply.

    set -a; . openrouter.env; set +a
    python bakeoff/video/run.py CLIP_DIR native:google/gemini-4-argon strip4:anthropic/claude-opus-5.5 ...

An approach is native:<model> (the MP4 as a video_url part) or strip<fps>:<model>
(frames sampled at <fps>, as image parts with their timestamps, no audio).
Replies land in results/<set>/<approach>/<clip>.run<N>.json and are not repeated
on a re-run. Spend is the sum of usage.cost over every reply on disk, and the run
stops before the cap instead of after it. A failed call is recorded and scored as
failed; a timed-out call counts at the highest cost seen, since it may still bill.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
API = "https://openrouter.ai/api/v1/chat/completions"
FPS = 24  # every clip in labels.json is 24 fps

PICTURE_PATTERNS = [
    "Flicker or texture boil",
    "Identity or background drift",
    "Objects appear, vanish, or merge",
    "Floaty timing",
    "Contact slip",
    "Action stalls, reverses, or skips",
    "Near-static clip",
    "Implausible camera path",
    "Implausible physics",
    "Broken cause and effect",
    "Unstable or unreadable overlays",
    "Visible clip seams",
    "Continuity errors",
    "Confusing screen direction",
]
AUDIO_PATTERNS = [
    "Lip-sync error",
    "Sound event misses picture",
    "Sound contradicts space",
    "Narration-picture mismatch",
]

PROMPT = """You review one video for unintended defects before it ships.

{medium}

What the video is meant to be: {brief}

Report only defects you can point to, each with the time range where it occurs.
Use only these pattern names: {patterns}.
Do not report deliberate style: jump cuts, handheld shake, stepped stop-motion
timing, animation smears, slow motion, film grain and stylized physics are fine.
Simple drawn graphics are fine. Do not guess whether the video was made by AI.
Compression and motion blur can look like a fault in one frame: report a defect
only if it holds across consecutive frames. If you see nothing wrong, return no
findings.

For "basis" say how you know: "frame inspection" for what you saw in frames,
"listening" only if you actually received and heard sound, or "inference".
Never claim that a person watched or listened.

Answer with JSON only, no prose:
{{"findings": [{{"start_s": number, "end_s": number, "pattern": "<one of the names>",
  "evidence": "<what you see or hear, one sentence>", "impact": "<why it matters, one sentence>",
  "confidence": "high|medium|low", "basis": "frame inspection|listening|inference",
  "fix": "<proposed fix, one sentence>"}}]}}"""

MEDIUM_NATIVE = (
    "You receive the video file. It may have a sound track; if it has none, or you cannot hear it, "
    "do not report sound patterns. Times are seconds from the start of the file."
)
MEDIUM_STRIP = (
    "You receive {n} frames sampled from the video at {fps} frames per second, in order, each "
    "preceded by its timestamp. The clip is {seconds:.1f} seconds long. You have no audio and no "
    "motion between the frames, so report only what the frames show."
)


class Budget:
    """Keeps the cap honest across threads, including replies already on disk."""

    def __init__(self, cap: float, root: Path) -> None:
        self.cap, self.root, self.lock = cap, root, threading.Lock()
        self.spent = 0.0
        self.unknown = self.seen_high = 0.0
        self.stopped = False
        for f in root.rglob("*.run*.json"):
            rec = json.loads(f.read_text())
            if isinstance(rec.get("cost"), (int, float)):
                self.spent += rec["cost"]
                self.seen_high = max(self.seen_high, rec["cost"])

    def reserve(self) -> bool:
        with self.lock:
            if self.spent + self.unknown >= self.cap:
                self.stopped = True
                return False
            return True

    def add(self, rec: dict) -> None:
        with self.lock:
            if isinstance(rec.get("cost"), (int, float)):
                self.spent += rec["cost"]
                self.seen_high = max(self.seen_high, rec["cost"])
            elif rec.get("maybe_billed"):
                self.unknown += self.seen_high


def post(body: dict, timeout: int = 240) -> dict:
    req = urllib.request.Request(
        API,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def frames_for(clip: Path, cache: Path, every: int) -> list[Path]:
    """The clip's frames at one per `every` source frames, named by frame number."""
    out = cache / f"{clip.stem}_{every}"
    if not (out / ".done").exists():
        out.mkdir(parents=True, exist_ok=True)
        vf = f"select='not(mod(n,{every}))'"
        cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(clip), "-vf", vf]
        cmd += ["-vsync", "0", "-q:v", "3", "-frame_pts", "1", str(out / "%05d.jpg")]
        subprocess.run(cmd, check=True, capture_output=True)
        if not list(out.glob("*.jpg")):
            raise RuntimeError(f"{clip}: no frames sampled")
        (out / ".done").write_text("")
    return sorted(out.glob("*.jpg"))


def native_content(item: dict, clips: Path) -> list[dict]:
    b64 = base64.b64encode((clips / item["file"]).read_bytes()).decode()
    text = PROMPT.format(
        medium=MEDIUM_NATIVE, brief=item["brief"], patterns="; ".join(PICTURE_PATTERNS + AUDIO_PATTERNS)
    )
    return [
        {"type": "text", "text": text},
        {"type": "video_url", "video_url": {"url": f"data:video/mp4;base64,{b64}"}},
    ]


def strip_content(item: dict, clips: Path, cache: Path, fps: int) -> tuple[list[dict], int]:
    frames = frames_for(clips / item["file"], cache, FPS // fps)
    medium = MEDIUM_STRIP.format(n=len(frames), fps=fps, seconds=item["seconds"])
    patterns = PICTURE_PATTERNS + [
        "Narration-picture mismatch"
    ]  # a caption contradicting the picture needs no sound
    text = PROMPT.format(medium=medium, brief=item["brief"], patterns="; ".join(patterns))
    content: list[dict] = [{"type": "text", "text": text}]
    for frame in frames:
        n = int(frame.stem)
        b64 = base64.b64encode(frame.read_bytes()).decode()
        content.append({"type": "text", "text": f"t={n / FPS:.3f} s"})
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    return content, len(frames)


def call(model: str, content: list[dict]) -> dict:
    body = {
        "model": model,
        "temperature": 0,
        "max_tokens": 6000,
        "messages": [{"role": "user", "content": content}],
        "usage": {"include": True},
    }
    rec: dict = {"text": None, "cost": None, "error": None, "maybe_billed": False}
    t0 = time.time()
    for attempt in range(3):
        try:
            data = post(body)
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:400].decode("utf-8", "replace")
            rec["error"] = f"HTTP {exc.code}: {detail}"
            if exc.code not in (408, 429, 500, 502, 503, 504) or attempt == 2:
                break
            time.sleep(6 * (attempt + 1))
            continue
        except (OSError, json.JSONDecodeError) as exc:
            rec["error"] = f"{type(exc).__name__}: {exc}"
            rec["maybe_billed"] = True
            if attempt == 2:
                break
            time.sleep(6 * (attempt + 1))
            continue
        rec["seconds"] = round(time.time() - t0, 1)
        rec["attempts"] = attempt + 1
        if "error" in data and not data.get("choices"):
            rec["error"] = json.dumps(data["error"])[:400]
            usage = data.get("usage") or {}
            rec["cost"] = usage.get("cost")
            break
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        usage = data.get("usage") or {}
        rec.update(
            text=msg.get("content") or "",
            finish_reason=choice.get("finish_reason"),
            native_finish=choice.get("native_finish_reason"),
            provider=data.get("provider"),
            served_model=data.get("model"),
            cost=usage.get("cost"),
            usage={k: usage.get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens")},
            usage_detail={
                "prompt": usage.get("prompt_tokens_details"),
                "completion": usage.get("completion_tokens_details"),
            },
        )
        if not rec["text"]:
            rec["error"] = f"empty reply, finish_reason={rec['finish_reason']}"
        else:
            rec["error"] = None
        break
    return rec


def job(approach: str, item: dict, run: int, args, budget: Budget) -> str:
    kind, model = approach.split(":", 1)
    dest = args.out / approach.replace(":", "__").replace("/", "__") / f"{item['clip']}.run{run}.json"
    if dest.exists():
        return "cached"
    if not budget.reserve():
        return "skipped (cap)"
    extra: dict = {}
    if kind == "native":
        content = native_content(item, args.clips)
    else:
        content, extra["frames"] = strip_content(item, args.clips, args.cache, int(kind[5:]))
    rec = call(model, content)
    rec.update(approach=approach, model=model, clip=item["clip"], run=run, **extra)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(rec, indent=1))
    budget.add(rec)
    return "error" if rec["error"] else "ok"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("clips", type=Path)
    ap.add_argument("approaches", nargs="+")
    ap.add_argument("--set", default="raw", help="results/<set> directory")
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--only", help="comma-separated clip names")
    ap.add_argument("--cap", type=float, default=18.0)
    ap.add_argument("--cache", type=Path, default=Path("/tmp/sgvb/frames"))
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    if "OPENROUTER_API_KEY" not in os.environ:
        raise SystemExit("OPENROUTER_API_KEY is not set")
    args.out = HERE / "results" / args.set
    items = json.loads((HERE / "labels.json").read_text())
    if args.only:
        wanted = set(args.only.split(","))
        items = [it for it in items if it["clip"] in wanted]
    budget = Budget(args.cap, HERE / "results")
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
