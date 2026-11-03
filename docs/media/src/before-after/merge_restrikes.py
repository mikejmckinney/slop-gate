"""Merge transcription fragments of one held note, keeping a split only where the audio has an onset.

A fragment is merged into the note before it when it is the same pitch on the same channel, starts
within a 32nd note of that note's end, and the audio shows no new attack at that pitch: energy in the
note's own constant-Q bin rises by less than 3 dB from the 40 ms before the fragment to the 40 ms after.
A drum hit elsewhere in the mix doesn't count as an attack of the held note.
"""

import sys

import librosa
import mido
import numpy as np

mid_path, wav_path, out_path = sys.argv[1:4]
y, sr = librosa.load(wav_path, sr=22050, mono=True)
HOP, FMIN = 256, librosa.midi_to_hz(24)
cqt_db = librosa.amplitude_to_db(
    np.abs(librosa.cqt(y, sr=sr, hop_length=HOP, fmin=FMIN, n_bins=84)), ref=np.max
)


def attacked(pitch, t):
    b = pitch - 24
    if not 0 <= b < cqt_db.shape[0]:
        return True
    f, w = int(t * sr / HOP), max(1, int(0.040 * sr / HOP))
    before, after = cqt_db[b, max(0, f - w) : f], cqt_db[b, f : f + w]
    return len(before) and len(after) and after.max() - before.mean() >= 3.0


m = mido.MidiFile(mid_path)
tempo = next((msg.tempo for t in m.tracks for msg in t if msg.type == "set_tempo"), 500000)


def sec(tick):
    return mido.tick2second(tick, m.ticks_per_beat, tempo)


merged = kept = 0
for ti, track in enumerate(m.tracks):
    notes, open_, t = [], {}, 0
    others = []
    for msg in track:
        t += msg.time
        if msg.type == "note_on" and msg.velocity > 0:
            open_[(msg.channel, msg.note)] = (t, msg.velocity)
        elif msg.type in ("note_off", "note_on") and (msg.channel, msg.note) in open_:
            s, v = open_.pop((msg.channel, msg.note))
            notes.append([s, t, msg.channel, msg.note, v])
        elif msg.type not in ("note_on", "note_off"):
            others.append((t, msg))
    if not notes:
        continue
    notes.sort()
    out = []
    last = {}
    for n in notes:
        key = (n[2], n[3])
        prev = last.get(key)
        if prev is not None and 0 <= n[0] - prev[1] <= m.ticks_per_beat // 8:
            if not attacked(n[3], sec(n[0])):
                prev[1] = max(prev[1], n[1])
                merged += 1
                continue
            kept += 1
        out.append(n)
        last[key] = n
    events = [(s, mido.Message("note_on", channel=c, note=p, velocity=v)) for s, e, c, p, v in out]
    events += [(e, mido.Message("note_off", channel=c, note=p, velocity=0)) for s, e, c, p, v in out]
    events += others
    events.sort(key=lambda ev: (ev[0], ev[1].type != "note_off"))
    new, now = mido.MidiTrack(), 0
    for at, msg in events:
        if msg.type == "end_of_track":
            continue
        new.append(msg.copy(time=at - now))
        now = at
    new.append(mido.MetaMessage("end_of_track", time=0))
    m.tracks[ti] = new
m.save(out_path)
print(f"merged {merged} fragments; kept {kept} splits where the pitch is re-attacked in the audio")
