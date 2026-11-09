"""GeneVA scoring: an unusable reply is never "no findings", and the matching rules hold."""

import importlib.util
import json
import random
import sys
import tempfile
import unittest
from pathlib import Path

GENEVA = Path(__file__).parent.parent / "bakeoff" / "video" / "geneva"
SPEC = importlib.util.spec_from_file_location("bakeoff_geneva_score", GENEVA / "score_geneva.py")
geneva = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(geneva)
pick_clips = sys.modules["pick_clips"]

APPROACH = "native:vendor/model"
OK = json.dumps({"findings": []})


def finding(**kw):
    base = {"start_s": 1.0, "end_s": 2.0, "category": "Motion", "box": [0.1, 0.1, 0.4, 0.4]}
    return {**base, **kw}


def artifact(**kw):
    base = {"categories": ["Motion"], "start_s": 1.2, "end_s": 1.4, "boxes": [[1.3, 0.1, 0.1, 0.4, 0.4]]}
    return {**base, **kw}


class ReplyTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.raw = Path(tmp.name)
        self.folder = self.raw / "native__vendor__model"
        self.folder.mkdir()

    def write(self, clip, rec):
        text = rec if isinstance(rec, str) else json.dumps(rec)
        (self.folder / f"{clip}.run1.json").write_text(text)

    def state(self, clip):
        return geneva.read_reply(APPROACH, clip, self.raw)

    def test_missing_reply_is_reported(self):
        self.assertEqual(self.state("a"), ("missing", None))

    def test_corrupt_file_is_a_failure(self):
        self.write("a", "{not json")
        self.assertEqual(self.state("a"), ("failed", None))

    def test_empty_file_is_a_failure(self):
        self.write("a", "")
        self.assertEqual(self.state("a"), ("failed", None))

    def test_recorded_error_is_a_failure(self):
        self.write("a", {"text": OK, "error": "HTTP 500"})
        self.assertEqual(self.state("a"), ("failed", None))

    def test_empty_reply_is_a_failure(self):
        self.write("a", {"text": "", "error": None})
        self.assertEqual(self.state("a"), ("failed", None))

    def test_prose_reply_is_a_failure(self):
        self.write("a", {"text": "Looks fine to me.", "error": None})
        self.assertEqual(self.state("a"), ("failed", None))

    def test_real_empty_findings_are_kept(self):
        self.write("a", {"text": OK, "error": None})
        self.assertEqual(self.state("a"), ("ok", []))

    def test_failed_clip_leaves_the_denominator(self):
        self.write("a", {"text": "garbage", "error": None})
        self.write("b", {"text": json.dumps({"findings": [finding()]}), "error": None, "cost": 0.01})
        self.write("c", {"text": OK, "error": None, "cost": 0.01})
        clips = [
            {"clip": "a", "group": "unannotated", "generator": "sora", "seconds": 5.0},
            {"clip": "b", "group": "annotated", "generator": "sora", "seconds": 5.0},
            {"clip": "c", "group": "unannotated", "generator": "sora", "seconds": 5.0},
        ]
        result = geneva.score_approach(APPROACH, clips, {"a": [], "b": [artifact()], "c": []}, self.raw)
        self.assertEqual(result["replies"], {"ok": 2, "failed": 1, "missing": 0})
        self.assertEqual(result["flagged_unannotated"]["n"], 1)
        self.assertEqual(result["flagged_annotated"]["k"], 1)
        self.assertEqual(result["usd"], 0.02)


class MatchTest(unittest.TestCase):
    def matched(self, f, a):
        return geneva.match(geneva.usable(f), a)

    def test_full_match(self):
        self.assertTrue(self.matched(finding(), artifact())["full"])

    def test_label_categories_match_prompt_spelling(self):
        result = self.matched(
            finding(category="Shape/Form/Geometry"), artifact(categories=["Shape, Form, Geometry"])
        )
        self.assertTrue(result["category"])

    def test_time_tolerance_edges(self):
        self.assertTrue(self.matched(finding(start_s=1.8, end_s=2.0), artifact())["time"])
        self.assertFalse(self.matched(finding(start_s=2.0, end_s=3.0), artifact())["time"])

    def test_box_outside_the_time_window_does_not_count(self):
        late = artifact(boxes=[[4.0, 0.1, 0.1, 0.4, 0.4]])
        self.assertFalse(self.matched(finding(), late)["box"])

    def test_box_below_minimum_iou(self):
        far = finding(box=[0.45, 0.45, 0.4, 0.4])
        self.assertFalse(self.matched(far, artifact())["box"])

    def test_malformed_findings_are_not_usable(self):
        for bad in (
            finding(category="Vibes"),
            finding(box=[0.1, 0.1, 0.0, 0.4]),
            finding(box="center"),
            finding(box=[0.1, 0.1, 0.4]),
            finding(start_s="soon"),
        ):
            self.assertIsNone(geneva.usable(bad))

    def test_malformed_finding_matches_nothing(self):
        self.assertFalse(any(geneva.score_clip([finding(box=None)], [artifact()]).values()))


class DiscriminationTest(unittest.TestCase):
    def test_flagging_everything_scores_zero(self):
        result = geneva.discrimination([True] * 10, [True] * 5, random.Random(1))
        self.assertEqual(result["value"], 0.0)

    def test_empty_group_has_no_score(self):
        self.assertIsNone(geneva.discrimination([], [True], random.Random(1))["value"])


class LabelsTest(unittest.TestCase):
    def test_changed_labels_file_stops_the_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "labels.json"
            path.write_text("[]")
            with self.assertRaises(SystemExit):
                pick_clips.load_labels(path)

    def test_clip_that_no_longer_fits_its_group_stops_the_score(self):
        item = {
            "clip": "a",
            "video_id": "sora/1.mp4",
            "group": "unannotated",
            "generator": "sora",
            "seconds": 5.0,
        }
        with self.assertRaises(SystemExit):
            geneva.checked_clips({"clips": [item]}, {"sora/1.mp4": {"artifacts": [artifact()]}})
        with self.assertRaises(SystemExit):
            geneva.checked_clips({"clips": [item]}, {})


if __name__ == "__main__":
    unittest.main()
