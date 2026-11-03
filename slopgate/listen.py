"""Model listening review of audio against the audio-generation antipattern list.

Advisory only, like the vision review: models miss defects and invent others,
so this never blocks a PR. Two parts:

- review: a model listens to the whole file and reports defects by time range,
  using the pattern names from the antipattern list. Each finding is labelled
  as inference from model listening, with the model's own confidence.
- transcript check: when a `<name>.script.txt` or `<name>.lyrics.txt` sidecar
  holds the words the audio should contain, a model transcribes the audio and
  the words are compared in code, so a misread number or a dropped word shows
  up as a diff rather than as the model's opinion.

The bakeoff in bakeoff/listen/ picked the models and measured them.
"""

from __future__ import annotations

import base64
import difflib
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath

API = "https://openrouter.ai/api/v1/chat/completions"

# Pattern name -> the antipattern list's fix, shortened.
PATTERNS = {
    "metallic shimmer / watery modulation / chirping": (
        "Compare with the unprocessed source; undo damaging processing, or replace the passage."
    ),
    "steady hiss": "Gentle noise reduction with a noise-only profile; stop when wanted detail goes.",
    "soft or smeared attacks": "Enhance surviving attacks with care, or replace the hits or replay the part.",
    "instrument morphing or smear": "Clarify roles, simplify competing parts, or regenerate the phrase.",
    "uncontrolled pitch or texture wobble": (
        "Correct isolated notes on clean parts; regenerate or replay pervasive wobble. Keep intended vibrato."
    ),
    "reverb wash or changing room": "Adjust sends and tails, or match the space between passages.",
    "clicks, gaps, severed tails": "Repair locally and crossfade the join; recheck it in context.",
    "unstable stereo image": "Compare stereo and mono; fix widening or phase on the stem.",
    "flat dynamics, unwanted pumping, clipping": "Find the cause in arrangement, automation or limiting.",
    "unwanted mechanical vocal delivery": (
        "Regenerate coherent phrases; fix specific defects, not with blanket EQ."
    ),
    "voice or accent drift": "Keep voice settings consistent and regenerate the passage with its context.",
    "garbled words or accidental language": "Compare with the intended text; simplify or replace the line.",
    "misplaced or mechanical breaths": "Edit or regenerate; add breaths only where the phrasing needs them.",
    "repetition without purpose": "Develop the returns through phrasing, register or rhythm, or shorten.",
    "disconnected sections": "Connect sections with shared material or a transition.",
    "buildup without payoff": "Create contrast before the intended peak.",
    "competing foreground parts": "Clarify roles with call and response, register, balance or muting.",
    "wrong stress or repetitive cadence": "Rewrite for speech and regenerate whole phrases.",
    "unnatural pauses or acceleration": "Generate whole sentences; adjust pacing in context.",
}

PROMPT = """You are reviewing one audio file for unintended defects before it ships.
Listen to the whole file. Report only defects you can hear, with the time range where they occur.
Use only these pattern names: {patterns}.
Do not report deliberate style (quantized timing, detuning, distortion, vocoding, sidechain pumping,
steady loops, wordless vocals) as a defect. Do not guess whether the audio was made by AI.
If you hear nothing wrong, return no findings.

What the file is meant to be: {brief}

Answer with JSON only, no prose:
{{"findings": [{{"start_s": number, "end_s": number, "pattern": "<one of the names>",
  "evidence": "<what you hear, one sentence>", "confidence": "high|medium|low"}}]}}"""

TRANSCRIBE = (
    "Transcribe the speech or sung words in this audio verbatim. Write numbers the way they are "
    "spoken, as digits. Answer with the transcript only."
)

NO_BRIEF = "No brief was given. Judge only defects that are wrong in any style."

# Chosen by the 2026-11-02 bakeoff (bakeoff/listen/README.md): together the two
# review models placed all six planted defects with no medium or high finding
# on the clean clips; Inkling transcribed the speech clips word for word.
DEFAULT_MODELS = ["google/gemini-3.1-pro-preview", "google/gemini-4-argon"]
DEFAULT_TRANSCRIBE_MODEL = "thinkingmachines/inkling"
DEFAULT_MAX_AUDIO = 10
MAX_SECONDS = 300
TEXT_SIDECARS = (".script.txt", ".lyrics.txt")


def prompt(brief: str) -> str:
    return PROMPT.format(patterns="; ".join(PATTERNS), brief=brief or NO_BRIEF)


