"""Listening review: reply parsing, the transcript diff, sidecars and the report. No network."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from slopgate import listen

SCRIPT = "Wave seven begins in 1,250 meters. Tap and hold to charge, then release to fire."


class ParseTests(unittest.TestCase):
    def test_reads_fenced_json_and_short_pattern_names(self):
        reply = (
            "```json\n"
            + json.dumps(
                {
                    "findings": [
                        {
                            "start_s": 10,
                            "end_s": 18,
                            "pattern": "metallic shimmer",
                            "evidence": "Cymbals turn to chirping.",
                            "confidence": "high",
                        }
                    ]
                }
            )
            + "\n```"
        )
        [f] = listen.parse(reply)
        self.assertEqual(f["pattern"], "metallic shimmer / watery modulation / chirping")
        self.assertEqual((f["start_s"], f["end_s"]), (10.0, 18.0))

    def test_rejects_invented_patterns_and_prose(self):
        bad = {"findings": [{"pattern": "sounds like AI", "confidence": "high"}]}
        with self.assertRaises(ValueError):
            listen.parse(json.dumps(bad))
        with self.assertRaises(ValueError):
            listen.parse("Sounds fine to me.")

    def test_empty_findings_are_fine(self):
        self.assertEqual(listen.parse('{"findings": []}'), [])


class TranscriptTests(unittest.TestCase):
    def test_misread_number_and_dropped_word(self):
        heard = "Wave seven begins in 12,050 meters. Tap and hold to charge, release to fire."
        diff = listen.word_diff(SCRIPT, heard)
        self.assertEqual(len(diff), 2, diff)
        self.assertIn("expected “1250”, heard “12050”", diff[0])
        self.assertIn("expected “then”, heard “(nothing)”", diff[1])

    def test_formatting_differences_are_not_reported(self):
        heard = "wave seven begins in 1250 meters tap and hold to charge then release to fire"
        self.assertEqual(listen.word_diff(SCRIPT, heard), [])


class RunTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.repo)
        (self.repo / "vo").mkdir()
        (self.repo / "vo" / "intro.mp3").write_bytes(b"not audio; review is mocked")
        (self.repo / "vo" / "intro.script.txt").write_text(SCRIPT)
        (self.repo / "vo" / "intro.prompt.txt").write_text("Calm tutorial narration, one voice.")

    def test_sidecars(self):
        self.assertEqual(listen.find_text(self.repo, "vo/intro.mp3"), (SCRIPT, "vo/intro.script.txt"))
        brief = listen.find_brief(self.repo, "vo/intro.mp3", {})
        self.assertEqual(brief, "Calm tutorial narration, one voice.")
        self.assertIsNone(listen.find_text(self.repo, "vo/other.mp3"))

    def test_run_merges_models_and_reports_the_word_diff(self):
        finding = {
            "start_s": 8.2,
            "end_s": 9.0,
            "pattern": "voice or accent drift",
            "evidence": "The narrator changes to a British male voice.",
            "confidence": "high",
        }

        def fake_review(model, path, brief):
            self.assertIn("one voice", brief)
            found = [finding] if model == "a/one" else []
            return {"findings": found, "error": None, "cost": 0.01}

        heard = "Wave seven begins in 12,050 meters. Tap and hold to charge, then release to fire."
        with (
            mock.patch.object(listen, "review", fake_review),
            mock.patch.object(listen, "transcribe", lambda m, p: (heard, 0.002)),
        ):
            brief = listen.find_brief(self.repo, "vo/intro.mp3", {})
            [r] = listen.run(self.repo, [("vo/intro.mp3", brief)], ["a/one", "b/two"], "t/x")
        self.assertEqual([f["models"] for f in r["rows"]], [["a/one"]])
        self.assertEqual(len(r["diff"]["changes"]), 1)
        text = listen.report([r], ["a/one", "b/two"], 0)
        self.assertIn("| 0:08.2–0:09.0 | voice or accent drift |", text)
        self.assertIn("high, model listening (one)", text)
        self.assertIn("expected “1250”, heard “12050”", text)
        self.assertIn("not a human listening check", text)

    def test_overlapping_findings_from_both_models_merge(self):
        rows = [
            {
                "start_s": 10,
                "end_s": 18,
                "pattern": "steady hiss",
                "evidence": "a",
                "confidence": "medium",
                "model": "a/one",
            },
            {
                "start_s": 11,
                "end_s": 19,
                "pattern": "steady hiss",
                "evidence": "b",
                "confidence": "high",
                "model": "b/two",
            },
            {
                "start_s": 40,
                "end_s": 41,
                "pattern": "steady hiss",
                "evidence": "c",
                "confidence": "low",
                "model": "b/two",
            },
        ]
        merged = listen.merge(rows)
        self.assertEqual(
            [(m["start_s"], m["end_s"], m["models"]) for m in merged],
            [(10, 19, ["a/one", "b/two"]), (40, 41, ["b/two"])],
        )
        self.assertEqual(merged[0]["confidence"], "high")
        text = listen.report(
            [{"path": "x.mp3", "rows": merged, "diff": None, "errors": [], "cost": 0}], ["a/one", "b/two"], 0
        )
        self.assertIn("high, model listening (both models)", text)

    def test_one_model_failing_is_reported_not_fatal(self):
        def flaky(model, path, brief):
            if model == "b/two":
                raise RuntimeError("HTTP 500")
            return {"findings": [], "error": None, "cost": 0.0}

        with (
            mock.patch.object(listen, "review", flaky),
            mock.patch.object(listen, "transcribe", lambda m, p: (SCRIPT, 0.0)),
        ):
            [r] = listen.run(self.repo, [("vo/intro.mp3", "")], ["a/one", "b/two"], "t/x")
        self.assertEqual(r["errors"], ["b/two: HTTP 500"])


if __name__ == "__main__":
    unittest.main()
