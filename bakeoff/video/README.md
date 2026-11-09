# Video-review bakeoff (2026-11-09)

How should slop-gate's advisory video review work: native video input, or
sampled frames sent to the vision models it already has? Twelve approaches got
the same 30 labeled clips, two runs each, temperature 0, with the same rubric
([`run.py`](run.py)). This PR doesn't wire anything into `slop-judge`, `judge.py`
or `listen.py`: Mike picks the models from these results, and a later PR builds
the job.

## The approaches

- **Native video (six models).** The MP4 as a `video_url` part: `google/gemini-4-argon`, `google/gemini-3.8-flash`,
  `qwen/qwen3.8-omni-flash`, `meta/muse-spark-open`, `xiaomi/mimo-v2.6-pro`,
  `perceptron/perceptron-mk1.5`. All six list audio input. Chosen across vendors
  and by cost, before any accuracy result: `moonshotai/kimi-k3.1-pro` cost eight
  times the next model in the probe, and `bytedance-seed/seed-2-1-turbo` hit the
  6,000-token limit with an empty reply in its one probe call, so both were left out.
- **Frame strips (three models, two rates).** Sampled frames as image parts,
  each preceded by its timestamp, no audio: the two image-judge models in
  `judge.models` (`anthropic/claude-opus-5.5`, `google/gemini-4-argon`) and one
  cheap vision model (`openai/gpt-6.1-terra`), each at 1 and 4 frames per second.
  Terra is the cheap model because it did better than GPT-6 Luna in the image
  bakeoff at about a tenth of Opus's image price.

Every approach reports findings as `start_s, end_s, pattern, evidence, impact,
confidence, basis, fix`, the shape of the video list's "Finding format". The
`basis` field is `frame inspection`, `listening` or `inference`.

## The test set

Thirty clips from [`make_clips.py`](make_clips.py), 8 to 10 s, 640x360, 24 fps,
about 10 MB in all. Clips aren't committed; [`clips.sha256`](clips.sha256) has
the SHA-256 of each file and the MD5 of its decoded frames. Two independent
builds were byte-identical. Everything is drawn with PIL/numpy or comes from
this public repo: the `docs/media/hero-tour.mp4` recording and the
`docs/media/before-after/speech-after.ogg` narration.

**13 planted defects** (one per clip, 26 defect-runs per approach at 2 runs),
**17 clean clips** (13 clean siblings with the defect left out, 4 extra
controls; 34 clean-runs). Filenames and labels are not sent to any model. The
build compares each defect render against its clean sibling and stops if the
picture differs outside the recipe's range.

| Defect clip | Pattern (the label) | What was planted | Label range (s) |
|---|---|---|---|
| `flicker` | Flicker or texture boil | A 340x160 patch of a static wall boils | 3.0-7.0 |
| `identity_drift` | Identity or background drift | Robot's color, eyes and antenna morph and stay changed | 4.0-6.0 |
| `object_vanish` | Objects appear, vanish, or merge | Ball goes behind a pillar and never comes out | 5.2-8.0 |
| `action_skip` | Action stalls, reverses, or skips | Car jumps 170 px between two frames | 3.75-4.25 |
| `action_reverse` | Action stalls, reverses, or skips | Train runs backward for 1.5 s | 4.0-5.5 |
| `cause_effect` | Broken cause and effect | Tower falls a second before the ball arrives | 3.8-5.0 |
| `text_mutate` | Unstable or unreadable overlays | A caption's letters change over a real screen recording | 4.0-6.5 |
| `sound_off` | Sound event misses picture | One of six bounces sounds 0.5 s late | 5.25-5.85 |
| `narration_mismatch` | Narration-picture mismatch | Narration says wave seven, 1,250 meters; picture shows wave 3, 400 m | 4.7-8.35 |
| `caption_mismatch` | Narration-picture mismatch | Caption says the door stays shut while it opens | 4.0-7.5 |
| `clip_seam` | Visible clip seams | Exposure, color and grain jump at 5.0 s | 4.75-5.25 |
| `brief_gap` | Objects appear, vanish, or merge | Star gone for 3 frames (125 ms), between both sampling rates' frames | 3.33-3.46 |
| `brief_dense` | Objects appear, vanish, or merge | Cube appears for 3 frames, on one 4 fps sample and between 1 fps samples | 4.25-4.38 |

Extra clean controls: stop-motion stepped timing, a deliberate jump cut,
handheld shake with film grain (each with a brief that says so), and an
untouched screen recording. **Lip-sync is not tested:** there is no public
footage here of a visible person speaking, and a drawn mouth would not be a
fair case, so it was left out rather than faked. The pattern stayed in the
rubric so models could still report it.