def _encode(path: Path, fmt: str) -> bytes:
    """The first MAX_SECONDS as MP3 (stereo, 128 kb/s) or WAV (mono, 16 kHz)."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / f"clip.{fmt}"
        codec = ["-ac", "2", "-b:a", "128k"] if fmt == "mp3" else ["-ac", "1", "-ar", "16000"]
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", str(path), "-t", str(MAX_SECONDS), *codec, str(out)],
            check=True,
            capture_output=True,
        )
        return out.read_bytes()


def _call(
    model: str, text: str, audio: bytes, fmt: str, timeout: int = 300
) -> tuple[str, float | None, float]:
    body = {
        "model": model,
        "temperature": 0,
        "max_tokens": 3000,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": text},
                    {
                        "type": "input_audio",
                        "input_audio": {"data": base64.b64encode(audio).decode(), "format": fmt},
                    },
                ],
            }
        ],
    }
    req = urllib.request.Request(
        API,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
            "Content-Type": "application/json",
        },
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{model}: HTTP {exc.code} {exc.read()[:300]!r}") from exc
    content = (data["choices"][0]["message"].get("content") or "").strip()
    return content, (data.get("usage") or {}).get("cost"), time.time() - t0


def parse(text: str) -> list[dict]:
    """Findings from a reply; raise ValueError when the reply has none in the agreed shape."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object in reply")
    findings = json.loads(text[start : end + 1]).get("findings")
    if not isinstance(findings, list):
        raise ValueError("reply has no findings list")
    out = []
    for f in findings:
        pattern = str(f.get("pattern", "")).strip().lower()
        name = next((p for p in PATTERNS if p == pattern or p.split(" /")[0] == pattern), None)
        if name is None or f.get("confidence") not in ("high", "medium", "low"):
            raise ValueError(f"bad finding: {f}")
        out.append(
            {
                "start_s": float(f.get("start_s", 0)),
                "end_s": float(f.get("end_s", 0)),
                "pattern": name,
                "evidence": str(f.get("evidence", "")),
                "confidence": f["confidence"],
            }
        )
    return out


def review(model: str, path: Path, brief: str) -> dict:
    audio = _encode(path, "mp3")
    text, cost, seconds = _call(model, prompt(brief), audio, "mp3")
    result = {"seconds": round(seconds, 1), "cost": cost, "raw": text, "error": None, "findings": []}
    try:
        result["findings"] = parse(text)
    except (ValueError, json.JSONDecodeError) as exc:
        result["error"] = str(exc)
    return result


def transcribe(model: str, path: Path) -> tuple[str, float | None]:
    text, cost, _ = _call(model, TRANSCRIBE, _encode(path, "wav"), "wav")
    return text, cost


def _words(text: str) -> list[str]:
    text = re.sub(r"(?<=\d),(?=\d{3})", "", text.lower())  # 1,250 -> 1250
    return re.findall(r"[a-z0-9']+", text)


def word_diff(expected: str, heard: str) -> list[str]:
    """Human-readable differences, each with a few words of context."""
    a, b = _words(expected), _words(heard)
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        context = " ".join(a[max(0, i1 - 3) : i1])
        want, got = " ".join(a[i1:i2]) or "(nothing)", " ".join(b[j1:j2]) or "(nothing)"
        out.append(f"after “{context}”: expected “{want}”, heard “{got}”")
    return out


def find_text(repo: Path, rel: str) -> tuple[str, str] | None:
    """The words the audio should contain, from a `.script.txt` or `.lyrics.txt` sidecar."""
    p = PurePosixPath(rel)
    for suffix in TEXT_SIDECARS:
        for side in (p.with_suffix(suffix), p.with_suffix(p.suffix + suffix)):
            if (repo / side).is_file():
                return (repo / side).read_text(encoding="utf-8").strip(), str(side)
    return None


def find_brief(repo: Path, rel: str, rule: dict) -> str:
    p = PurePosixPath(rel)
    for side in (p.with_suffix(".prompt.txt"), p.with_suffix(p.suffix + ".prompt.txt")):
        if (repo / side).is_file():
            return (repo / side).read_text(encoding="utf-8").strip()
    return str(rule.get("spec") or "").strip()


