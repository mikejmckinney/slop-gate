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