Nobody watched these clips at playback speed. Claude checked sampled frames of
the defect clips (contact sheets and the frames around the 3-frame plants) to
confirm the plants look as described.

## Scoring

[`score.py`](score.py) computes every number from the labels and the raw
replies. Claude did not judge any claim, and [`labels.json`](labels.json) was
written before any reply came back.

- **Hit:** a finding whose pattern matches the label (case and punctuation
  ignored) and whose time range overlaps the label range widened by 0.5 s on
  each side.
- **Net hit:** a hit that does not also fire, under the same rule, on the
  clip's clean sibling in the same run. **The ranking uses net hits.** This
  check was added after the first results, when hits turned up that fired
  identically on the clean sibling; for example, Gemini 4 Argon reported the
  sound-sync pattern over nearly the whole clip on both the defect and the
  clean sibling, in both runs. Raw hits are in the table too.
- **Defects and defect-runs are counted separately:** 13 defects, 26
  defect-runs per approach. "Recall by run" is net hits out of 26; "by defect"
  is how many of the 13 were found in at least one run or in both.
- **Claims on clean clips:** a run with any finding on a clean clip counts as
  flagged, whatever the finding says. It is mechanical, and it doesn't mean
  the claim was wrong or right; see below.

**Corrections made while running.** Three errors in the test or the prompt
were found and fixed; the replies they affect are kept in
[`results/superseded`](results/superseded) and counted in spend.

1. The clean `cause_effect` clip had the ball stop 8 px short of the tower, a
   real defect I had built into the clean clip. Models said so. Rebuilt and rerun.
2. The clean `text_mutate` caption box covered interface text in the
   recording, which is a real instance of the overlay pattern in the clean
   clip. Models said so. The recording was scaled down and the caption moved
   to its own bar. Rebuilt and rerun.
3. The frame-strip prompt left out "Narration-picture mismatch", the only
   pattern that can score the caption clip, so every strip approach scored 0
   on it by construction. The two caption clips were rerun for the strip
   approaches with that one pattern added.

## Results

Two runs per clip per approach. 95% Wilson intervals on net defect-runs are in
[`results/summary.json`](results/summary.json): 18 of 26 is 0.50 to 0.83 and 16
of 26 is 0.43 to 0.78, so the top ten rows overlap and **should not be read as a
ranking**. The per-call failure column is empty or unusable replies out of 60.

| Approach | Net defect-runs /26 | Picture-only net /22 | Defects found, either / both runs /13 | Raw hits /26 | Sibling hits | Clean runs flagged /34 | Median onset error s | Median IoU | Same outcome both runs | Failed/empty | Median s | $ / clip | $ / min |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `native:google/gemini-4-argon` | 18 | 16 | 9 / 9 | 22 | 4 | 5/34 | 0.5 | 0.63 | 29/30 | 0 | 10.2 | 0.0036 | 0.022 |
| `native:meta/muse-spark-open` | 18 | 18 | 9 / 9 | 18 | 0 | 7/34 | 0.17 | 0.6 | 29/30 | 0 | 10.7 | 0.0068 | 0.043 |
| `strip1:google/gemini-4-argon` | 18 | 18 | 9 / 9 | 18 | 0 | 5/34 | 0.2 | 0.64 | 29/30 | 0 | 14.0 | 0.0223 | 0.141 |
| `strip4:anthropic/claude-opus-5.5` | 18 | 18 | 9 / 9 | 20 | 2 | 10/34 | 0.0 | 0.79 | 30/30 | 0 | 9.0 | 0.0590 | 0.373 |
| `strip4:google/gemini-4-argon` | 18 | 18 | 9 / 9 | 18 | 0 | 3/34 | 0.02 | 0.81 | 29/30 | 0 | 15.8 | 0.0825 | 0.521 |
| `native:qwen/qwen3.8-omni-flash` | 17 | 15 | 9 / 8 | 17 | 0 | 13/34 | 0.5 | 0.56 | 26/30 | 0 | 44.8 | 0.0021 | 0.013 |
| `strip1:openai/gpt-6.1-terra` | 16 | 16 | 8 / 8 | 16 | 0 | 9/34 | 0.75 | 0.59 | 29/30 | 0 | 4.8 | 0.0028 | 0.018 |
| `native:google/gemini-3.8-flash` | 16 | 14 | 9 / 7 | 19 | 3 | 14/34 | 0.2 | 0.6 | 24/30 | 0 | 7.5 | 0.0047 | 0.030 |
| `strip4:openai/gpt-6.1-terra` | 16 | 16 | 9 / 7 | 16 | 0 | 8/34 | 0.0 | 0.86 | 28/30 | 0 | 6.7 | 0.0085 | 0.054 |
| `strip1:anthropic/claude-opus-5.5` | 16 | 16 | 8 / 8 | 18 | 2 | 7/34 | 0.1 | 0.59 | 29/30 | 0 | 6.7 | 0.0226 | 0.143 |
| `native:perceptron/perceptron-mk1.5` | 2 | 2 | 1 / 1 | 4 | 2 | 18/33 | 0.8 | 0.36 | 24/27 | 4 | 2.9 | 0.0005 | 0.003 |
| `native:xiaomi/mimo-v2.6-pro` | 0 | 0 | 0 / 0 | 0 | 0 | 10/16 | n/a | n/a | 15/16 | 23 | 79.1 | 0.0034 | 0.021 |

