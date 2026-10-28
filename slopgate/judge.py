"""Vision-model review of images against the imagegen antipattern rubric.

Advisory only: model output varies between runs, so it never blocks a PR.
The bakeoff in bakeoff/judge/ picks the model and measures how often it is
right.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath

from PIL import Image

API = "https://openrouter.ai/api/v1/chat/completions"

CATEGORIES = {
    "hands-limbs": "Extra, missing, or fused fingers; impossible joints; limbs that can't connect as shown.",
    "face": (
        "Teeth merging into lips; eyes, eyelids, or pupils that don't agree on "
        "a gaze; features at different scales."
    ),
    "mismatched-pair": (
        "Paired items (eyes, earrings, lenses, gloves, boots, collars) that should match but don't."
    ),
    "contact-weight": (
        "Fingers passing through objects, feet or objects floating, heavy "
        "things with no support, missing contact shadows."
    ),
    "merged-objects": (
        "Separate objects melting into each other with no boundary (fabric into wood, strap into hair)."
    ),
    "construction": "Hinges, buckles, latches, straps, or controls that could not work as drawn.",
    "perspective": "Rigid parts that imply different viewpoints; stairs or windows that don't line up.",
    "lighting": (
        "Shadows or highlights that contradict the light sources, including the lighting the spec asks for."
    ),
    "reflection": "Reflections that show different features, pose, or placement than the reflected object.",
    "lettering": (
        "Near-readable or invented text, garbled signs, labels, or signatures; "
        "any text where the spec forbids text."
    ),
    "count-repetition": (
        "Wrong number of items versus the spec; regular parts that change shape or count; cloned details."
    ),
    "attribute-leak": "One subject inheriting another subject's colors, clothing, or gear.",
    "spec-mismatch": "Anything else the image does differently from the spec it was generated from.",
}

RUBRIC = """You review generated game art for production defects before it ships.

You get one image and the spec it was generated from. Report only defects you can
point to in the image. Exaggerated proportions, toy-like shapes, and painterly
style are deliberate when the spec asks for them; they are not defects.

Categories (use these ids):
{categories}

Severity:
- "blocking": a reviewer would reject the asset for it (wrong finger count, text
  on a sign that must be blank, a floating character, a broken hinge, a count
  that contradicts the spec).
- "advisory": real but minor, or you are less than sure.

Reply with JSON only, no prose before or after:
{{"findings": [{{"category": "<id>", "severity": "blocking|advisory",
"where": "<part of the image>", "evidence": "<what you see, one sentence>"}}],
"verdict": "pass|fail"}}
"verdict" is "fail" if and only if at least one finding is blocking. An image
with no defects gets {{"findings": [], "verdict": "pass"}}."""


def rubric() -> str:
    cats = "\n".join(f"- {k}: {v}" for k, v in CATEGORIES.items())
    return RUBRIC.format(categories=cats)


def encode_image(path: str, max_side: int = 1024) -> str:
    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def parse(text: str) -> dict:
    """Pull the JSON object out of a reply; raise ValueError when there is none."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object in reply")
    data = json.loads(text[start : end + 1])
    findings = data.get("findings")
    if not isinstance(findings, list):
        raise ValueError("reply has no findings list")
    for f in findings:
        if f.get("category") not in CATEGORIES or f.get("severity") not in ("blocking", "advisory"):
            raise ValueError(f"bad finding: {f}")
    data["verdict"] = "fail" if any(f["severity"] == "blocking" for f in findings) else "pass"
    return data


def review(model: str, image_path: str, spec: str, timeout: int = 180) -> dict:
    """One review call. Returns parsed findings plus cost, latency, and raw text."""
    body = {
        "model": model,
        "temperature": 0,
        "max_tokens": 4000,
        "messages": [
            {"role": "system", "content": rubric()},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"Spec the image was generated from:\n{spec}"},
                    {"type": "image_url", "image_url": {"url": encode_image(image_path)}},
                ],
            },
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
    seconds = time.time() - t0
    text = (data["choices"][0]["message"].get("content") or "").strip()
    usage = data.get("usage") or {}
    result = {"seconds": round(seconds, 1), "cost": usage.get("cost"), "raw": text, "error": None}
    try:
        result.update(parse(text))
    except (ValueError, json.JSONDecodeError) as exc:
        result.update({"findings": [], "verdict": "invalid", "error": str(exc)})
    return result


# ------------------------------------------------------------------ PR job

# Chosen by the 2026-10-27 bakeoff (bakeoff/judge/README.md): Gemini covers
# anatomy, contact, and counting; Opus covers conformance to the spec.
DEFAULT_MODELS = ["anthropic/claude-opus-5.5", "google/gemini-4-argon"]
DEFAULT_MAX_IMAGES = 20
NO_SPEC = (
    "No spec was provided. Judge only defects that are wrong in any style: contradictions "
    "within the image itself."
)