def run(repo: Path, items: list[tuple[str, str]], models: list[str], transcribe_model: str) -> list[dict]:
    """Review each (path, brief) with every model, and check its words when a sidecar has them."""
    out = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = {(rel, m): pool.submit(review, m, repo / rel, brief) for rel, brief in items for m in models}
        texts = {}
        for rel, _ in items:
            expected = find_text(repo, rel)
            if expected:
                texts[rel] = (expected, pool.submit(transcribe, transcribe_model, repo / rel))
        for rel, _ in items:
            rows, errors, cost = [], [], 0.0
            for m in models:
                try:
                    r = jobs[(rel, m)].result()
                except Exception as exc:  # noqa: BLE001 - one model's outage is reported, not fatal
                    errors.append(f"{m}: {exc}")
                    continue
                cost += r.get("cost") or 0
                if r["error"]:
                    errors.append(f"{m}: {r['error']}")
                rows += [{**f, "model": m} for f in r["findings"]]
            diff = None
            if rel in texts:
                (expected, source), fut = texts[rel]
                try:
                    heard, c = fut.result()
                    cost += c or 0
                    diff = {"source": source, "changes": word_diff(expected, heard)}
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{transcribe_model} transcript: {exc}")
            rows = merge(rows)
            out.append({"path": rel, "rows": rows, "diff": diff, "errors": errors, "cost": cost})
    return out


def merge(rows: list[dict]) -> list[dict]:
    """One row per pattern and stretch of time: findings from different models
    that name the same pattern over overlapping ranges become one row."""
    merged: list[dict] = []
    for f in sorted(rows, key=lambda f: (f["pattern"], f["start_s"])):
        last = merged[-1] if merged else None
        if last and last["pattern"] == f["pattern"] and f["start_s"] <= last["end_s"]:
            last["end_s"] = max(last["end_s"], f["end_s"])
            if f["model"] not in last["models"]:
                last["models"].append(f["model"])
                last["confidence"] = max(last["confidence"], f["confidence"], key=CONFIDENCE.index)
            continue
        merged.append({**f, "models": [f["model"]]})
    return sorted(merged, key=lambda f: (f["start_s"], f["pattern"]))


CONFIDENCE = ["low", "medium", "high"]


def _short(model: str) -> str:
    return model.split("/", 1)[-1]


def clock(seconds: float) -> str:
    m, s = divmod(max(seconds, 0), 60)
    return f"{int(m)}:{s:04.1f}"


def report(results: list[dict], models: list[str], skipped: int) -> str:
    if not results:
        return ""
    cost = sum(r["cost"] for r in results)
    lines = [
        "## Listening review (advisory)",
        "",
        f"Listened to {len(results)} changed audio file(s) with "
        f"{', '.join(f'`{_short(m)}`' for m in models)}. Cost ${cost:.3f}."
        + (f" {skipped} more file(s) were skipped by the `max_audio` limit." if skipped else ""),
        "",
        "These findings are inference from model listening, not measurement, and not a human "
        "listening check. Listen to each time range before acting on it; a range both models raise "
        "is more likely real.",
    ]
    for r in results:
        if not r["rows"] and not r["errors"] and not (r["diff"] and r["diff"]["changes"]):
            continue  # listed in the count of clean files below
        lines += ["", f"### `{r['path']}`", ""]
        if r["rows"]:
            lines += [
                "| Time | Pattern | What the model heard | Confidence and basis | Possible fix |",
                "|---|---|---|---|---|",
            ]
            for f in r["rows"]:
                when = f"{clock(f['start_s'])}–{clock(f['end_s'])}"
                text = f["evidence"].replace("|", "\\|").replace("\n", " ")
                who = (
                    "both models"
                    if len(f["models"]) == len(models) > 1
                    else ", ".join(map(_short, f["models"]))
                )
                basis = f"{f['confidence']}, model listening ({who})"
                lines.append(f"| {when} | {f['pattern']} | {text} | {basis} | {PATTERNS[f['pattern']]} |")
        if r["diff"] and r["diff"]["changes"]:
            if r["rows"]:
                lines.append("")
            lines += [
                f"Words differ from `{r['diff']['source']}` (model transcript compared in code):",
                "",
            ]
            lines += [f"- {c}" for c in r["diff"]["changes"][:15]]
        for e in r["errors"]:
            lines.append(f"\n⚠️ Review failed: {e[:300]}")

    def quiet(r):
        return not r["rows"] and not r["errors"] and not (r["diff"] and r["diff"]["changes"])

    clean = [r for r in results if quiet(r)]
    if clean:
        lines += ["", f"{len(clean)} audio file(s) had no findings."]
    return "\n".join(lines) + "\n"
