"""The bakeoff runner's spend cap holds with calls in flight. No network: `post` is faked."""

import importlib.util
import json
import tempfile
import threading
import unittest
from argparse import Namespace
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

SPEC = importlib.util.spec_from_file_location(
    "bakeoff_video_run", Path(__file__).parent.parent / "bakeoff" / "video" / "run.py"
)
run = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run)

APPROACH = "native:vendor/model"


def reply(cost):
    usage = {} if cost is None else {"cost": cost}
    return {"choices": [{"message": {"content": '{"findings": []}'}}], "usage": usage}


class AdmissionTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "c.mp4").write_bytes(b"not really a video")
        self.args = Namespace(clips=self.root, out=self.root / "results", cache=self.root / "frames")
        self.dispatched = 0
        self.lock = threading.Lock()

    def items(self, n):
        return [{"clip": f"c{i}", "file": "c.mp4", "brief": "b", "seconds": 1.0} for i in range(n)]

    def run_jobs(self, n, cap, post, bound=1.0, workers=None):
        budget = run.Budget(cap, self.root)
        with (
            mock.patch.object(run, "request_bound", return_value=bound, create=True),
            mock.patch.object(run, "post", post),
            mock.patch.object(run.time, "sleep"),
            ThreadPoolExecutor(max_workers=workers or n) as pool,
        ):
            statuses = list(
                pool.map(lambda item: run.job(APPROACH, item, 1, self.args, budget), self.items(n))
            )
        return budget, statuses

    def counted(self):
        with self.lock:
            self.dispatched += 1
            return self.dispatched

    def test_concurrent_calls_never_exceed_the_cap(self):
        everyone_in = threading.Event()

        def post(_body):
            if self.counted() == 6:
                everyone_in.set()
            # Hold the reply open so that a racing worker would dispatch too.
            everyone_in.wait(0.3)
            return reply(1.0)

        budget, statuses = self.run_jobs(6, 1.0, post)
        self.assertLessEqual(self.dispatched * 1.0, 1.0)
        self.assertEqual(self.dispatched, 1)
        self.assertLessEqual(budget.spent, 1.0)
        self.assertEqual(statuses.count("skipped (cap)"), 5)

    def test_calls_that_fit_all_run(self):
        def post(_body):
            self.counted()
            return reply(0.5)

        budget, statuses = self.run_jobs(4, 2.0, post, bound=0.5)
        self.assertEqual(self.dispatched, 4)
        self.assertEqual(budget.spent, 2.0)
        self.assertEqual(statuses, ["ok"] * 4)

    def test_a_possibly_billed_attempt_keeps_its_reservation(self):
        def post(_body):
            self.counted()
            raise ConnectionResetError("reset after the provider may have billed")

        budget, statuses = self.run_jobs(1, 1.5, post)
        # The first attempt holds $1 forever, so a second $1 attempt can't fit.
        self.assertEqual(self.dispatched, 1)
        self.assertEqual(budget.unknown, 1.0)
        self.assertEqual(statuses, ["error"])
        saved = json.loads(next((self.root / "results").rglob("*.json")).read_text())
        self.assertEqual(saved["retained_usd"], 1.0)
        # A restart reads the retained amount back from disk.
        self.assertEqual(run.Budget(1.5, self.root).unknown, 1.0)

    def test_a_reply_without_a_cost_keeps_its_reservation(self):
        def post(_body):
            self.counted()
            return reply(None)

        budget, _ = self.run_jobs(2, 1.5, post)
        self.assertEqual(self.dispatched, 1)
        self.assertEqual(budget.unknown, 1.0)

    def test_a_refused_http_attempt_releases_its_reservation(self):
        def post(_body):
            self.counted()
            raise run.urllib.error.HTTPError("u", 400, "bad", None, None)

        budget, statuses = self.run_jobs(2, 1.0, post)
        self.assertEqual(self.dispatched, 2)
        self.assertEqual((budget.spent, budget.unknown), (0.0, 0.0))
        self.assertEqual(statuses, ["error", "error"])

    def test_a_cost_above_the_bound_stops_new_calls(self):
        def post(_body):
            self.counted()
            return reply(0.9)

        budget, statuses = self.run_jobs(2, 2.0, post, bound=0.5, workers=1)
        self.assertEqual(self.dispatched, 1)
        self.assertTrue(budget.stopped)
        self.assertEqual(sorted(statuses), ["ok", "skipped (cap)"])

    def test_the_bound_covers_the_heaviest_published_call(self):
        parts = [{"type": "text", "text": "x" * 2500}]
        parts += [{"type": "image_url", "image_url": {"url": "data:,"}}] * 40
        self.assertGreater(run.request_bound(parts, 10.0), 0.113)
        native = [{"type": "video_url", "video_url": {"url": "data:,"}}]
        self.assertGreater(run.request_bound(native, 10.0), 0.05)


if __name__ == "__main__":
    unittest.main()
