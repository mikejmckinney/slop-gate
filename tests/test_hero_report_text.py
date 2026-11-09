"""The hero video reads its findings from the committed reports."""

import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parent.parent / "docs/media/src/hero_video.py"
spec = importlib.util.spec_from_file_location("hero_video", SCRIPT)
hero = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hero)


class ReportText(unittest.TestCase):
    def test_every_scene_finds_its_values_in_the_committed_reports(self):
        self.assertEqual(
            [f[1] for f in hero.writing_findings()], ["ContrastFormula", "AIVocabulary", "CorporateVerbs"]
        )
        self.assertEqual(
            hero.sprite_facts("gpt-image-2.5-sunburst"), "1024×1024 · 60,167 colors · soft alpha"
        )
        self.assertEqual(hero.sprite_facts("seedream-5-0-pro"), "2048×2048 · JPEG named .png")
        self.assertEqual(hero.music_findings()[1][1], "−10.9 LUFS; the manifest targets −16 ±2.")
        self.assertEqual(hero.speech_diffs()[1], ("after “seven begins in”", "1250", "12050"))
        self.assertEqual(hero.blocking_count("pixel_before"), 16)
        self.assertEqual(hero.passes_badge("music_after"), "passes, 0 findings")

    def with_changed_report(self, name, old, new):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        for path in hero.REPORTS.glob("*.md"):
            shutil.copy(path, tmp)
        report = tmp / f"{name}.md"
        text = report.read_text()
        self.assertIn(old, text)
        report.write_text(text.replace(old, new))
        return mock.patch.object(hero, "REPORTS", tmp)

    def test_a_new_number_in_a_report_reaches_the_video(self):
        with self.with_changed_report("music_before", "933 ms at the end", "120 ms at the end"):
            self.assertTrue(hero.music_findings()[2][1].startswith("120 ms of silence"))

    def test_a_report_without_the_value_stops_the_build(self):
        changed = self.with_changed_report("music_before", "`loop-gap`", "`renamed-rule`")
        with changed, self.assertRaises(SystemExit) as stop:
            hero.music_findings()
        self.assertIn("loop-gap", str(stop.exception))

    def test_a_pass_badge_needs_a_clean_report(self):
        changed = self.with_changed_report(
            "writing_after", "0 blocking, 0 advisory", "1 blocking, 0 advisory"
        )
        with changed, self.assertRaises(SystemExit):
            hero.passes_badge("writing_after")


if __name__ == "__main__":
    unittest.main()