`$ / clip` is the mean cost per call over clips averaging 9.5 s. Frame-strip
picture-only numbers exclude the two sound-dependent defects, which they
can't hear; those two count as 0 in the /26 column.

Net hits per planted defect, out of 2 runs (the full table for all 12 is in
[`scores.txt`](scores.txt)). All 12 is out of 24 defect-runs across the 12
approaches.

| Defect | Pattern | native argon | native muse | native qwen omni | native 3.8-flash | strip1 argon | strip4 opus | All 12 |
|---|---|---|---|---|---|---|---|---|
| flicker | Flicker or texture boil | 2 | 2 | 1 | 2 | 2 | 2 | 19/24 |
| identity_drift | Identity or background drift | 2 | 2 | 2 | 2 | 2 | 2 | 20/24 |
| object_vanish | Objects appear, vanish, or merge | 2 | 2 | 2 | 2 | 2 | 2 | 21/24 |
| action_skip | Action stalls, reverses, or skips | 2 | 2 | 2 | 2 | 2 | 2 | 20/24 |
| action_reverse | Action stalls, reverses, or skips | 2 | 2 | 2 | 1 | 2 | 2 | 19/24 |
| cause_effect | Broken cause and effect | 0 | 2 | 2 | 0 | 2 | 2 | 16/24 |
| text_mutate | Unstable or unreadable overlays | 2 | 2 | 2 | 2 | 2 | 2 | 20/24 |
| sound_off | Sound event misses picture | 0 | 0 | 0 | 0 | 0 | 0 | 0/24 |
| narration_mismatch | Narration-picture mismatch | 2 | 0 | 2 | 2 | 0 | 0 | 6/24 |
| caption_mismatch | Narration-picture mismatch | 2 | 2 | 2 | 2 | 2 | 0 | 16/24 |
| clip_seam | Visible clip seams | 2 | 2 | 0 | 1 | 2 | 2 | 14/24 |
| brief_gap | Objects appear, vanish, or merge | 0 | 0 | 0 | 0 | 0 | 0 | 0/24 |
| brief_dense | Objects appear, vanish, or merge | 0 | 0 | 0 | 0 | 0 | 2 | 2/24 |

What the numbers say:

- **Native video matches frame strips on picture defects at a fraction of the
  cost.** The best native rows (Argon, Muse) tie the best strip rows at 18 of
  26, at $0.004 to $0.007 per clip against $0.022 to $0.083 for the strips
  that tie. The cheap strips (Terra) cost about the same as native but landed
  on 16 of 26 and can't hear.
- **Dense sampling bought one defect.** Going from 1 to 4 fps cost 2.6 to 3.7 times
  as much; the only gain is that strip4 Opus caught `brief_dense`, which by
  construction sits on a sampled 4 fps frame, and the other two strip4 models
  missed it even with that frame in front of them. Nothing caught `brief_gap`,
  and no native model caught either brief defect. Short glitches stay out of
  reach, as the video list warns.
- **Nothing found the off-beat bounce.** `sound_off` is 0 of 24 defect-runs.
  Only Argon hit it raw, and it reported the same thing on the clean sibling:
  no model showed it can judge sound timing against the picture.
- **Narration mismatch needs hearing.** Argon, Qwen and Gemini 3.8 Flash caught
  it in both runs; Muse did not, and no strip can.
- **Perceptron and MiMo are not usable as configured.** Perceptron made claims
  on 18 of 33 clean runs. MiMo named the right pattern in 15 of 26 defect-runs
  but its timestamps ran about three times too long (a 9 s clip described to
  30 s), so no finding overlapped its range; 23 of its 60 replies were empty
  at the 6,000-token limit.
