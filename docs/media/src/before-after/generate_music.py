"""Music bakeoff: the same brief to Suno (kie.ai) and Mureka, two takes each.

    python generate_music.py suno|mureka

Writes brief.txt, the provider's JSON and the takes (suno_N.mp3, mureka_N.mp3 and .flac)
to the current folder. Both providers bill per request.
"""

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

OUT = Path.cwd()
BRIEF = (
    "Looping background music for a top-down arena bomber game. Upbeat synth with chiptune leads, "
    "punchy drums, steady energy, 120 BPM, instrumental, no vocals, no intro, no fade-out."
)
(OUT / "brief.txt").write_text(BRIEF + "\n")


def req(url, key, body=None):
    r = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    try:
        return json.load(urllib.request.urlopen(r, timeout=120))
    except urllib.error.HTTPError as e:
        return {"http_error": e.code, "body": e.read()[:500].decode(errors="replace")}


def suno():
    key = os.environ["KIE_API_KEY"]
    task = req(
        "https://api.kie.ai/api/v1/generate",
        key,
        {
            "prompt": BRIEF,
            "customMode": False,
            "instrumental": True,
            "model": sys.argv[2] if len(sys.argv) > 2 else "V5",
            "callBackUrl": "https://example.com/callback",
        },
    )
    print("suno submit", json.dumps(task)[:400], flush=True)
    tid = (task.get("data") or {}).get("taskId")
    if not tid:
        return
    for _ in range(90):
        time.sleep(10)
        d = req(f"https://api.kie.ai/api/v1/generate/record-info?taskId={tid}", key)
        st = (d.get("data") or {}).get("status")
        print("suno", st, flush=True)
        if st == "SUCCESS":
            (OUT / "suno.json").write_text(json.dumps(d, indent=1))
            for i, s in enumerate(d["data"]["response"]["sunoData"]):
                urllib.request.urlretrieve(s["audioUrl"], OUT / f"suno_{i}.mp3")
            return
        if st and ("FAIL" in st or "ERROR" in st):
            print(json.dumps(d)[:600])
            return


def mureka():
    key = os.environ["MUREKA_API_KEY"]
    task = req(
        "https://api.mureka.ai/v1/instrumental/generate",
        key,
        {"model": "mureka-9.5", "prompt": BRIEF, "n": 2},
    )
    print("mureka submit", json.dumps(task)[:400], flush=True)
    tid = task.get("id")
    if not tid:
        return
    for _ in range(90):
        time.sleep(10)
        d = req(f"https://api.mureka.ai/v1/instrumental/query/{tid}", key)
        st = d.get("status")
        print("mureka", st, flush=True)
        if st == "succeeded":
            (OUT / "mureka.json").write_text(json.dumps(d, indent=1))
            for c in d["choices"]:
                i = c.get("index", 0)
                urllib.request.urlretrieve(c["url"], OUT / f"mureka_{i}.mp3")
                if c.get("flac_url"):
                    urllib.request.urlretrieve(c["flac_url"], OUT / f"mureka_{i}.flac")
            return
        if st in ("failed", "timeouted", "cancelled"):
            print(json.dumps(d)[:600])
            return


{"suno": suno, "mureka": mureka}[sys.argv[1]]()
