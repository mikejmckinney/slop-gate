"""Bakeoff: which audio-capable model to use for the advisory listening review.

Task 1 (review): each clip, twice per model; findings as JSON.
Task 2 (transcribe): the three speech clips; verbatim text, scored by WER.

    python run.py <clip dir> <results dir> <model,model,...>   (env: OPENROUTER_API_KEY)

Results already on disk are skipped, so a rerun only fills gaps. A model that
rejects MP3 is retried with 16 kHz mono WAV.
"""

import base64
import concurrent.futures as cf
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

BAKE = Path(sys.argv[1])
OUT = Path(sys.argv[2])
MODELS = sys.argv[3].split(",")
OUT.mkdir(exist_ok=True)

PATTERNS = [
    "metallic shimmer / watery modulation / chirping",
    "steady hiss",
    "soft or smeared attacks",
    "instrument morphing or smear",
    "uncontrolled pitch or texture wobble",
    "reverb wash or changing room",
    "clicks, gaps, severed tails",
    "unstable stereo image",
    "flat dynamics, unwanted pumping, clipping",
    "unwanted mechanical vocal delivery",
    "voice or accent drift",
    "garbled words or accidental language",
    "misplaced or mechanical breaths",
    "repetition without purpose",
    "disconnected sections",
    "buildup without payoff",
    "competing foreground parts",
    "wrong stress or repetitive cadence",
    "unnatural pauses or acceleration",
]

REVIEW = f"""You are reviewing one audio file for unintended defects before it ships in a game.
Listen to the whole file. Report only defects you can hear, with the time range where they occur.
Use only these pattern names: {"; ".join(PATTERNS)}.
Do not report deliberate style (quantized timing, detuning, distortion, sidechain pumping, steady loops)
as a defect. Do not guess whether the audio was made by AI. If you hear nothing wrong, return no findings.

Answer with JSON only, no prose:
{{"findings": [{{"start_s": number, "end_s": number, "pattern": "<one of the names>",
  "evidence": "<what you hear, one sentence>", "confidence": "high|medium|low"}}]}}"""

TRANSCRIBE = (
    "Transcribe the speech in this audio verbatim. Write numbers the way they are spoken, as digits. "
    "Answer with the transcript only."
)


def call(model, prompt, path, fmt="mp3", max_tokens=1500):
    b64 = base64.b64encode(path.read_bytes()).decode()
    body = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "input_audio", "input_audio": {"data": b64, "format": fmt}},
                ],
            }
        ],
        "max_tokens": max_tokens,
        "temperature": 0,
    }
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"],
            "Content-Type": "application/json",
        },
    )
    t0 = time.time()
    r = json.load(urllib.request.urlopen(req, timeout=300))
    msg = r["choices"][0]["message"].get("content") or ""
    return msg, r.get("usage", {}).get("cost"), time.time() - t0


def wav_of(path):
    w = path.with_suffix(".wav")
    if not w.exists():
        cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(path), "-ac", "1", "-ar", "16000", str(w)]
        subprocess.run(cmd, check=True)
    return w


def job(model, task, clip, run):
    path = BAKE / clip
    key = f"{model.replace('/', '__')}__{task}__{clip}__{run}.json"
    dest = OUT / key
    if dest.exists():
        return
    prompt = REVIEW if task == "review" else TRANSCRIBE
    rec = {"model": model, "task": task, "clip": clip, "run": run}
    for fmt, p in (("mp3", path), ("wav", None)):
        try:
            p = p or wav_of(path)
            text, cost, secs = call(model, prompt, p, fmt)
            rec.update(text=text, cost=cost, secs=round(secs, 1), fmt=fmt)
            if text.strip():
                break
        except Exception as exc:  # noqa: BLE001 - record and try wav
            body = getattr(exc, "read", lambda: b"")()
            rec.update(error=f"{exc} {body[:300]!r}", fmt=fmt)
    dest.write_text(json.dumps(rec))


SPEECH = ["speech_clean.mp3", "speech_misread.mp3", "speech_drift.mp3"]
CLIPS = sorted(p.name for p in BAKE.glob("music_*.mp3")) + SPEECH
jobs = [(m, "review", c, r) for m in MODELS for c in CLIPS for r in (1, 2)]
jobs += [(m, "transcribe", c, 1) for m in MODELS for c in SPEECH]
with cf.ThreadPoolExecutor(8) as ex:
    list(ex.map(lambda j: job(*j), jobs))
print("done", len(jobs))
