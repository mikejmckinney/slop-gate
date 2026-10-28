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


if __name__ == "__main__":
    unittest.main()
