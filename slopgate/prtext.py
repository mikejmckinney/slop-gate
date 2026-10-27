"""PR title, PR body, and commit messages."""

from __future__ import annotations

import re
from pathlib import Path

from . import prose
from .findings import Finding

# Attribution lines agent harnesses add. Matching is on the line, so a PR that
# discusses these trailers in a code span is still flagged only if a whole line
# looks like one.
TRAILER = re.compile(
    r"^(?:co-authored-by:\s*.*(?:claude|anthropic|copilot|openai|chatgpt|gemini|grok|cursor|"
    r"opencode|codex|devin|\[bot\]).*|claude-session:\s*\S+|generated-by:\s*.+)\s*$",
    re.I | re.M,
)
PR_FOOTER = re.compile(
    r"^.*(?:🤖\s*)?generated (?:with|by) \[?(?:claude code|copilot|cursor|opencode|codex|chatgpt)\]?"
    r".*$|^https://claude\.ai/code/session_\w+\s*$",
    re.I | re.M,
)
_FENCE = re.compile(r"```.*?```", re.S)

TRAILER_FIX = (
    "Turn attribution off at the source (Claude Code: `attribution.commit` in "
    ".claude/settings.json) and squash-merge with the PR title and description as the "
    "message so commit bodies never reach the default branch."
)
FOOTER_FIX = "Delete the footer. For Claude Code, set `attribution.pr` in .claude/settings.json."


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def check_pr(vale_ini: Path, title: str, body: str) -> list[Finding]:
    findings = prose.check_text(vale_ini, "PR description", body)
    findings += prose.check_text(vale_ini, "PR title", title)
    for m in PR_FOOTER.finditer(body):
        findings.append(
            Finding(
                "pr-text",
                "attribution-footer",
                "error",
                "PR description",
                _line_of(body, m.start()),
                f"Tool attribution footer: '{m.group(0).strip()[:80]}'.",
                FOOTER_FIX,
            )
        )
    return findings


def check_commits(vale_ini: Path, commits: list[tuple[str, str]], trailer_level: str) -> list[Finding]:
    findings: list[Finding] = []
    for sha, message in commits:
        label = f"commit {sha[:7]}"
        if trailer_level != "off":
            for m in TRAILER.finditer(message):
                findings.append(
                    Finding(
                        "pr-text",
                        "attribution-trailer",
                        trailer_level,
                        label,
                        _line_of(message, m.start()),
                        f"Attribution trailer '{m.group(0).strip()[:80]}'.",
                        TRAILER_FIX,
                    )
                )
        # Lighter tier for commit messages: only the blocking assistant-residue rule.
        body = _FENCE.sub("", TRAILER.sub("", message))
        for f in prose.check_text(vale_ini, label, body):
            if f.rule in ("SlopGate.AssistantResidue", "SlopGate.PromptPlaceholders"):
                findings.append(f)
    return findings