- **False claims are common.** Depending on the approach, 3 to 18 of its
  clean-runs were flagged. Argon and Muse flag 15 to 21% of clean clips. Those
  include fair complaints about simple drawn clips (the probe's Implausible
  physics on the bouncing ball) as well as false ones. The scoring doesn't
  decide which is which.

Time-range accuracy looks good for 4 fps strips (median onset error at or
near 0) partly because every strip finding snaps to a sampled timestamp.
Native models start a median 0.17 to 0.5 s off on the hits they do get, on
labels 0.5 to 4 s long. MiMo's timeline is the exception: it put the
`flicker` plant, 3 to 7 s in the clip, at 10 to 21 s.

## Sample size and what this can't support

30 clips, 13 defects, 2 runs each: a gap of a few defect-runs is noise. Ten
approaches sit between 16 and 18 of 26. Don't read order among them. Only the
two weakest rows are clearly worse. Runs are not independent draws: agreement
between runs was 80 to 100%, so 26 runs is closer to 13 trials.

Every clip is a short, simple, drawn scene or a screen recording, with one
large defect, and the brief describes what should happen. Generated footage
has subtler, more varied faults, and nothing here measures them. The clean
clips are scored as clean by construction, and models do flag real weaknesses
in cartoon physics, so the clean-flagged numbers mix false alarms with fair
comments; nothing in this bakeoff separates the two (see below).

## Unadjudicated claims

[`results/unadjudicated.json`](results/unadjudicated.json) lists every finding
not matched to a planted label: 245 from the scored runs, on defect clips and
clean clips. **None was judged, and none enters a metric** except that a run
with any finding on a clean clip counts as flagged. Those claims were not
adjudicated because Claude is in the same model family as some candidates. Mike
can read and overrule any of them.

## Realism sample: not scored

Three 4-second clips generated with `google/veo-3.1-lite` (marble and coins,
person on a rainy street, kettle pour; prompts and hashes in
[`results/realism_generation.json`](results/realism_generation.json), replies
in [`results/realism`](results/realism)) were reviewed by native Argon, native
Muse and strip4 Opus, twice each. These have no labels and no ground truth, so
they are unscored and listed for reading only. The generated clips are about
5 MB and aren't committed. Argon flagged the marble and kettle clips in both
runs and the street clip in neither; Muse flagged only the marble; Opus flagged
the marble and kettle in both runs and the street clip once. No one has checked
whether any of those claims is true.

## Spend

$17.11 in total, from each response's `usage.cost`; the OpenRouter key's usage
counter moved by the same amount over the run. That is:

- $13.13 on the scored run
- $2.64 on superseded replies from the three corrections above
- $0.32 on the probe that sized the run
- $0.66 reviewing the realism clips
- $0.36 generating them

The hard cap was $20; the run stopped well under it.

## Reproduce

```bash
python bakeoff/video/make_clips.py /tmp/clips   # and compare with clips.sha256
export OPENROUTER_API_KEY=...
python bakeoff/video/run.py /tmp/clips native:google/gemini-4-argon strip4:anthropic/claude-opus-5.5
python bakeoff/video/score.py
```

Replies are cached in `results/raw`; a re-run only fills gaps.

## Recommendation

Use **native video with `google/gemini-4-argon`, one call per clip**, advisory
and labeled as model inference. About $0.0036 per 9.5-second clip, or roughly
$0.022 per minute of video: about half the cost of Muse, and a sixth to a
twentieth of the strips that tied it. It hears the track, so narration mismatch
is covered, and it was tied for the top score with no failed replies. Every finding must
carry its `basis` and must never claim playback; the job should mark temporal
review as pending, as the list requires. This is worth running as a pointer for
a person: at about two cents a minute it costs nothing to look. It is not good
enough to decide anything alone: in this set it missed every brief defect, did
not distinguish real sync errors from none, and flagged 5 of 34 clean runs.

Don't add frame strips by default. The ones that tied Argon cost 6 to 20
times more, none of them can hear, and the only defect dense sampling
added was a 3-frame one placed on a sampled frame.

**Strongest reason to pick otherwise:** this set is drawn cartoons with single
large defects, and Argon is tied, not ahead. If the footage you care about is
generated picture-only video (hero loops, no narration), `meta/muse-spark-open`
reached the same 18 net with 18 of 22 on picture alone, including
`cause_effect`, where Argon's only hits also fired on the clean sibling. Choose
Muse then, or run Argon and Muse together for about $0.010 per clip, and accept
that the pair flags more clean clips. The gap between them is two runs out of
22; neither pick is proven against the other.
