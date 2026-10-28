"""Generate and edit test images through OpenRouter (used only to build the bakeoff set)."""

import base64
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

API = "https://openrouter.ai/api/v1/chat/completions"


def call(model: str, prompt: str, image: Path | None = None, out: Path | None = None) -> dict:
    content = [{"type": "text", "text": prompt}]
    if image:
        b64 = base64.b64encode(image.read_bytes()).decode()
        mime = "image/jpeg" if image.suffix.lower() in (".jpg", ".jpeg") else "image/png"
        content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
    body = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "modalities": ["image", "text"],
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
    with urllib.request.urlopen(req, timeout=300) as r:
        data = json.load(r)
    msg = data["choices"][0]["message"]
    images = msg.get("images") or []
    if not images:
        raise RuntimeError(f"no image returned: {str(msg.get('content'))[:300]}")
    url = images[0]["image_url"]["url"]
    raw = base64.b64decode(url.split(",", 1)[1])
    if out:
        out.write_bytes(raw)
    return {
        "seconds": round(time.time() - t0, 1),
        "cost": data.get("usage", {}).get("cost"),
        "id": data.get("id"),
    }


if __name__ == "__main__":
    model, prompt, out = sys.argv[1], sys.argv[2], Path(sys.argv[3])
    src = Path(sys.argv[4]) if len(sys.argv) > 4 else None
    print(json.dumps(call(model, prompt, src, out)))
