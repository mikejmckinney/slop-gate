# Before-and-after examples

Where each example in the README's "Before and after" section came from. Every
"before" is a model's output as returned; every report in [`reports`](reports)
is slop-gate's own output on a scratch repo that committed the file with the
manifest shown in the README. The scripts here made the "after" files by
following the reports' fix text; slop-gate itself changes nothing. All of it
was made on 2026-11-02.

| Example | Before | Fix | Reports |
|---|---|---|---|
| Writing | `google/gemini-2.5-flash` on OpenRouter, temperature 0.7, prompt in [`inputs/writing-prompt.txt`](inputs/writing-prompt.txt); output in [`inputs/writing-before.md`](inputs/writing-before.md) | Rewritten by hand: [`inputs/writing-after.md`](inputs/writing-after.md) | [`writing_before.md`](reports/writing_before.md), [`writing_after.md`](reports/writing_after.md) |
| Pixel sprite | Five image models on OpenRouter, one prompt (in the README). Only 200 px previews are committed for four models; their originals were 1024 or 2048 px. The `gpt-image-2.5-sunburst` original is committed in full at [`inputs/sprite-sunburst-original.png`](inputs/sprite-sunburst-original.png). It is a later generation from the same model and prompt, and replaced the first one because it looks better | [`fix_sprite.py`](fix_sprite.py) on the `gpt-image-2.5-sunburst` output, the one with real transparency: [`inputs/sprite-after.png`](inputs/sprite-after.png) | [`pixel_before.md`](reports/pixel_before.md), [`pixel_after.md`](reports/pixel_after.md) |
| Vision review | The lamp images from the vision bakeoff, [`bakeoff/judge/images`](../../../../bakeoff/judge/images) | None: the four-lamp image is the bakeoff's clean twin | [`bakeoff/judge/results/raw`](../../../../bakeoff/judge/results/raw) |
| Music loop | [`generate_music.py`](generate_music.py): Mureka `mureka-9.5` instrumental and Suno (kie.ai, returned `chirp-hawk`), two takes each, brief in [`inputs/music-brief.txt`](inputs/music-brief.txt) | [`make_loop.py`](make_loop.py) on Mureka take A's FLAC | [`music_before.md`](reports/music_before.md), [`music_after.md`](reports/music_after.md) |
| Spoken line | ElevenLabs `eleven_v4`, voice Sarah, reading a script with one number changed and one word dropped (planted for the listening bakeoff) | The same voice reading the real script | [`speech_before.md`](reports/speech_before.md); the after review is quoted in the README. `docs/media/before-after/speech-before.ogg` and `speech-after.ogg` are the middle two sentences of each 17-second take (5.0–11.35 s and 4.95–12.5 s), cut at the pauses and re-encoded as Ogg Vorbis |
| MIDI | Basic Pitch 0.4.0 on the music loop: [`inputs/midi-before.mid`](inputs/midi-before.mid) | [`merge_restrikes.py`](merge_restrikes.py): [`inputs/midi-after.mid`](inputs/midi-after.mid) | Quoted in the README |

[`build.py`](build.py) turns those files into the README media in
[`docs/media/before-after`](../../before-after): the sprite previews, the
looping waveform (`loop-wrap.webp`, and `loop-wrap.mp4` with sound), the
12-second wrap clips and the piano roll. GitHub plays video in a README only
from its attachment store, so `loop-wrap.mp4` is also uploaded there, in a
comment on [issue #21](https://github.com/mikejmckinney/slop-gate/issues/21).
After re-recording, upload the new file in a new comment there and update the
URL in the README.

The scripts need Python 3.13 with numpy, Pillow, mido, librosa 1.0 and
soundfile, plus ffmpeg with libvorbis and libx264. `generate_music.py` reads
`KIE_API_KEY` and `MUREKA_API_KEY`.
