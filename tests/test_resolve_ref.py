"""Choosing the slop-gate commit a reusable workflow checks out, and the workflows' wiring of it."""

import io
import json
import os
import re
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


ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
# The last commit before the resolver existed, the reviewer's example of an old override.
BEFORE_RESOLVER = "1e22f61"
OVERRIDE_EXPR = re.compile(r"\$\{\{\s*inputs\.slop-gate-ref(?:\s*\|\|\s*'([^']*)')?\s*\}\}")


def git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True)


def has_commit(ref):
    return git("cat-file", "-e", f"{ref}^{{commit}}").returncode == 0


def require_commit(case, ref):
    if has_commit(ref):
        return
    if os.environ.get("CI"):
        case.fail(f"commit {ref} is missing; CI's test job needs full history (fetch-depth: 0)")
    case.skipTest(f"commit {ref} is not in this clone")


def resolver_steps(wf):
    """The ref job's checkout step and its resolve step."""
    steps = wf["jobs"]["ref"]["steps"]
    checkout = next(s for s in steps if (s.get("with") or {}).get("repository") == "mikejmckinney/slop-gate")
    resolve = next(s for s in steps if s.get("id") == "resolve")
    return checkout, resolve


def checkout_ref(expr, override):
    """What the checkout's `ref:` evaluates to for a given slop-gate-ref."""
    return OVERRIDE_EXPR.sub(lambda m: override or (m.group(1) or ""), str(expr)).strip()


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

    def test_resolver_checkout_does_not_follow_the_override(self):
        pins = set()
        for name, wf in self.reusable_workflows().items():
            checkout, _ = resolver_steps(wf)
            ref = str(checkout["with"]["ref"])
            self.assertRegex(ref, r"^[0-9a-f]{40}$", f"{name}: the resolver must load from a fixed commit")
            pins.add(ref)
        self.assertEqual(len(pins), 1, f"the workflows pin different resolver commits: {sorted(pins)}")

    def test_pinned_resolver_matches_this_tree(self):
        for name, wf in self.reusable_workflows().items():
            checkout, _ = resolver_steps(wf)
            pin, path = checkout["with"]["ref"], checkout["with"]["sparse-checkout"]
            require_commit(self, pin)
            pinned = git("show", f"{pin}:{path}")
            self.assertEqual(pinned.returncode, 0, f"{name}: {path} is not in {pin}")
            self.assertEqual(
                pinned.stdout,
                (ROOT / path).read_text(),
                f"{name}: {path} changed since {pin}; move the pin in all four workflows to a commit with it",
            )


class BootstrapTest(unittest.TestCase):
    """Runs each workflow's ref job as written: a one-file checkout, then its run command."""

    def run_ref_job(self, name, wf, override, gh_script):
        checkout, resolve = resolver_steps(wf)
        self.assertEqual(resolve["env"]["SLOP_GATE_REF"], "${{ inputs.slop-gate-ref }}", name)
        ref = checkout_ref(checkout["with"]["ref"], override)
        path, sparse = checkout["with"]["path"], checkout["with"]["sparse-checkout"]
        if re.fullmatch(r"[0-9a-f]{40}", ref):
            require_commit(self, ref)
        with tempfile.TemporaryDirectory() as tmp:
            work, bin_dir = Path(tmp, "work"), Path(tmp, "bin")
            bin_dir.mkdir()
            shown = git("show", f"{ref}:{sparse}")
            if shown.returncode == 0:
                target = work / path / sparse
                target.parent.mkdir(parents=True)
                target.write_text(shown.stdout)
            else:
                work.mkdir()
            fake_gh = bin_dir / "gh"
            fake_gh.write_text(gh_script)
            fake_gh.chmod(0o755)
            out = Path(tmp, "output")
            env = {
                "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                "GITHUB_REPOSITORY": CONSUMER,
                "GITHUB_RUN_ID": "1",
                "GITHUB_RUN_ATTEMPT": "1",
                "GITHUB_OUTPUT": str(out),
                "GH_TOKEN": "unused",
                "SLOP_GATE_REF": override,
            }
            proc = subprocess.run(
                ["bash", "-e", "-c", resolve["run"]], cwd=work, env=env, capture_output=True, text=True
            )
            output = out.read_text() if out.exists() else ""
        return proc, output

    def test_override_from_before_the_resolver_existed(self):
        require_commit(self, BEFORE_RESOLVER)
        listed = {
            "referenced_workflows": [
                {
                    "path": f"mikejmckinney/slop-gate/.github/workflows/{n}@v1",
                    "ref": "refs/heads/v1",
                    "sha": SHA,
                }
                for n in ("slop-gate.yml", "slop-audit.yml", "slop-footer.yml", "slop-judge.yml")
            ]
        }
        answers = {
            "lookup works": f"#!/bin/sh\ncat <<'JSON'\n{json.dumps(listed)}\nJSON\n",
            "HTTP 403": "#!/bin/sh\necho 'gh: Resource not accessible (HTTP 403)' >&2\nexit 1\n",
        }
        for name, wf in WorkflowWiringTest().reusable_workflows().items():
            for case, gh_script in answers.items():
                with self.subTest(workflow=name, gh=case):
                    proc, output = self.run_ref_job(name, wf, BEFORE_RESOLVER, gh_script)
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    self.assertEqual(output, f"ref={BEFORE_RESOLVER}\n")
                    self.assertEqual(proc.stdout.count("::warning"), 1, proc.stdout)


if __name__ == "__main__":
    unittest.main()
