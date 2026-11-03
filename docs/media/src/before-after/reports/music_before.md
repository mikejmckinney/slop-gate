<!-- slop-gate-report -->
## slop-gate: ❌ Blocked

3 blocking, 0 advisory. Checked 1 media files at `a0a3f89`.

Errors block the merge; warnings are advice. Fix the errors, push, and this report updates. To accept a finding on purpose, see [suppressing findings](https://github.com/mikejmckinney/slop-gate#suppressing-a-finding).

### media (3 blocking, 0 advisory)

_Image and audio checks against the media manifest in .slop-gate.yml._

| | Location | Rule | Problem and fix |
|---|---|---|---|
| 🛑 | `assets/music/arena.ogg` | `true-peak` | True peak +0.9 dBTP exceeds -1.0 dBTP; lossy encoding and resampling will clip it. **Fix:** Normalize with a true-peak limiter (ffmpeg loudnorm TP=-1). |
| 🛑 | `assets/music/arena.ogg` | `loudness` | Integrated loudness -10.9 LUFS; the manifest targets -16 ±2.0. **Fix:** Normalize with `ffmpeg -i in -af loudnorm=I=-16:TP=-1 out`. |
| 🛑 | `assets/music/arena.ogg` | `loop-gap` | Loop has 0 ms of silence at the start and 933 ms at the end, which plays as a gap every time it wraps. **Fix:** Trim to the musical loop points or crossfade the tail into the head before export. Prefer Ogg Vorbis/Opus over MP3 for loops; MP3 encoder padding adds a gap. |
