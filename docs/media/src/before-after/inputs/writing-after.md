## Why sfxpack?

A folder of raw WAV effects needs three passes before it goes into a game:
convert each file to the format Unity or Godot expects, bring every file to
the same loudness, and cut the silence at the start and end. sfxpack runs all
three over the whole folder in one command, with the loudness target set in
LUFS, so a new batch of effects doesn't mean opening each file in an audio
editor.
