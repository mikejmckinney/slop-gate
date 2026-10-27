"""Which files and lines a PR changed.

Checks only report on lines the PR added or modified, so a repo with existing
debt can adopt slop-gate without fixing everything first.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

# None means "the whole file is in scope" (new binary file, or --all mode).
ChangedLines = dict[str, set[int] | None]

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "core.quotePath=false", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def _unquote(path: str) -> str:
    # git still C-quotes paths with control characters or quotes.
    if path.startswith('"') and path.endswith('"'):
        return path[1:-1].encode("latin-1").decode("unicode_escape").encode("latin-1").decode("utf-8")
    return path


def parse_unified_diff(diff: str) -> ChangedLines:
    changed: ChangedLines = {}
    current: str | None = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            target = line[4:]
            current = None if target == "/dev/null" else _unquote(target)[2:]  # strip "b/"
            if current is not None:
                changed.setdefault(current, set())
        elif line.startswith("Binary files ") and " and b/" in line:
            path = line.split(" and b/", 1)[1].rsplit(" differ", 1)[0]
            changed[_unquote(path)] = None
        elif current is not None and (m := _HUNK.match(line)):
            start, count = int(m.group(1)), int(m.group(2) or 1)
            lines = changed[current]
            if lines is not None:
                lines.update(range(start, start + count))
    # Pure deletions and renames without edits leave empty sets; drop them.
    return {p: ls for p, ls in changed.items() if ls is None or ls}


def changed_lines(repo: Path, base: str, head: str) -> ChangedLines:
    merge_base = _git(repo, "merge-base", base, head).strip()
    diff = _git(
        repo,
        "diff",
        "--unified=0",
        "--no-color",
        "--no-ext-diff",
        "--find-renames",
        "--diff-filter=ACMR",
        merge_base,
        head,
    )
    return parse_unified_diff(diff)


def all_files(repo: Path) -> ChangedLines:
    return {p: None for p in _git(repo, "ls-files", "-z").split("\0") if p}


def commit_messages(repo: Path, base: str, head: str) -> list[tuple[str, str]]:
    merge_base = _git(repo, "merge-base", base, head).strip()
    raw = _git(repo, "log", "--format=%H%x00%B%x1e", f"{merge_base}..{head}")
    out = []
    for record in raw.split("\x1e"):
        record = record.strip("\n")
        if record:
            sha, _, body = record.partition("\0")
            out.append((sha, body))
    return out


def in_scope(changed: ChangedLines, path: str, line: int | None) -> bool:
    lines = changed.get(path, set())
    if lines is None:
        return True
    return line is not None and line in lines
