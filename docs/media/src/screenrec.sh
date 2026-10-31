#!/usr/bin/env bash
# Screen-record one browser scene: Xvfb display, ffmpeg x11grab, headed Chromium.
#   screenrec.sh <scene> <outdir>     (env: PR, FIX_SHA)
set -euo pipefail
scene=$1; out=$2; disp=:${DISPLAY_NUM:-77}
mkdir -p "$out"
Xvfb "$disp" -screen 0 1280x900x24 -nolisten tcp & xpid=$!
sleep 1
ffmpeg -v error -y -f x11grab -draw_mouse 0 -framerate 30 -video_size 1280x900 -i "$disp" \
  -c:v libx264 -preset veryfast -crf 14 -pix_fmt yuv444p "$out/$scene.raw.mp4" & fpid=$!
start=$(date +%s.%N)
DISPLAY=$disp node "$(dirname "$0")/record_pr.js" "$scene" "$out"
sleep 0.5
kill -INT $fpid; wait $fpid || true
kill $xpid || true
python3 - "$out/$scene.marks.json" "$start" <<'PY'
import json, sys
p, start = sys.argv[1], float(sys.argv[2])
m = {k: round(v - start, 2) for k, v in json.load(open(p)).items()}
json.dump(m, open(p, "w")); print(m)
PY
