import unittest

from slopgate.judge import CATEGORIES, parse, rubric


class ParseTests(unittest.TestCase):
    def test_fenced_json_and_verdict_recomputed(self):
        reply = (
            '```json\n{"findings": [{"category": "lettering", "severity": "blocking", '
            '"where": "sign", "evidence": "garbled"}], "verdict": "pass"}\n```'
        )
        self.assertEqual(parse(reply)["verdict"], "fail")

    def test_empty_findings_pass(self):
        self.assertEqual(parse('{"findings": [], "verdict": "pass"}')["verdict"], "pass")

    def test_unknown_category_is_rejected(self):
        with self.assertRaises(ValueError):
            parse('{"findings": [{"category": "vibes", "severity": "blocking"}]}')

    def test_no_json_is_rejected(self):
        with self.assertRaises(ValueError):
            parse("")

    def test_rubric_lists_every_category(self):
        text = rubric()
        for key in CATEGORIES:
            self.assertIn(f"- {key}:", text)


class PrJobTests(unittest.TestCase):
    def setUp(self):
        import shutil
        import tempfile
        from pathlib import Path

        self.repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.repo)

    def test_spec_sidecar_then_manifest_then_none(self):
        from slopgate.judge import NO_SPEC, find_spec

        (self.repo / "art").mkdir()
        (self.repo / "art" / "hero.prompt.txt").write_text("A fox courier.\n")
        self.assertEqual(find_spec(self.repo, "art/hero.png", {}), ("A fox courier.", "art/hero.prompt.txt"))
        self.assertEqual(
            find_spec(self.repo, "art/tile.png", {"spec": "Seamless grass"}), ("Seamless grass", "manifest")
        )
        self.assertEqual(find_spec(self.repo, "art/tile.png", {}), (NO_SPEC, "none"))

    def test_findings_merge_by_category_and_failures_are_reported(self):
        from slopgate.judge import report, run

        def fake(model, path, spec):
            if model == "c":
                raise RuntimeError("HTTP 503")
            findings = [
                {"category": "hands-limbs", "severity": "blocking", "where": "hand", "evidence": "six"}
            ]
            if model == "a":
                findings.append(
                    {"category": "lighting", "severity": "advisory", "where": "sky", "evidence": "x"}
                )
            return {"findings": findings, "verdict": "fail", "cost": 0.01, "error": None}

        results = run(self.repo, [("hero.png", "spec", "manifest")], ["a", "b", "c"], review_fn=fake)
        rows = results[0]["rows"]
        self.assertEqual(
            [(r["category"], r["models"]) for r in rows], [("hands-limbs", ["a", "b"]), ("lighting", ["a"])]
        )
        self.assertEqual(results[0]["errors"], ["c: HTTP 503"])
        text = report(results, ["a", "b", "c"], skipped=2)
        self.assertIn("never blocks the merge", text)
        self.assertIn("2 more image(s) were skipped", text)
        self.assertIn("Review failed: c: HTTP 503", text)

    def test_unknown_judge_key_fails_loudly(self):
        from slopgate.config import load

        (self.repo / ".slop-gate.yml").write_text("judge:\n  model: x\n")
        with self.assertRaises(ValueError):
            load(self.repo)


if __name__ == "__main__":
    unittest.main()
