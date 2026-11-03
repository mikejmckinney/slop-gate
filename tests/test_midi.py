"""MIDI checks, each with a clean twin that must stay quiet."""

import shutil
import tempfile
import unittest
from pathlib import Path

import mido

from slopgate.midi import check_midi

TPB = 480


def track(*events, program=0, channel=0):
    """events: (start tick, end tick, pitch) for notes, or ("tempo", tick, bpm)."""
    msgs = [(0, mido.Message("program_change", channel=channel, program=program))]
    for e in events:
        if e[0] == "tempo":
            msgs.append((e[1], mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(e[2]))))
            continue
        start, end, pitch = e
        msgs.append((start, mido.Message("note_on", channel=channel, note=pitch, velocity=90)))
        if end is not None:
            msgs.append((end, mido.Message("note_off", channel=channel, note=pitch, velocity=0)))
    msgs.sort(key=lambda m: (m[0], m[1].type == "note_on"))  # note-offs first on a shared tick
    t = mido.MidiTrack()
    now = 0
    for tick, msg in msgs:
        t.append(msg.copy(time=tick - now))
        now = tick
    return t


class MidiTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)

    def rules(self, *tracks, name="song.mid"):
        mf = mido.MidiFile(ticks_per_beat=TPB)
        mf.tracks.extend(tracks)
        path = self.dir / name
        mf.save(path)
        return {f.rule for f in check_midi(path, name)}

    def sixteenths(self, n, pitch=45, start=0):
        step = TPB // 4
        return [(start + i * step, start + i * step + step - 20, pitch) for i in range(n)]

    def test_clean_grid_bassline_passes(self):
        # Repeated sixteenths on the grid are a pulse, not a chopped note.
        self.assertEqual(self.rules(track(*self.sixteenths(32))), set())

    def test_restruck_note(self):
        cuts = [0, 173, 401, 590, 811, 1033]  # back to back, off the grid
        frags = [(a, b, 57) for a, b in zip(cuts, cuts[1:], strict=False)]
        self.assertIn("restruck-note", self.rules(track(*frags)))

    def test_duplicate_part_on_same_instrument(self):
        part = self.sixteenths(8, pitch=64)
        self.assertIn("duplicate-notes", self.rules(track(*part, program=81), track(*part, program=81)))
        # Doubling with another instrument is arrangement, not a duplicate.
        self.assertNotIn(
            "duplicate-notes",
            self.rules(track(*part, program=81), track(*part, program=48, channel=1)),
        )

    def test_overlapping_and_zero_length_notes(self):
        t = track((0, 960, 60), (480, 1200, 60))
        t.append(mido.Message("note_on", note=62, velocity=90, time=240))
        t.append(mido.Message("note_off", note=62, velocity=0, time=0))  # ends on the tick it starts
        found = self.rules(t)
        self.assertTrue({"overlapping-note", "zero-length-note"} <= found, found)

    def test_hanging_note(self):
        self.assertIn("hanging-note", self.rules(track((0, None, 60), (480, 960, 64))))

    def test_tempo_jitter(self):
        jitter = [("tempo", i * TPB, 120 + (0.4 if i % 2 else -0.3)) for i in range(12)]
        self.assertIn("tempo-jitter", self.rules(track(*jitter, (0, 480, 60))))
        steps = [("tempo", 0, 120), ("tempo", TPB * 16, 90), ("tempo", TPB * 32, 140)]
        self.assertNotIn("tempo-jitter", self.rules(track(*steps, (0, 480, 60))))

    def test_semitone_clash(self):
        held = track((0, TPB * 4, 59), channel=0)
        self.assertIn("semitone-clash", self.rules(held, track((0, TPB * 2, 60), channel=1)))
        # A major seventh is a chord color, not a clash.
        self.assertNotIn("semitone-clash", self.rules(held, track((0, TPB * 2, 70), channel=1)))

    def test_bar_numbers_in_compound_and_short_meters(self):
        # Bars of 3/8 are 1.5 quarter notes; bars of 1/8 half of one. A zero-length
        # note at tick 1440 is in bar 3 of 3/8 (720 ticks per bar).
        for (num, den), tick, bar in (((3, 8), 1440, 3), ((1, 8), 240, 2)):
            with self.subTest(meter=f"{num}/{den}"):
                t = track((0, 120, 60))
                t.insert(0, mido.MetaMessage("time_signature", numerator=num, denominator=den, time=0))
                t.append(mido.Message("note_on", note=62, velocity=90, time=tick - 120))
                t.append(mido.Message("note_off", note=62, velocity=0, time=0))
                mf = mido.MidiFile(ticks_per_beat=TPB)
                mf.tracks.append(t)
                path = self.dir / "meter.mid"
                mf.save(path)
                [f] = [f for f in check_midi(path, "meter.mid") if f.rule == "zero-length-note"]
                self.assertIn(f"in bars {bar};", f.message)

    def test_unreadable_file(self):
        path = self.dir / "broken.mid"
        path.write_bytes(b"MThd\x00\x00\x00\x06\x00\x01\x00\x02\x01\xe0MTrk\x00\x00\xff\xff")
        self.assertEqual({f.rule for f in check_midi(path, "broken.mid")}, {"midi-unreadable"})


if __name__ == "__main__":
    unittest.main()