def find_spec(repo, rel: str, rule: dict) -> tuple[str, str]:
    """The text the image was generated from, and where it came from.

    A sidecar `<name>.prompt.txt` next to the image wins (the Bomberguy9
    convention), then a `spec` on the matching media manifest rule.
    """
    p = PurePosixPath(rel)
    for sidecar in (p.with_suffix(".prompt.txt"), p.with_suffix(p.suffix + ".prompt.txt")):
        path = Path(repo) / sidecar
        if path.is_file():
            return path.read_text(encoding="utf-8").strip(), str(sidecar)
    if rule.get("spec"):
        return str(rule["spec"]).strip(), "manifest"
    return NO_SPEC, "none"


def merge(results: dict[str, dict]) -> list[dict]:
    """Combine per-model findings. Same category from several models is one row."""
    rows: dict[tuple[str, str], dict] = {}
    for model, r in results.items():
        for f in r.get("findings", []):
            key = (f["category"], f["severity"])
            row = rows.setdefault(
                key,
                {
                    "category": f["category"],
                    "severity": f["severity"],
                    "models": [],
                    "where": f.get("where", ""),
                    "evidence": f.get("evidence", ""),
                },
            )
            if model not in row["models"]:
                row["models"].append(model)
    order = {"blocking": 0, "advisory": 1}
    return sorted(rows.values(), key=lambda r: (order[r["severity"]], -len(r["models"]), r["category"]))


def run(repo, images: list[tuple[str, str, str]], models: list[str], review_fn=None) -> list[dict]:
    """Review each (path, spec, spec_source) with every model in parallel."""
    review_fn = review_fn or review
    jobs = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for rel, spec, _ in images:
            for m in models:
                jobs[(rel, m)] = pool.submit(review_fn, m, str(Path(repo) / rel), spec)
    out = []
    for rel, _, source in images:
        per_model, errors, cost = {}, [], 0.0
        for m in models:
            try:
                r = jobs[(rel, m)].result()
            except Exception as exc:  # noqa: BLE001 - one model's outage is reported, not fatal
                errors.append(f"{m}: {exc}")
                continue
            if r.get("error"):
                errors.append(f"{m}: {r['error']}")
                continue
            per_model[m] = r
            cost += r.get("cost") or 0
        out.append(
            {
                "path": rel,
                "spec_source": source,
                "rows": merge(per_model),
                "errors": errors,
                "cost": cost,
                "models_ok": list(per_model),
            }
        )
    return out


def _short(model: str) -> str:
    return model.split("/", 1)[-1]


def report(results: list[dict], models: list[str], skipped: int) -> str:
    marker = "<!-- slop-judge-report -->"
    flagged = [r for r in results if any(row["severity"] == "blocking" for row in r["rows"])]
    cost = sum(r["cost"] for r in results)
    lines = [
        marker,
        "## slop-judge: vision review (advisory)",
        "",
        f"Reviewed {len(results)} changed image(s) with {', '.join(f'`{_short(m)}`' for m in models)}; "
        f"{len(flagged)} have likely defects. Cost ${cost:.3f}."
        + (f" {skipped} more image(s) were skipped by the `max_images` limit." if skipped else ""),
        "",
        "This review never blocks the merge. Models disagree with each other and with themselves "
        "between runs, so treat a finding from one model as a pointer to look, and one both models "
        "raise as likely real.",
    ]
    for r in results:
        if not r["rows"] and not r["errors"]:
            continue
        lines += ["", f"### `{r['path']}`", ""]
        if r["spec_source"] == "none":
            lines.append(
                "_No spec found (add a `.prompt.txt` sidecar or a manifest `spec`), so only "
                "internal contradictions were checked._\n"
            )
        if r["rows"]:
            lines += ["| | Category | Raised by | What the model saw |", "|---|---|---|---|"]
            for row in r["rows"]:
                icon = "🔶" if row["severity"] == "blocking" else "▫️"
                who = (
                    "both" if len(row["models"]) == len(models) > 1 else ", ".join(map(_short, row["models"]))
                )
                text = f"{row['where']}: {row['evidence']}".replace("|", "\\|").replace("\n", " ")
                lines.append(f"| {icon} | `{row['category']}` | {who} | {text} |")
        for e in r["errors"]:
            lines.append(f"\n⚠️ Review failed: {e[:300]}")
    clean = len([r for r in results if not r["rows"] and not r["errors"]])
    if clean:
        lines += ["", f"{clean} image(s) had no findings."]
    return "\n".join(lines) + "\n"
