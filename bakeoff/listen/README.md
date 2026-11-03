# Listening-review bakeoff (2026-11-02)

Which audio model should listen to changed audio for the defects in the audio
antipattern list, and which should transcribe speech for the words check? Nine
models on OpenRouter that accept audio input got the same clips and prompt
(`run.py`, the same prompt as [`slopgate/listen.py`](../../slopgate/listen.py)),
two runs each, temperature 0.

## The clips

Built by [`make_clips.sh`](make_clips.sh). The audio isn't committed: the music
is from a game soundtrack that isn't public, and the speech was generated for
the test.

| Clip | What was done to it | Expected finding |
|---|---|---|
| `music_clean` | 30 s of a Mureka track, untouched | none |
| `music_warble` | 5.5 Hz vibrato, 0.7 depth, 10–18 s | pitch wobble, 10–18 s |
| `music_metallic` | 8–16 s round-tripped through Opus at 6 kb/s | metallic or watery, 8–16 s |
| `music_wash` | 12–22 s through a long multi-tap echo | reverb wash, 12–22 s |
| `music_repeat` | one 3 s bar looped for 30 s | repetition without purpose |
| `music_splice` | 15 s of this track, then 15 s of another | disconnected sections at 15 s |
| `speech_clean` | ElevenLabs `eleven_v4`, one voice, the [script](script.txt) | none |
| `speech_misread` | the script with 1,250 read as 12,050 and "then" dropped | transcript differences |
| `speech_drift` | first half one voice, second half another | voice drift near 8 s |

## Results

From [`scores.txt`](scores.txt). *Hits* name the right pattern over the right
range; *located* is any finding over the planted range, whatever it is
called. *FA* counts medium or high findings on the two clean clips. *WER* is
word error rate on the three speech clips, and *num* counts transcripts that
got the number right.

| Model | Hits /12 | Located /12 | Same both runs /6 | FA | WER | Num /3 | Cost |
|---|---|---|---|---|---|---|---|
| google/gemini-3.1-pro-preview | 7 | 9 | 4 | 0 | 0.08 | 3 | $0.24 |
| google/gemini-4-argon | 5 | 8 | 5 | 0 | 0.12 | 3 | $0.05 |
| meta/muse-wonder | 4 | 4 | 6 | 0 | 0.36 | 2 | $0.51 |
| openai/gpt-audio | 2 | 6 | 3 | 9 | 0.05 | 2 | $0.21 |
| qwen/qwen3.8-omni-flash | 2 | 5 | 6 | 2 | 0.04 | 3 | $0.02 |
| google/gemini-3.8-flash | 1 | 1 | 4 | 2 | 0.07 | 3 | $0.07 |
| xiaomi/mimo-v2.6-pro | 0 | 1 | 4 | 2 | 0.04 | 3 | $0.01 |
| mistralai/voxtral-small-24b-2507 | 0 | 8 | 5 | 2 | 0.09 | 0 | $0.05 |
| thinkingmachines/inkling | 0 | 0 | 6 | 0 | 0.00 | 3 | $0.04 |

Cost is for all 21 calls per model.

What the table hides:

- **Gemini 3.1 Pro and Gemini 4 Argon find different things.** Pro caught the
  warble, the codec damage and the splice in both runs; Argon caught the loop
  and the voice change in both runs. Together they placed all six planted
  defects, and neither made a medium or high finding on a clean clip.
- **Nobody named the echo.** Gemini 3.1 Pro put a finding on exactly 12–18 s
  but called it "instrument morphing or smear". The report shows what the model
  heard next to the pattern name, so a reader can still tell.
- **Voxtral's located score is an artifact.** It reported the same click at
  12.5 s on nearly every clip, which happens to overlap most planted ranges.
- **GPT Audio** sometimes answered as if it had no audio, and made nine
  findings on the clean clips.
- **Inkling** rejects MP3 and accepts 16 kHz WAV. It found nothing in review
  but transcribed all three speech clips word for word.

## Choice

- Review: `google/gemini-3.1-pro-preview` and `google/gemini-4-argon`, merged
  into one table. About $0.014 per 30 s clip for the pair.
- Transcript: `thinkingmachines/inkling` with 16 kHz mono WAV. The diff against
  the expected words is done in code, so a misread number shows up as a word
  difference, not as a model's opinion.

Override both in `.slop-gate.yml` under `judge.audio_models` and
`judge.transcribe_model`.

## Follow-up: three TTS engines on a line with written forms

On 2026-11-02 ElevenLabs `eleven_v4` (voice Sarah), `google/gemini-3.8-flash-tts`
(Kore) and `heygen/heygen-voice-1` (nadine) each read
[`tts/line.txt`](tts/line.txt), which has a unit abbreviation, a hyphenated
code, a time and a percentage. Inkling transcribed each file twice
([`tts/transcripts.json`](tts/transcripts.json)).

All six transcripts match what was meant: no engine misread anything. The
first version of the diff still reported two differences every time: "m"
against "meters", and "B-12" against "B12". The diff now treats a code that
mixes letters and digits as the same with or without a separator, and expands
a short list of unit abbreviations (`SPOKEN_UNITS` in `slopgate/listen.py`).
All six come back clean, while "m" heard as "miles", "now here" heard as
"nowhere" and "12 50" heard as "1250" are still reported.

## Limits

Nine clips from one music track and one script is a small set. It shows which
models can hear planted damage at all; it doesn't measure how often they miss
subtler, natural defects. Gemini 3.1 Pro is a preview model and may be renamed
or withdrawn. Re-run the bakeoff with your own clips before trusting the
review on a different kind of audio.
