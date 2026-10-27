"""Run the CLI against a throwaway git repo, the way the workflow does."""

import io
import json
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from slopgate.cli import main
from slopgate.config import Config, load
from slopgate.gitdiff import parse_unified_diff
from slopgate.prtext import check_commits, check_pr

ROOT = Path(__file__).resolve().parent.parent
HAS_VALE = shutil.which("vale") is not None


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


class DiffParsingTests(unittest.TestCase):
    def test_added_lines_binary_and_deletions(self):
        diff = "\n".join(
            [
                "diff --git a/a.md b/a.md",
                "--- a/a.md",
                "+++ b/a.md",
                "@@ -1,0 +2,2 @@",
                "+one",
                "+two",
                "@@ -9 +11 @@",
                "-old",
                "+new",
                "diff --git a/gone.md b/gone.md",
                "--- a/gone.md",
                "+++ b/gone.md",
                "@@ -3,2 +2,0 @@",
                "diff --git a/s.png b/s.png",
                "Binary files /dev/null and b/s.png differ",
            ]
        )
        self.assertEqual(parse_unified_diff(diff), {"a.md": {2, 3, 11}, "s.png": None})


class ConfigTests(unittest.TestCase):
    def test_excludes_match_at_any_depth(self):
        cfg = Config()
        self.assertTrue(cfg.excluded("node_modules/x/index.js"))
        self.assertTrue(cfg.excluded("web/node_modules/x.js"))
        self.assertFalse(cfg.excluded("src/index.js"))

    def test_unknown_key_fails_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, ".slop-gate.yml").write_text("exlude: []\n")
            with self.assertRaises(ValueError):
                load(Path(tmp))


@unittest.skipUnless(HAS_VALE, "vale not installed")
class PrTextTests(unittest.TestCase):
    vale_ini = ROOT / ".vale.ini"

    def test_commit_trailer_is_reported_at_configured_level(self):
        msg = "Fix cache key\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\n"
        found = check_commits(self.vale_ini, [("abc1234def", msg)], "warning")
        self.assertEqual([(f.rule, f.severity) for f in found], [("attribution-trailer", "warning")])
        self.assertEqual(check_commits(self.vale_ini, [("abc1234def", msg)], "off"), [])

    def test_pr_footer_blocks(self):
        footer = (
            "\U0001f916 Generated with [Claude Code](https://claude.com/claude-code)"  # slop-gate: ignore
        )
        body = f"Adds retry.\n\n{footer}\n"
        found = check_pr(self.vale_ini, "Add retry", body)
        self.assertIn(("attribution-footer", "error"), {(f.rule, f.severity) for f in found})


@unittest.skipUnless(HAS_VALE, "vale not installed")
class ProseFixtureTests(unittest.TestCase):
    def test_every_rule_has_a_fixture_that_fires(self):
        from slopgate.prose import run_vale

        rules = {p.stem for p in (ROOT / "styles" / "SlopGate").glob("*.yml")}
        fixtures = {p.stem for p in (ROOT / "tests" / "fixtures" / "prose").glob("*.md")}
        self.assertEqual(
            rules, fixtures, "each Vale rule needs a fixture; a rule that matches nothing fails silently"
        )
        for name in sorted(rules):
            with self.subTest(rule=name):
                found = run_vale(ROOT, ROOT / ".vale.ini", [f"tests/fixtures/prose/{name}.md"])
                self.assertIn(f"SlopGate.{name}", {f.rule for f in found})

    def test_clean_writing_passes(self):
        from slopgate.prose import run_vale

        self.assertEqual(run_vale(ROOT, ROOT / ".vale.ini", ["tests/fixtures/clean.md"]), [])


@unittest.skipUnless(HAS_VALE, "vale not installed")
class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.repo)
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "t@example.com")
        git(self.repo, "config", "user.name", "t")
        # Pre-existing debt on main must not block a PR that doesn't touch it.
        (self.repo / "README.md").write_text("# Demo\n\nIt's important to note that this is old text.\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "base")
        git(self.repo, "checkout", "-qb", "feature")
        with (self.repo / "README.md").open("a") as f:
            f.write("\nThis isn't just a demo, it's a platform.\n")
        (self.repo / "app.py").write_text(
            "import os\n\n\ndef run():\n    try:\n        return 1\n    except Exception:\n        pass\n"
        )
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "feature")

    def run_cli(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        report = self.repo / "report.json"
        with redirect_stdout(out), redirect_stderr(err):
            code = main(
                [
                    "check",
                    "--repo",
                    str(self.repo),
                    "--base",
                    "main",
                    "--head",
                    "feature",
                    "--only",
                    "prose,code",
                    "--report-json",
                    str(report),
                    *extra,
                ]
            )
        return code, json.loads(report.read_text()), out.getvalue()

    def test_only_changed_lines_are_reported_and_errors_block(self):
        code, findings, report = self.run_cli()
        self.assertEqual(code, 1)
        rules = {(f["path"], f["rule"]) for f in findings}
        self.assertIn(("README.md", "SlopGate.ContrastFormula"), rules)
        self.assertNotIn(("README.md", "SlopGate.HedgingPreambles"), rules)  # untouched line
        self.assertIn(("app.py", "F401"), rules)
        self.assertIn(("app.py", "S110"), rules)
        self.assertIn("**Fix:**", report)

    def test_override_can_turn_a_rule_off(self):
        (self.repo / ".slop-gate.yml").write_text(
            "overrides:\n  SlopGate.ContrastFormula: 'off'\n  F401: warning\n  S110: warning\n"
            "  BLE001: warning\n  format: 'off'\n"
        )
        code, findings, _ = self.run_cli()
        self.assertEqual(code, 0, findings)
        self.assertNotIn("SlopGate.ContrastFormula", {f["rule"] for f in findings})


if __name__ == "__main__":
    unittest.main()
