"""Choosing the slop-gate commit a reusable workflow checks out, and the workflows' wiring of it."""

import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import yaml

from slopgate import resolve_ref
from slopgate.resolve_ref import DEFAULT_REF, LookupFailed, resolve

SHA = "331dcaca5b52ef68f87d9ef27148b7239b8d53d8"
OTHER = "449031e099a5e46b461670eea643896e23ec6fd6"
CONSUMER = "someone/consumer"

# Shapes copied from real runs: a caller at @v1, a caller pinned to a SHA (no `ref`
# field), and slop-gate's own ci.yml calling ./.github/workflows/ on a PR.
AT_V1 = {
    "referenced_workflows": [
        {
            "path": "mikejmckinney/slop-gate/.github/workflows/slop-gate.yml@v1",
            "ref": "refs/heads/v1",
            "sha": SHA,
        },
        {
            "path": "mikejmckinney/slop-gate/.github/workflows/slop-judge.yml@v1",
            "ref": "refs/heads/v1",
            "sha": SHA,
        },
    ]
}
PINNED = {
    "referenced_workflows": [
        {"path": f"mikejmckinney/slop-gate/.github/workflows/slop-footer.yml@{OTHER}", "sha": OTHER}
    ]
}
LOCAL_PR = {
    "referenced_workflows": [
        {
            "path": f"mikejmckinney/slop-gate/.github/workflows/slop-gate.yml@{OTHER}",
            "ref": "refs/pull/22/merge",
            "sha": OTHER,
        }
    ]
}


def run(data):
    return lambda: data


def failing(message):
    def fetch():
        raise LookupFailed(message)

    return fetch


class ResolveTest(unittest.TestCase):
    def test_match_uses_detected_sha(self):
        res = resolve("slop-gate.yml", "", CONSUMER, run(AT_V1))
        self.assertEqual(res.ref, SHA)
        self.assertEqual(res.warnings, [])

    def test_match_picks_this_workflows_entry(self):
        data = {"referenced_workflows": AT_V1["referenced_workflows"] + PINNED["referenced_workflows"]}
        self.assertEqual(resolve("slop-footer.yml", "", CONSUMER, run(data)).ref, OTHER)
        self.assertEqual(resolve("slop-judge.yml", "", CONSUMER, run(data)).ref, SHA)

    def test_sha_pinned_call_has_no_ref_field(self):
        res = resolve("slop-footer.yml", "", CONSUMER, run(PINNED))
        self.assertEqual(res.ref, OTHER)
        self.assertEqual(res.warnings, [])

    def test_no_match_falls_back_to_v1_with_one_warning(self):
        res = resolve("slop-audit.yml", "", CONSUMER, run(AT_V1))
        self.assertEqual(res.ref, DEFAULT_REF)
        self.assertEqual(len(res.warnings), 1)
        self.assertIn("actions: read", res.warnings[0])

    def test_other_repo_with_same_file_name_does_not_match(self):
        data = {
            "referenced_workflows": [
                {"path": "fork/slop-gate/.github/workflows/slop-gate.yml@v1", "sha": SHA}
            ]
        }
        self.assertEqual(resolve("slop-gate.yml", "", CONSUMER, run(data)).ref, DEFAULT_REF)

    def test_empty_run_falls_back(self):
        res = resolve("slop-gate.yml", "", CONSUMER, run({}))
        self.assertEqual(res.ref, DEFAULT_REF)
        self.assertEqual(len(res.warnings), 1)

    def test_two_calls_at_different_commits_fall_back(self):
        data = {
            "referenced_workflows": AT_V1["referenced_workflows"]
            + [{"path": f"mikejmckinney/slop-gate/.github/workflows/slop-gate.yml@{OTHER}", "sha": OTHER}]
        }
        res = resolve("slop-gate.yml", "", CONSUMER, run(data))
        self.assertEqual(res.ref, DEFAULT_REF)
        self.assertIn("more than one commit", res.warnings[0])

    def test_api_403_falls_back_to_v1_with_one_warning(self):
        res = resolve(
            "slop-gate.yml", "", CONSUMER, failing("HTTP 403: Resource not accessible by integration")
        )
        self.assertEqual(res.ref, DEFAULT_REF)
        self.assertEqual(len(res.warnings), 1)
        self.assertIn("HTTP 403", res.warnings[0])
        self.assertIn("actions: read", res.warnings[0])

    def test_api_error_with_override_uses_override_and_warns(self):
        res = resolve("slop-gate.yml", "v2", CONSUMER, failing("HTTP 500"))
        self.assertEqual(res.ref, "v2")
        self.assertEqual(len(res.warnings), 1)
        self.assertIn("actions: read", res.warnings[0])

    def test_override_equal_to_detected_sha(self):
        res = resolve("slop-gate.yml", SHA, CONSUMER, run(AT_V1))
        self.assertEqual(res.ref, SHA)
        self.assertEqual(res.warnings, [])

    def test_override_short_sha_counts_as_equal(self):
        res = resolve("slop-gate.yml", SHA[:7], CONSUMER, run(AT_V1))
        self.assertEqual(res.ref, SHA)
        self.assertEqual(res.warnings, [])

    def test_override_equal_to_written_ref_pins_the_sha(self):
        # Today's callers pass slop-gate-ref: v1 next to @v1. That names the same commit,
        # so no warning, and the SHA keeps a v1 that moves mid-run from changing it.
        res = resolve("slop-gate.yml", "v1", CONSUMER, run(AT_V1))
        self.assertEqual(res.ref, SHA)
        self.assertEqual(res.warnings, [])

    def test_override_that_differs_wins_and_warns(self):
        res = resolve("slop-gate.yml", OTHER, CONSUMER, run(AT_V1))
        self.assertEqual(res.ref, OTHER)
        self.assertEqual(len(res.warnings), 1)
        self.assertIn(OTHER, res.warnings[0])
        self.assertIn(SHA, res.warnings[0])

    def test_short_override_is_not_a_prefix_match(self):
        res = resolve("slop-gate.yml", SHA[:3], CONSUMER, run(AT_V1))
        self.assertEqual(res.ref, SHA[:3])
        self.assertEqual(len(res.warnings), 1)

    def test_local_call_keeps_override_silently(self):
        # ci.yml passes the PR head; the run lists the merge commit. That is intended.
        head = "1c2991518b58f2db2add99311ea9321b925d7b29"
        res = resolve("slop-gate.yml", head, "mikejmckinney/slop-gate", run(LOCAL_PR))
        self.assertEqual(res.ref, head)
        self.assertEqual(res.warnings, [])

    def test_local_call_without_actions_read_keeps_override_silently(self):
        head = "1c2991518b58f2db2add99311ea9321b925d7b29"
        res = resolve("slop-gate.yml", head, "mikejmckinney/slop-gate", failing("HTTP 403"))
        self.assertEqual(res.ref, head)
        self.assertEqual(res.warnings, [])

    def test_local_call_without_override_uses_detected(self):
        res = resolve("slop-gate.yml", "", "MikeJMcKinney/slop-gate", run(LOCAL_PR))
        self.assertEqual(res.ref, OTHER)


