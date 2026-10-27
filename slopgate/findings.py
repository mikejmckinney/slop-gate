"""Finding model and the three outputs a PR author sees: annotations, a
Markdown report, and the process exit code."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass

SEVERITIES = ("error", "warning")

# What each check family is for, shown once per section in the report.
SECTION_HELP = {
    "prose": "Writing patterns from the house style guide. Rewrite the sentence; "
    "a word swap rarely fixes the underlying pattern.",
    "pr-text": "The PR title, body, and commit messages are prose too.",
    "unicode": "Invisible or look-alike characters. They hide text from reviewers "
    "while compilers and LLM tokenizers still read it.",
    "residue": "Leftovers from code generation: truncation markers, stub admissions, "
    "placeholder secrets, AI banners.",
    "ruff": "Python lint (Ruff) on changed lines. Rule docs: https://docs.astral.sh/ruff/rules/",
    "biome": "JS/TS lint (Biome) on changed lines. Rule docs: https://biomejs.dev/linter/",
    "media": "Image and audio checks against the media manifest in .slop-gate.yml.",
}


@dataclass(frozen=True)
class Finding:
    check: str  # family: prose, pr-text, unicode, residue, ruff, biome, media
    rule: str  # specific rule id, e.g. SlopGate.ContrastFormula or F401
    severity: str  # error blocks the PR; warning is reported only
    path: str
    line: int | None
    message: str
    fix: str = ""  # recommendation; empty when the message already says how

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError(f"unknown severity {self.severity!r} for {self.rule}")


def apply_overrides(findings: list[Finding], overrides: dict[str, str]) -> list[Finding]:
    """Re-level or drop findings by rule id (``off`` drops the finding)."""
    out = []
    for f in findings:
        level = overrides.get(f.rule)
        if level == "off":
            continue
        if level in SEVERITIES and level != f.severity:
            f = Finding(**{**asdict(f), "severity": level})
        out.append(f)
    return out


def _escape_annotation(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def annotations(findings: list[Finding], limit: int = 50) -> str:
    """GitHub workflow commands. GitHub shows at most ~50 per job, so errors go first."""
    ordered = sorted(findings, key=lambda f: (f.severity != "error", f.path, f.line or 0))
    lines = []
    for f in ordered[:limit]:
        loc = f"file={f.path}" + (f",line={f.line}" if f.line else "")
        body = f.message + (f" Fix: {f.fix}" if f.fix else "")
        lines.append(f"::{f.severity} {loc},title={f.rule}::{_escape_annotation(body)}")
    return "\n".join(lines)


def _md_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def markdown_report(findings: list[Finding], checked: dict[str, int], head_sha: str = "") -> str:
    errors = [f for f in findings if f.severity == "error"]
    warnings = [f for f in findings if f.severity == "warning"]
    status = "❌ Blocked" if errors else "✅ Passed"
    scope = ", ".join(f"{n} {k}" for k, n in checked.items() if n) or "nothing in scope"
    out = [
        "<!-- slop-gate-report -->",
        f"## slop-gate: {status}",
        "",
        f"{len(errors)} blocking, {len(warnings)} advisory. Checked {scope}"
        + (f" at `{head_sha[:7]}`." if head_sha else "."),
    ]
    if not findings:
        return "\n".join(out) + "\n"
    out += [
        "",
        "Errors block the merge; warnings are advice. Fix the errors, push, and this "
        "report updates. To accept a finding on purpose, see "
        "[suppressing findings](https://github.com/mikejmckinney/slop-gate#suppressing-a-finding).",
    ]
    by_check: dict[str, list[Finding]] = defaultdict(list)
    for f in findings:
        by_check[f.check].append(f)
    for check in sorted(by_check, key=lambda c: (all(f.severity == "warning" for f in by_check[c]), c)):
        items = sorted(by_check[check], key=lambda f: (f.severity != "error", f.path, f.line or 0))
        n_err = sum(f.severity == "error" for f in items)
        out += ["", f"### {check} ({n_err} blocking, {len(items) - n_err} advisory)", ""]
        if check in SECTION_HELP:
            out += [f"_{SECTION_HELP[check]}_", ""]
        out += ["| | Location | Rule | Problem and fix |", "|---|---|---|---|"]
        for f in items[:200]:
            icon = "🛑" if f.severity == "error" else "⚠️"
            loc = f"`{f.path}`" + (f":{f.line}" if f.line else "")
            text = f.message + (f" **Fix:** {f.fix}" if f.fix else "")
            out.append(f"| {icon} | {loc} | `{f.rule}` | {_md_cell(text)} |")
        if len(items) > 200:
            out.append(f"\n…and {len(items) - 200} more in the JSON artifact.")
    return "\n".join(out) + "\n"


def to_json(findings: list[Finding]) -> str:
    return json.dumps([asdict(f) for f in findings], indent=2)


def exit_code(findings: list[Finding]) -> int:
    return 1 if any(f.severity == "error" for f in findings) else 0
