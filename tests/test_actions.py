"""The composite actions: scripts they call exist, third-party actions are pinned, inputs are documented."""

import re
import unittest
from pathlib import Path

import yaml

from slopgate.media import AUDIO_EXTS
from slopgate.video import VIDEO_EXTS

ROOT = Path(__file__).resolve().parent.parent
ACTIONS = {
    "action.yml": ROOT / "action.yml",
    "audit/action.yml": ROOT / "audit" / "action.yml",
    "footer/action.yml": ROOT / "footer" / "action.yml",
    "judge/action.yml": ROOT / "judge" / "action.yml",
}
SCRIPT_PATH = re.compile(r"""\$\{?SLOPGATE_DIR\}?/([^"'\s;)]+)""")
MODULE = re.compile(r"python3? -m (slopgate(?:\.\w+)?)")
PINNED = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
INPUT_REF = re.compile(r"inputs\.([\w-]+)")
# A composite input is always a string, so `if: inputs.x` is true for "false".
BARE_INPUT_IN_IF = re.compile(r"inputs\.[\w-]++(?!\s*[=!]=)")


def load(path):
    return yaml.safe_load(path.read_text())


def steps(action):
    return action["runs"]["steps"]


class ActionsTest(unittest.TestCase):
    def test_every_action_yml_is_listed(self):
        found = {str(p.relative_to(ROOT)) for p in [ROOT / "action.yml", *ROOT.glob("*/action.yml")]}
        self.assertEqual(found, set(ACTIONS))

    def test_composite(self):
        for name, path in ACTIONS.items():
            action = load(path)
            self.assertEqual(action["runs"]["using"], "composite", name)
            for step in steps(action):
                if "run" in step:
                    self.assertEqual(step.get("shell"), "bash", f"{name}: {step.get('name')}")

    def test_first_step_sets_slopgate_dir(self):
        for name, path in ACTIONS.items():
            first = steps(load(path))[0]
            self.assertEqual(first["env"]["ACTION_DIR"], "${{ github.action_path }}", name)
            self.assertIn("SLOPGATE_DIR=", first["run"], name)
            self.assertIn('>> "$GITHUB_ENV"', first["run"], name)
            nested = "/" in name
            self.assertEqual('"$ACTION_DIR/.."' in first["run"], nested, name)

    def test_script_paths_exist(self):
        for name, path in ACTIONS.items():
            runs = [s["run"] for s in steps(load(path)) if "run" in s]
            text = "\n".join(runs)
            self.assertNotIn(".slop-gate/", text, f"{name}: use $SLOPGATE_DIR, not the old checkout path")
            called = SCRIPT_PATH.findall(text)
            self.assertTrue(called or MODULE.findall(text), name)
            for rel in called:
                self.assertTrue((ROOT / rel).is_file(), f"{name}: {rel} does not exist")
            for module in MODULE.findall(text):
                parts = module.split(".")
                file = ROOT.joinpath(*parts[:-1], parts[-1] + ".py") if len(parts) > 1 else None
                package = ROOT / module.replace(".", "/") / "__main__.py"
                self.assertTrue((file and file.is_file()) or package.is_file(), f"{name}: {module}")
            for line in text.splitlines():
                if "python" in line and " -m slopgate" in line:
                    self.assertIn('PYTHONPATH="$SLOPGATE_DIR"', line, name)

    def test_uses_are_pinned_to_a_sha(self):
        for name, path in ACTIONS.items():
            for step in steps(load(path)):
                if "uses" in step:
                    self.assertRegex(step["uses"], PINNED, name)

    def test_no_checkout_inside_an_action(self):
        # A checkout into the workspace would wipe the action's own files under `uses: ./`.
        for name, path in ACTIONS.items():
            for step in steps(load(path)):
                self.assertNotIn("actions/checkout", step.get("uses", ""), name)

    def test_inputs_are_documented_and_used(self):
        for name, path in ACTIONS.items():
            action = load(path)
            self.assertTrue(action.get("name") and action.get("description"), name)
            inputs = action.get("inputs") or {}
            text = path.read_text()
            for key, spec in inputs.items():
                self.assertTrue(str(spec.get("description", "")).strip(), f"{name}: {key} has no description")
                self.assertIsInstance(spec.get("default"), str, f"{name}: {key} needs a string default")
                self.assertIn(f"inputs.{key}", text, f"{name}: {key} is never used")
            for ref in INPUT_REF.findall(text):
                self.assertIn(ref, inputs, f"{name}: inputs.{ref} is not declared")

    def test_if_conditions_compare_inputs_as_strings(self):
        for name, path in ACTIONS.items():
            for step in steps(load(path)):
                cond = str(step.get("if", ""))
                self.assertIsNone(BARE_INPUT_IN_IF.search(cond), f"{name}: {cond}")


class FfmpegInstallConditionTest(unittest.TestCase):
    """Each grep runs before Python's packages exist, so it stays literal and is checked here."""

    GREP = re.compile(r"grep -qiE '\\\.\(([a-z0-9|]+)\)\$'")

    def test_grep_lists_exactly_the_extensions_that_need_ffmpeg(self):
        for name in ("action.yml", "audit/action.yml"):
            text = "\n".join(s["run"] for s in steps(load(ACTIONS[name])) if "run" in s)
            found = self.GREP.findall(text)
            self.assertEqual(len(found), 1, f"{name}: expected one extension grep")
            self.assertEqual({f".{e}" for e in found[0].split("|")}, AUDIO_EXTS | VIDEO_EXTS, name)


class CallersTest(unittest.TestCase):
    def test_ci_keeps_the_required_check_names(self):
        jobs = load(ROOT / ".github" / "workflows" / "ci.yml")["jobs"]
        names = {job.get("name", job_id) for job_id, job in jobs.items()}
        self.assertLessEqual({"test", "gate / slop-gate", "audit / slop-audit"}, names)

    def test_ci_runs_the_local_actions(self):
        jobs = load(ROOT / ".github" / "workflows" / "ci.yml")["jobs"]
        used = {s.get("uses") for job in jobs.values() for s in job.get("steps", [])}
        self.assertLessEqual({"./", "./audit", "./judge"}, used)

    def test_example_gate_keeps_the_ruleset_check_name(self):
        jobs = load(ROOT / "examples" / ".github" / "workflows" / "slop-gate.yml")["jobs"]
        self.assertEqual(jobs["slop-gate"]["name"], "slop-gate / slop-gate")
        self.assertIn("mikejmckinney/slop-gate@v1", [s.get("uses") for s in jobs["slop-gate"]["steps"]])


if __name__ == "__main__":
    unittest.main()
