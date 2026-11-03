"""Listening review: reply parsing, the transcript diff, sidecars and the report. No network."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from slopgate import judge, listen
from slopgate.paths import repo_file

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

    def test_scripts_without_spaces_compare_by_character(self):
        self.assertEqual(
            listen.word_diff("你好世界", "完全不同"), ["after “”: expected “你好世界”, heard “完全不同”"]
        )
        self.assertEqual(
            listen.word_diff("你好世界", ""), ["after “”: expected “你好世界”, heard “(nothing)”"]
        )
        self.assertEqual(listen.word_diff("第七波开始。", "第七波开始"), [])
        [change] = listen.word_diff("ボタンを押して", "ボタンを離して")
        self.assertIn("expected “押”, heard “離”", change)

    def test_other_scripts_keep_their_words(self):
        [change] = listen.word_diff("Волна семь начинается", "Волна восемь начинается")
        self.assertIn("expected “семь”, heard “восемь”", change)
        [change] = listen.word_diff("اضغط مع الاستمرار", "اضغط الاستمرار")
        self.assertIn("expected “مع”, heard “(nothing)”", change)
        self.assertEqual(listen.word_diff("नमस्ते दुनिया", "नमस्ते दुनिया।"), [])

    def test_a_sidecar_with_no_words_is_reported_not_passed(self):
        result = listen.check_words("♪ ♪ ♪", "la la la", truncated=False)
        self.assertIn("no words", result["skipped"])


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

    def test_a_file_longer_than_the_limit_is_not_diffed_against_the_whole_script(self):
        def no_transcribe(m, p):
            raise AssertionError("a truncated file must not be transcribed for the diff")

        with (
            mock.patch.object(listen, "review", lambda m, p, b: {"findings": [], "error": None, "cost": 0}),
            mock.patch.object(listen, "transcribe", no_transcribe),
            mock.patch.object(listen, "duration", lambda p: listen.MAX_SECONDS + 60),
        ):
            [r] = listen.run(self.repo, [("vo/intro.mp3", "")], ["a/one"], "t/x")
        self.assertTrue(r["truncated"])
        self.assertNotIn("changes", r["diff"])
        text = listen.report([r], ["a/one"], 0)
        self.assertIn("Only the first 5:00.0 was reviewed", text)
        self.assertIn("Words not checked against `vo/intro.script.txt`: the file is longer than", text)
        self.assertNotIn("had no findings", text)

    def test_findings_timed_past_the_end_are_dropped_and_said(self):
        ghost = {
            "start_s": 255,
            "end_s": 258,
            "pattern": "disconnected sections",
            "evidence": "x",
            "confidence": "high",
        }
        real = {**ghost, "start_s": 170, "end_s": 190}
        with (
            mock.patch.object(
                listen, "review", lambda m, p, b: {"findings": [ghost, real], "error": None, "cost": 0}
            ),
            mock.patch.object(listen, "transcribe", lambda m, p: (SCRIPT, 0.0)),
            mock.patch.object(listen, "duration", lambda p: 176.3),
        ):
            [r] = listen.run(self.repo, [("vo/intro.mp3", "")], ["a/one"], "t/x")
        self.assertEqual([(f["start_s"], f["end_s"]) for f in r["rows"]], [(170, 176.3)])
        self.assertEqual(
            r["errors"], ["a/one: dropped 1 finding(s) timed after the end of the audio it heard"]
        )


class SidecarEscapeTests(unittest.TestCase):
    """A PR must not be able to make the judge read files outside the checkout:
    the job holds OPENROUTER_API_KEY and sends sidecar text to a model."""

    def setUp(self):
        base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, base)
        self.secret = base / "environ"
        self.secret.write_text("OPENROUTER_API_KEY=canary")
        self.repo = base / "repo"
        (self.repo / "vo").mkdir(parents=True)
        (self.repo / "vo" / "intro.mp3").write_bytes(b"audio")
        (self.repo / "art").mkdir()
        (self.repo / "art" / "hero.png").write_bytes(b"png")

    def test_symlinked_sidecars_are_ignored(self):
        (self.repo / "vo" / "intro.prompt.txt").symlink_to(self.secret)
        (self.repo / "vo" / "intro.script.txt").symlink_to(self.secret)
        (self.repo / "art" / "hero.prompt.txt").symlink_to(self.secret)
        self.assertEqual(
            listen.find_brief(self.repo, "vo/intro.mp3", {"spec": "from manifest"}), "from manifest"
        )
        self.assertIsNone(listen.find_text(self.repo, "vo/intro.mp3"))
        self.assertNotIn("canary", judge.find_spec(self.repo, "art/hero.png", {})[0])

    def test_symlinked_parent_directory_is_ignored(self):
        outside = self.secret.parent / "outside"
        outside.mkdir()
        (outside / "intro.mp3").write_bytes(b"audio")
        (outside / "intro.lyrics.txt").write_text("canary")
        (self.repo / "linked").symlink_to(outside)
        self.assertIsNone(listen.find_text(self.repo, "linked/intro.mp3"))
        self.assertIsNone(repo_file(self.repo, "linked/intro.mp3"))

    def test_repo_file_rejects_escapes_and_accepts_plain_files(self):
        (self.repo / "vo" / "alias.mp3").symlink_to(self.repo / "vo" / "intro.mp3")
        self.assertEqual(repo_file(self.repo, "vo/intro.mp3"), (self.repo / "vo" / "intro.mp3").resolve())
        self.assertIsNone(repo_file(self.repo, "vo/alias.mp3"))
        self.assertIsNone(repo_file(self.repo, "../environ"))
        self.assertIsNone(repo_file(self.repo, "vo"))
        self.assertIsNone(repo_file(self.repo, "vo/missing.mp3"))


if __name__ == "__main__":
    unittest.main()
