"""Score bakeoff results: planted defects found, false alarms on clean clips, transcript WER.

python score.py results
"""

import collections
import glob
import json
import re
import sys
from pathlib import Path

R = sys.argv[1]
GT = {
    "music_warble.mp3": (r"wobble", 10, 18),
    "music_metallic.mp3": (r"metallic|watery|chirp|morph|smear", 8, 16),
    "music_wash.mp3": (r"reverb", 12, 22),
    "music_repeat.mp3": (r"repetition", None, None),
    "music_splice.mp3": (r"disconnected", 13, 17),
    "speech_drift.mp3": (r"drift", 6, 11),
}
CLEAN = {"music_clean.mp3", "speech_clean.mp3"}
SCRIPT = (Path(__file__).parent / "script.txt").read_text()
SPOKEN = {
    "speech_clean.mp3": SCRIPT,
    "speech_drift.mp3": SCRIPT,
    "speech_misread.mp3": SCRIPT.replace("1,250", "12,050").replace("then release", "release"),
}


def parse(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0)).get("findings", [])
    except json.JSONDecodeError:
        return None


def words(t):
    t = t.lower().replace(",", "")
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    return t.split()


def wer(ref, hyp):
    r, h = words(ref), words(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = d[j]
            d[j] = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev = cur
    return d[-1] / max(len(r), 1)


stats = collections.defaultdict(lambda: collections.Counter())
details = collections.defaultdict(dict)
for f in glob.glob(R + "/*.json"):
    rec = json.loads(Path(f).read_text())
    m, task, clip, run = rec["model"], rec["task"], rec["clip"], rec["run"]
    s = stats[m]
    s["cost"] += rec.get("cost") or 0
    s["secs"] += rec.get("secs") or 0
    s["calls"] += 1
    if "text" not in rec:
        s["errors"] += 1
        continue
    if task == "transcribe":
        w = wer(SPOKEN[clip], rec["text"])
        s["wer_sum"] += w
        s["wer_n"] += 1
        num = "12050" if clip == "speech_misread.mp3" else "1250"
        s["numbers_right"] += num in " ".join(words(rec["text"]))
        details[m][clip + "#t"] = f"{w:.2f}"
        continue
    fs = parse(rec["text"])
    if fs is None:
        s["unparsed"] += 1
        continue
    if clip in CLEAN:
        s["false_alarms"] += len([x for x in fs if x.get("confidence") in ("high", "medium")])
        continue
    if clip in GT:
        pat, a, b = GT[clip]
        hit = any(
            re.search(pat, str(x.get("pattern", "")), re.I)
            and (a is None or (float(x.get("start_s", -9)) <= b + 2 and float(x.get("end_s", -9)) >= a - 2))
            for x in fs
        )
        s["hits"] += hit
        # Located: some finding overlaps the planted range, whatever it is called.
        s["located"] += hit or (
            a is not None
            and any(float(x.get("start_s", -9)) <= b + 2 and float(x.get("end_s", -9)) >= a - 2 for x in fs)
        )
        details[m].setdefault(clip, []).append(hit)
        s["extra"] += len(fs) - hit

print(f"{'model':40s} hits/12 located/12 consistent/6 FA(clean) extra unparsed err WER num/3 cost$ secs/call")
for m, s in sorted(stats.items(), key=lambda kv: -kv[1]["hits"]):
    cons = sum(1 for c, v in details[m].items() if isinstance(v, list) and len(v) == 2 and v[0] == v[1])
    w = s["wer_sum"] / s["wer_n"] if s["wer_n"] else float("nan")
    print(
        f"{m:40s} {s['hits']:4d} {s['located']:6d} {cons:6d} {s['false_alarms']:8d} "
        f"{s['extra']:5d} {s['unparsed']:5d} "
        f"{s['errors']:3d} {w:5.2f} {s['numbers_right']:3d} {s['cost']:6.3f} "
        f"{s['secs'] / max(s['calls'], 1):6.1f}"
    )
for m in sorted(details):
    print(m, {k: v for k, v in details[m].items()})