class MainTest(unittest.TestCase):
    def run_main(self, gh_result, override=""):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp, "out")
            env = {
                "GITHUB_REPOSITORY": CONSUMER,
                "GITHUB_RUN_ID": "42",
                "GITHUB_RUN_ATTEMPT": "2",
                "SLOP_GATE_REF": override,
                "GITHUB_OUTPUT": str(out),
            }
            stdout = io.StringIO()
            with (
                mock.patch.dict(os.environ, env, clear=True),
                mock.patch.object(resolve_ref.subprocess, "run", return_value=gh_result) as gh,
                redirect_stdout(stdout),
            ):
                code = resolve_ref.main(["resolve_ref.py", "slop-gate.yml"])
            return code, out.read_text(), stdout.getvalue(), gh

    def test_writes_detected_sha_and_reads_this_attempt(self):
        ok = subprocess.CompletedProcess([], 0, stdout=json.dumps(AT_V1), stderr="")
        code, out, _, gh = self.run_main(ok)
        self.assertEqual(code, 0)
        self.assertEqual(out, f"ref={SHA}\n")
        self.assertEqual(gh.call_args.args[0], ["gh", "api", f"repos/{CONSUMER}/actions/runs/42/attempts/2"])

    def test_gh_403_prints_a_warning_and_still_succeeds(self):
        denied = subprocess.CompletedProcess(
            [], 1, stdout="", stderr="gh: Resource not accessible by integration (HTTP 403)\n"
        )
        code, out, stdout, _ = self.run_main(denied)
        self.assertEqual(code, 0)
        self.assertEqual(out, f"ref={DEFAULT_REF}\n")
        warnings = [line for line in stdout.splitlines() if line.startswith("::warning")]
        self.assertEqual(len(warnings), 1)
        self.assertIn("HTTP 403", warnings[0])


WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"


class WorkflowWiringTest(unittest.TestCase):
    """Every workflow that checks out slop-gate's scripts takes the ref from the resolver job."""

    def reusable_workflows(self):
        found = {}
        for path in sorted(WORKFLOWS.glob("*.yml")):
            text = path.read_text()
            if "repository: mikejmckinney/slop-gate" in text:
                found[path.name] = yaml.safe_load(text)
        return found

    def test_found_the_reusable_workflows(self):
        self.assertGreaterEqual(
            set(self.reusable_workflows()),
            {"slop-gate.yml", "slop-audit.yml", "slop-footer.yml", "slop-judge.yml"},
        )

    def test_override_defaults_to_empty(self):
        for name, wf in self.reusable_workflows().items():
            on = wf.get("on", wf.get(True))
            self.assertEqual(on["workflow_call"]["inputs"]["slop-gate-ref"]["default"], "", name)

    def test_resolver_job_inherits_the_callers_permissions(self):
        # A called job that asks for actions: read fails every caller that doesn't grant it,
        # so the resolver job asks for nothing and gets whatever the caller allows.
        for name, wf in self.reusable_workflows().items():
            self.assertNotIn("permissions", wf, name)
            ref_job = wf["jobs"]["ref"]
            self.assertNotIn("permissions", ref_job, name)
            run_steps = [s.get("run", "") for s in ref_job["steps"]]
            self.assertTrue(any(f"resolve_ref.py {name}" in r for r in run_steps), name)

    def test_scripts_checkout_uses_resolved_ref(self):
        for name, wf in self.reusable_workflows().items():
            for job_name, job in wf["jobs"].items():
                if job_name == "ref":
                    continue
                for step in job.get("steps", []):
                    with_ = step.get("with") or {}
                    if with_.get("repository") == "mikejmckinney/slop-gate":
                        self.assertEqual(
                            with_.get("ref"), "${{ needs.ref.outputs.ref }}", f"{name}:{job_name}"
                        )
                        self.assertIn("ref", job.get("needs", []), f"{name}:{job_name}")


if __name__ == "__main__":
    unittest.main()
