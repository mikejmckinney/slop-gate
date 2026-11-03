#!/usr/bin/env bash
# Rebuild the bakeoff clips. Audio isn't committed: the music comes from a
# game soundtrack that isn't public, and the speech was generated for the test.
#   make_clips.sh <music track A> <music track B> <out dir>   (env: ELEVENLABS_API_KEY)
set -euo pipefail
a=$1; b=$2; out=$3
here="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$out"
cd "$out"

# Music: 30 s of track A, then one planted defect per clip.
ffmpeg -v error -y -ss 40 -t 30 -i "$a" -ar 44100 -ac 2 src30.wav
ffmpeg -v error -y -i src30.wav music_clean.mp3
ffmpeg -v error -y -i src30.wav -af "vibrato=f=5.5:d=0.7:enable='between(t,10,18)'" music_warble.mp3
ffmpeg -v error -y -i src30.wav -ss 8 -t 8 -c:a libopus -b:a 6k seg.opus
ffmpeg -v error -y -i seg.opus -ar 44100 -ac 2 seg.wav
ffmpeg -v error -y -i src30.wav -i seg.wav -filter_complex \
  "[0]atrim=0:8[x];[0]atrim=16,asetpts=PTS-STARTPTS[z];[x][1][z]concat=n=3:v=0:a=1" music_metallic.mp3
ffmpeg -v error -y -i src30.wav -filter_complex \
  "[0]atrim=0:12[x];[0]atrim=12:22,asetpts=PTS-STARTPTS,aecho=0.8:0.9:500|900|1300:0.6|0.5|0.4,atrim=0:10[y];[0]atrim=22,asetpts=PTS-STARTPTS[z];[x][y][z]concat=n=3:v=0:a=1" \
  music_wash.mp3
ffmpeg -v error -y -ss 6 -t 3.08 -i src30.wav chunk.wav
ffmpeg -v error -y -stream_loop 9 -i chunk.wav -t 30 music_repeat.mp3
ffmpeg -v error -y -ss 60 -t 15 -i "$b" -ar 44100 -ac 2 other.wav
ffmpeg -v error -y -i src30.wav -i other.wav -filter_complex "[0]atrim=0:15[x];[x][1]concat=n=2:v=0:a=1" music_splice.mp3

# Speech: ElevenLabs eleven_v4. The misread clip says 12,050 for 1,250 and drops "then";
# the drift clip switches from Sarah to George halfway.
cp "$here/script.txt" script.txt
python3 - <<'PY'
import json, os, urllib.request
key = os.environ["ELEVENLABS_API_KEY"]
script = open("script.txt").read()

def tts(text, voice, out):
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=mp3_44100_128",
        data=json.dumps({"text": text, "model_id": "eleven_v4"}).encode(),
        headers={"xi-api-key": key, "Content-Type": "application/json"},
    )
    open(out, "wb").write(urllib.request.urlopen(req, timeout=120).read())

sarah, george = "EXAVITQu4vr4xnSDxMaL", "JBFqnCBsd6RMkjVDRZzb"
tts(script, sarah, "speech_clean.mp3")
tts(script.replace("1,250", "12,050").replace("then release", "release"), sarah, "speech_misread.mp3")
first, second = script.split(" Wave seven", 1)
tts(first, sarah, "drift_a.mp3")
tts("Wave seven" + second, george, "drift_b.mp3")
PY
ffmpeg -v error -y -i drift_a.mp3 -i drift_b.mp3 -filter_complex "[0][1]concat=n=2:v=0:a=1" speech_drift.mp3
