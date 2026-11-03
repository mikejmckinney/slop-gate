"""MIDI checks from the stems, MIDI and exports part of the audio antipattern list.

Transcription (audio to MIDI) and generators leave structural faults that a
parser can find: notes that never end, one pitch started twice on the same
channel, a part duplicated note for note on a second track with the same
instrument, a held note chopped into a burst of short off-grid notes, and a
tempo map full of tiny changes. Semitone clashes between sustained parts are
reported too, because they are usually a wrong note; many are deliberate, so
that one stays a warning with the bars to listen to.

Doubling with a different instrument, repeated notes on the grid, rubato and
deliberate dissonance are left alone, as the antipattern list asks.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import mido

from .findings import Finding

MIDI_EXTS = {".mid", ".midi"}
DRUM_CHANNEL = 9  # channel 10 in 1-based numbering
RESTRIKE_MIN_RUN = 3
TEMPO_JITTER_MIN_EVENTS = 8
TEMPO_JITTER_BPM = 2.0
CLASH_MIN_BEATS = 0.25  # a sixteenth note in 4/4


@dataclass(frozen=True)
class Note:
    track: int
    channel: int
    program: int
    pitch: int
    start: int
    end: int
    velocity: int


def read_notes(mf: mido.MidiFile) -> tuple[list[Note], int, list[tuple[int, int]]]:
    """Notes, notes still sounding at the end of their track, and tempo events (tick, µs/beat)."""
    notes: list[Note] = []
    hanging = 0
    tempos: list[tuple[int, int]] = []
    for ti, track in enumerate(mf.tracks):
        tick = 0
        program = defaultdict(int)
        sounding: dict[tuple[int, int], list[tuple[int, int, int]]] = defaultdict(list)
        for msg in track:
            tick += msg.time
            if msg.type == "set_tempo":
                tempos.append((tick, msg.tempo))
            elif msg.type == "program_change":
                program[msg.channel] = msg.program
            elif msg.type == "note_on" and msg.velocity > 0:
                sounding[(msg.channel, msg.note)].append((tick, msg.velocity, program[msg.channel]))
            elif msg.type in ("note_off", "note_on"):
                stack = sounding.get((msg.channel, msg.note))
                if stack:
                    start, vel, prog = stack.pop(0)
                    notes.append(Note(ti, msg.channel, prog, msg.note, start, tick, vel))
        hanging += sum(len(v) for v in sounding.values())
    tempos.sort()
    return notes, hanging, tempos


def _bar_of(tick: int, bar_ticks: float) -> int:
    return int(tick // bar_ticks) + 1


def _bars(ticks: list[int], bar_ticks: float) -> str:
    bars = sorted({_bar_of(t, bar_ticks) for t in ticks})
    spans: list[str] = []
    for b in bars:
        if spans and spans[-1].split("–")[-1] == str(b - 1):
            spans[-1] = spans[-1].split("–")[0] + f"–{b}"
        else:
            spans.append(str(b))
    shown = ", ".join(spans[:6])
    return shown + (f" and {len(spans) - 6} more" if len(spans) > 6 else "")


def check_midi(abs_path: Path, rel: str) -> list[Finding]:
    out: list[Finding] = []

    def add(rule: str, severity: str, message: str, fix: str) -> None:
        out.append(Finding("midi", rule, severity, rel, None, message, fix))

    try:
        mf = mido.MidiFile(abs_path)
    except (OSError, ValueError, EOFError, KeyError, IndexError) as exc:
        add(
            "midi-unreadable",
            "error",
            f"The file doesn't parse as MIDI: {exc}.",
            "Re-export it from the tool that made it, or repair a copy with a MIDI editor. "
            "Check it opens in the player or DAW it is meant for.",
        )
        return out
    if mf.type == 2:
        return out  # sequential-song files are rare and timed per track; nothing below applies cleanly
    tpb = mf.ticks_per_beat
    # Bar length in ticks from the first time signature, kept fractional: a
    # 3/8 bar is 1.5 quarter notes and a 1/8 bar half of one. Bar numbers drift
    # after a meter change; they are a pointer for listening, not a score.
    num, den = next(
        ((m.numerator, m.denominator) for t in mf.tracks for m in t if m.type == "time_signature"), (4, 4)
    )
    bar_ticks = tpb * 4 * num / den
    notes, hanging, tempos = read_notes(mf)

    if hanging:
        add(
            "hanging-note",
            "warning",
            f"{hanging} note(s) start and never end, so a player holds them until it stops.",
            "Add the missing note-offs, or re-export from the source.",
        )

    # One pitch started twice on the same channel before the first ended.
    by_key: dict[tuple[int, int, int], list[Note]] = defaultdict(list)
    for n in notes:
        by_key[(n.track, n.channel, n.pitch)].append(n)
    overlaps = []
    zero = []
    for group in by_key.values():
        group.sort(key=lambda n: n.start)
        for a, b in zip(group, group[1:], strict=False):
            if b.start < a.end:
                overlaps.append(b.start)
        zero += [n.start for n in group if n.end <= n.start]
    if overlaps:
        add(
            "overlapping-note",
            "warning",
            f"{len(overlaps)} note(s) start while the same pitch is still sounding on the same channel, "
            f"in bars {_bars(overlaps, bar_ticks)}. Players disagree about which note-off "
            "ends which.",
            "Shorten the earlier note so it ends before the next one starts.",
        )
    if zero:
        add(
            "zero-length-note",
            "warning",
            f"{len(zero)} note(s) end on the tick they start, in bars {_bars(zero, bar_ticks)}; "
            "most players drop them.",
            "Give them a length or delete them.",
        )

    # The same part twice: same pitch, channel and instrument, starting together on two tracks.
    starts: dict[tuple[int, int, int, int], set[int]] = defaultdict(set)
    for n in notes:
        if n.channel != DRUM_CHANNEL:
            starts[(n.channel, n.program, n.pitch, n.start)].add(n.track)
    dupes = [k[3] for k, tracks in starts.items() if len(tracks) > 1]
    if len(dupes) >= 4:
        add(
            "duplicate-notes",
            "warning",
            f"{len(dupes)} notes are doubled on another track with the same channel and instrument, in bars "
            f"{_bars(dupes, bar_ticks)}. Combined, the part plays twice as loud and can phase.",
            "Delete the duplicate track or notes. Doubling with a different instrument or octave is not "
            "flagged; keep it when it is the arrangement.",
        )

    # A held note chopped into back-to-back fragments that don't sit on the grid.
    grid = max(1, tpb // 4)  # sixteenth notes
    slack = max(1, tpb // 32)
    restrikes = []
    for (_, channel, _), group in by_key.items():
        if channel == DRUM_CHANNEL:
            continue
        run = [group[0]] if group else []
        for a, b in zip(group, group[1:], strict=False):
            if 0 <= b.start - a.end <= slack:
                run.append(b)
                continue
            restrikes += _restrike(run, grid, slack)
            run = [b]
        restrikes += _restrike(run, grid, slack)
    if restrikes:
        add(
            "restruck-note",
            "warning",
            f"{len(restrikes)} held note(s) are split into back-to-back fragments off the sixteenth-note "
            f"grid, in bars {_bars(restrikes, bar_ticks)}: the pattern transcription leaves when a "
            "sustained note wobbles.",
            "Merge each run into one note where the source audio confirms one attack. Keep deliberate "
            "retriggering.",
        )

    # A tempo map of many tiny changes: detected tempo, not a performance.
    if len(tempos) >= TEMPO_JITTER_MIN_EVENTS:
        bpms = [60_000_000 / t for _, t in tempos]
        steps = sorted(abs(b - a) for a, b in zip(bpms, bpms[1:], strict=False))
        median = steps[len(steps) // 2] if steps else 0.0
        if median < TEMPO_JITTER_BPM:
            add(
                "tempo-jitter",
                "warning",
                f"{len(tempos)} tempo changes, the median only {median:.2f} BPM, between {min(bpms):.1f} and "
                f"{max(bpms):.1f} BPM. A steady passage with a tempo map like this drifts against the grid.",
                "Set one tempo and remap the notes to it, unless the rubato is intended.",
            )

    # Sustained semitone clashes between different pitched channels.
    clashes = _clashes(notes, tpb)
    if clashes:
        add(
            "semitone-clash",
            "warning",
            f"{len(clashes)} place(s) where two channels hold notes a semitone apart for a sixteenth or "
            f"more, in bars {_bars(clashes, bar_ticks)}. Often a wrong note; sometimes the "
            "intended color.",
            "Listen to those bars. Change one of the notes when it is wrong; keep deliberate dissonance.",
        )
    return out


def _restrike(run: list[Note], grid: int, slack: int) -> list[int]:
    if len(run) < RESTRIKE_MIN_RUN:
        return []
    off_grid = sum(1 for n in run[1:] if min(n.start % grid, grid - n.start % grid) > slack)
    return [run[0].start] if off_grid >= (len(run) - 1) / 2 else []


def _clashes(notes: list[Note], tpb: int) -> list[int]:
    min_len = int(CLASH_MIN_BEATS * tpb)
    pitched = sorted((n for n in notes if n.channel != DRUM_CHANNEL), key=lambda n: n.start)
    found: list[int] = []
    active: list[Note] = []
    for n in pitched:
        active = [a for a in active if a.end > n.start]
        for a in active:
            # Only a minor second in close position: a major seventh or a minor
            # ninth is a common chord color.
            if a.channel == n.channel or abs(a.pitch - n.pitch) != 1:
                continue
            if min(a.end, n.end) - max(a.start, n.start) >= min_len:
                found.append(max(a.start, n.start))
        active.append(n)
    return found
