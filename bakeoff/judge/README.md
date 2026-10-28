# Vision-judge bakeoff (2026-10-27)

Which vision model should review generated game art for the defects in the
imagegen antipattern table? Ten models reviewed the same 26 images twice each,
with the same rubric ([`slopgate/judge.py`](../../slopgate/judge.py)), and the
results were scored against checked ground truth.

## The image set

[`labels.json`](labels.json) lists every image with the spec it was generated
from.

- **Planted defects (10).** Eight base images were generated with
  `google/gemini-3-pro-image`, and edits to them added one defect each: six digits on
  a hand, a finger through a mug handle, mismatched goggle lenses, hinges on both
  sides of a chest lid, garbled text on a blank sign, five lamps where the spec
  says four, a scarf melting into a staff, a floating robot, mismatched eyes.
  The tenth is a natural miss: the generator put the sun in front of the camera
  when the spec put it behind the viewer. Four edit attempts that didn't take
  (a six-finger robot hand, two tries at a shadow cast toward the lantern, the
  first mug attempt) were discarded.
- **Synthetic bases (7)** that were meant to be clean.
- **Real assets (9):** the seven 2026-09-07 bomber redesigns and two enemy
  references from Bomberguy9, each with its original generation prompt as the
  spec.

## Ground truth

Planted defects are known by construction. Everything else came from the
models: every blocking (image, category) pair any model reported, 43 in all,
was checked by looking at the image against its spec and marked true, false,
or disputed in [`adjudication.json`](adjudication.json). That check turned up
eight real defects nobody planted, including the "clean" ninja base being
cropped at the waist when the spec asks for full body, a swoosh-like logo on
Pip's sneakers, Riff's tail sticking out to the side, and a cast shadow under
the stalker where the spec forbids one.

Two cautions about that ground truth. The adjudication was done by Claude, the
same model family as three of the candidates; every call is in the file, so
overrule any you disagree with and re-run `score.py`. And the planted defects
came from a Gemini image model, which may favor the Gemini judge.

## Results

Both runs pooled. Recall counts a defect as found when the run raised a
blocking finding in one of its categories. Precision excludes disputed pairs.

| Model | Planted recall (20 defect-runs) | Unplanted recall (16 defect-runs) | Precision | False alarms on clean images | Same verdict both runs | Unusable replies | Cost / image | Median seconds |
|---|---|---|---|---|---|---|---|---|
| `google/gemini-4-argon` | **0.95** | 0.25 | 1.00 | 0% | 92% | 2% | $0.0057 | 16 |
| `openai/gpt-6.1-sol` | 0.85 | 0.25 | 1.00 | 0% | 88% | 0% | $0.0037 | 7 |
| `openai/gpt-6.1-terra` | 0.80 | 0.38 | 1.00 | 0% | 92% | 0% | $0.0023 | 5 |
| `anthropic/claude-opus-5.5` | 0.70 | **0.69** | 1.00 | 0% | 96% | 0% | $0.0215 | 10 |
| `qwen/qwen3.8-omni-flash` | 0.70 | 0.25 | 0.94 | 5% | 81% | 0% | $0.0019 | 43 |
| `x-ai/grok-4.7` | 0.60 | 0.31 | 0.90 | 10% | 88% | 0% | $0.0311 | 57 |
| `anthropic/claude-sonnet-5.5` | 0.60 | 0.13 | 1.00 | 0% | 96% | 0% | $0.0106 | 8 |
| `anthropic/claude-haiku-5.5` | 0.50 | 0.00 | 1.00 | 0% | 87% | 6% | $0.0006 | 5 |
| `openai/gpt-6-luna` | 0.45 | 0.31 | 0.95 | 0% | 92% | 0% | $0.0003 | 7 |
| `nvidia/nemotron-3.5-super-vl` | 0.30 | 0.13 | 1.00 | 0% | 100% | 42% | $0.0009 | 89 |

What the numbers say:

- **Every model caught the obvious ones**: mismatched lenses, garbled sign
  text, the wrong lamp count, the sun on the wrong side.
- **Anatomy and contact separate the field.** Only Gemini caught the mismatched
  eyes in both runs. Gemini and the two GPT-6.1 models caught the floating
  robot; Opus and Sonnet never did. Opus also missed the six-digit hand both
  times.
- **Spec conformance on real assets is where Opus leads.** It found the long
  rabbit ears (spec: short), Pip's logo, Riff's tail, the stalker's shadow, and
  the drifter's frontal view. Gemini found none of those five.
- **False claims were rare.** Only Grok (3), Qwen (2), and GPT-6 Luna (1) made
  blocking claims that turned out wrong. The models disagree far more about
  what they miss than about what they invent.
- **Nemotron returned empty replies 42% of the time** and took 89 seconds at
  the median. It isn't usable as configured.

### Two-model ensembles

Running two judges and keeping both sets of findings covers each model's blind
spots ([`results/analysis.json`](results/analysis.json)):

| Pair | Recall (36 defect-runs) | False claims | Cost / image |
|---|---|---|---|
| Opus 5.5 + Gemini 4 Argon | **0.92** | 0 | $0.027 |
| Opus 5.5 + GPT-6.1 Sol | 0.83 | 0 | $0.025 |
| Gemini 4 Argon + GPT-6.1 Terra | 0.78 | 0 | $0.008 |

## Recommendation

Run **`anthropic/claude-opus-5.5` and `google/gemini-4-argon` together** and
post the union of their findings, marking findings both models raised. Gemini
covers anatomy, contact, and counting; Opus covers spec conformance. At about
$0.03 per image, a PR that adds 20 images costs about $0.54.

If cost matters more than coverage, Gemini 4 Argon plus GPT-6.1 Terra catches
78% of defects for under a cent per image.

Keep it advisory, as planned. Even at temperature 0, models changed their
verdict on 4–12% of images between identical runs.

## Limits

- 26 images and 18 confirmed defects (36 defect-runs) is small. With 20 planted-defect runs, the
  gap between 0.95 and 0.80 is three runs, inside the noise.
- The categories are coarse. A `spec-mismatch` finding on a ninja image counts
  as finding the waist crop whether or not the model meant that.
- Four of the 13 edit attempts didn't produce the requested defect, so every
  planted image was checked by eye before use.

## Reproduce

```bash
export OPENROUTER_API_KEY=...
python bakeoff/judge/run.py              # resumes from results/raw
python bakeoff/judge/score.py            # writes results/summary.json
python bakeoff/judge/analyze.py          # per-defect table and ensembles
```

Spend for this run: $4.09 on judge calls (517 priced calls) and $2.76 on image
generation.
