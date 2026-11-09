# Video review on real generated footage (GeneVA, 2026-11-09)

Round two of the [video-review bakeoff](../README.md). Round one used drawn clips
with one planted defect each, so it couldn't say how a reviewer does on real
generated video. This round uses 90 clips from
[GeneVA](https://kenchen10.github.io/projects/geneva/index.html), a set of
16,356 videos from Sora, Pika and VideoCrafter2 with human artifact annotations.
Nothing here is wired into `slop-judge`, `judge.py` or `listen.py`.

## Result

Native `google/gemini-4-argon` was the only approach whose interval for telling
annotated clips from unannotated ones stayed above zero, and it cost $0.0027 per
clip. It tied Opus strips on locating artifacts at about an eleventh of the
price. A flag from it says little by itself: it flagged 87% of annotated clips
and 63% of unannotated ones.

| Approach | Discrimination (95%) | Annotated flagged | Unannotated flagged | Category + time + box match | Shuffled baseline | $ / clip | $ / min |
|---|---|---|---|---|---|---|---|
| native `google/gemini-4-argon` | 0.233 (0.033 to 0.433) | 52/60 | 19/30 | 24/60 | 0.169 | 0.0027 | 0.05 |
| native `meta/muse-spark-open` | 0.000 (-0.217 to 0.217) | 26/60 | 13/30 | 9/60 | 0.098 | 0.0060 | 0.11 |
| strips `openai/gpt-6.1-terra` | -0.050 (-0.217 to 0.100) | 7/60 | 5/30 | 1/60 | 0.009 | 0.0058 | 0.11 |
| strips `openai/gpt-6.1-sol` | 0.117 (0.000 to 0.217) | 9/60 | 1/30 | 3/60 | 0.019 | 0.0109 | 0.21 |
| strips `anthropic/claude-opus-5.5` | 0.100 (-0.033 to 0.250) | 56/60 | 25/30 | 23/60 | 0.232 | 0.0308 | 0.58 |

Discrimination is the share of annotated clips flagged minus the share of
unannotated clips flagged. A model that flags everything scores 0. The shuffled
baseline is the same findings scored against another annotated clip from the
same generator, as a share of clips, so 0.169 is about 10 of 60. All 450 replies
parsed; none failed. Per-generator numbers and the category-only, time-only and
box-only matches are in [`scores.txt`](scores.txt).

The intervals overlap, so don't read the table as a ranking beyond three things:

- **Argon and Opus find and place things; the rest mostly don't.** Argon and Opus
  matched category, time and box on 24 and 23 of 60 clips, against about 10 and
  14 by chance. Muse reached 9.
- **The GPT strips were nearly silent.** Terra and Sol flagged 7 and 9 of 60
  annotated clips. Opus got the same 640 px strips and flagged 56, so strip
  resolution alone doesn't explain it. Sol's 1 of 30 on unannotated clips is the
  lowest flag rate, at the cost of missing most annotated ones.
- **Opus flags nearly everything.** 56 of 60 annotated and 25 of 30 unannotated,
  so its flag carries little signal and its box matches beat chance by less.

## Method

Five approaches, one run per clip, temperature 0 where the model allows,
[`review.py`](review.py) with one prompt. The two native models get the MP4 as a
`video_url` part. Terra, Sol and Opus get frames at 4 per second, long side 640
px, each preceded by its timestamp, because all three return HTTP 404 for video
through OpenRouter ([`results/refusal`](results/refusal)). OpenAI's model page
lists video as not supported.

`gpt-6-astra` was dropped on cost, so its absence is not a quality result. Three
probe clips cost $0.1743, which projected to $10.46 for two runs, half the
budget. OpenAI billed 6,265 of its 6,268 prompt tokens as cache writes at $12.50
per million, against Opus's $4 uncached input.

[`pick_clips.py`](pick_clips.py) drew the sample with a fixed seed, and
[`selection.json`](selection.json) was committed before any scored reply came
back: 60 annotated clips and 30 with an empty annotation and a video rating of 6
or 7 out of 7, split evenly across the three generators. It drops 9 rows with no
annotation field and 2 rows for a duplicated video. The labels file has no text
prompts, so "Semantic Mismatch with Text" can't be judged: 639 videos whose only
annotations are that category were excluded and models were told no brief exists.

Models report GeneVA's categories (Shape/Form/Geometry, Visual Artifacts, Motion,
Physics, Other), a time range and a box. [`score_geneva.py`](score_geneva.py)
scores everything from the labels in code, and no Claude model judged a claim,
because Opus is a candidate. A finding matches on category if the category is any
of the annotation's, on time if ranges overlap within 0.5 s, and on box at IoU 0.1
or more against a box in that window. Intervals are clip bootstraps. A missing,
failed or malformed reply is counted as that and never read as "no findings".

## Limits

- **Single run.** There is no run-to-run agreement number, and the intervals
  understate how much a model varies between runs.
- **An unannotated clip isn't proven clean.** Each video has one annotator, so
  "unannotated flagged" is a flag rate and not a false-alarm rate, and a finding
  that misses the annotation may still be real. None of the unmatched findings
  was judged.
- **Small sample.** 60 and 30 clips, 288 seconds in total.
- **Older generators.** Sora, Pika 1 and VideoCrafter2 predate the models in
  Mike's instructions, and their faults may be larger than current ones.
- **Picture only.** No sampled clip has audio, so hearing wasn't tested.
- **Unequal input.** Native models get the full file (up to 1280x720) and strip
  models get 640 px frames, so native and strip rows differ in resolution too.
- **Generator leak.** Pika clips carry a visible "PIKA LABS" watermark. The prompt
  says a watermark is not a defect, but the frames still reveal the generator.
- **Category names only.** The GeneVA paper names its categories without defining
  them, so the prompt gives names alone. Annotators' category use may differ.
- **Timing is noisy.** The median annotated span is 0.4 s, and many marks look
  like single moments, while the prompt asks for faults that hold across frames.
- **No playback.** Nobody watched these clips at playback speed for this round.

## Spend

$5.41 in total from each response's `usage.cost`, against a $20 cap: $5.06 on the
scored run (Argon $0.24, Muse $0.54, Terra $0.52, Sol $0.98, Opus $2.78) and $0.35
on the probe. The first attempt was killed when its cloud environment shut down
after 207 replies; saved replies are kept, so the rerun made only the other 243 calls.

## Reproduce

```bash
python3 -I bakeoff/video/geneva/pick_clips.py   # see its docstring for the arguments
export OPENROUTER_API_KEY=...
python3 -I bakeoff/video/geneva/review.py CLIP_DIR native:google/gemini-4-argon
python3 -I bakeoff/video/geneva/score_geneva.py LABELS.json
```

Clips, annotations and prompts aren't committed. `selection.json` has the video
IDs and hashes, and `pick_clips.py` checks the SHA-256 of GeneVA's labels file.

## Recommendation

If video review is built, use **native `google/gemini-4-argon`, one call per clip,
advisory only**, and show its time ranges and boxes to a person. It is the only row
with discrimination clearly above zero, it matched Opus on location at about an
eleventh of the cost, and round one reached the same pick.

**Strongest objection:** the separation is weak. An 87% against 63% flag rate means
"flagged" alone tells a reviewer little, and 24 of 60 full matches is 40%, on older
generators. On this evidence the value is in pointing at places to look, not in a
pass or fail. It should never block a PR, and not building it yet is a fair call.
