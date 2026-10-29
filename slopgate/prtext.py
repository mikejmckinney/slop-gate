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
# A footer line holds nothing but the footer: optional emphasis and robot
# emoji, "Generated with/by", the tool name (plain or as a Markdown link). A
# sentence that mentions a footer, in inline code or prose, is not one.
_TOOLS = r"claude code|copilot|cursor|opencode|codex|chatgpt"
PR_FOOTER = re.compile(
    rf"^[ \t]*[_*]*[ \t]*(?:🤖[ \t]*)?generated (?:with|by)[ \t]+"
    rf"(?:\[(?:{_TOOLS})\]\([^)\s]*\)|(?:{_TOOLS}))[ \t]*[_*.]*[ \t]*$"
    r"|^[ \t]*https://claude\.ai/code/session_\w+[ \t]*$",
    re.I | re.M,
)
_FENCE = re.compile(r"```.*?```", re.S)
_RULE_LINE = re.compile(r"\s*(?:-{3,}|\*{3,}|_{3,})\s*")

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


def _split_footers(body: str) -> tuple[list[str], list[str]]:
    """The body's lines without footer lines, and the footer lines removed.

    Lines inside code fences stay, so a body that quotes a footer is left for
    a person.
    """
    kept: list[str] = []
    removed: list[str] = []
    in_fence = False
    for line in body.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        if not in_fence and PR_FOOTER.fullmatch(line):
            removed.append(line)
        else:
            kept.append(line)
    return kept, removed


def footer_lines(body: str) -> list[str]:
    """The lines strip_footers would remove."""
    return _split_footers(body)[1]


def strip_footers(body: str) -> tuple[str, int]:
    """Remove tool attribution footer lines from a PR description or comment.

    Returns the new body and the number of lines removed. When a footer is
    removed, the horizontal rules and blank lines left at the end of the body
    go too.
    """
    kept, removed = _split_footers(body)
    if not removed:
        return body, 0
    while kept and (not kept[-1].strip() or _RULE_LINE.fullmatch(kept[-1])):
        kept.pop()
    newline = "\r\n" if "\r\n" in body else "\n"
    return newline.join(kept) + (newline if kept else ""), len(removed)


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
