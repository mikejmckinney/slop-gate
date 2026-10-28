"""Prose checks through Vale, limited to the lines a PR changed."""

from __future__ import annotations

import configparser
import json
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

from .findings import Finding
from .gitdiff import ChangedLines, in_scope

_CHUNK = 200  # files per Vale invocation, to stay under argv limits


def vale_extensions(vale_ini: Path) -> set[str]:
    """File extensions the Vale config has a section for, e.g. {'.md', '.py'}."""
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    # Vale ini files allow keys before any section; give them one.
    parser.read_string("[__root__]\n" + vale_ini.read_text(encoding="utf-8"))
    exts: set[str] = set()
    for section in parser.sections():
        if section.startswith("*.{") and section.endswith("}"):
            exts.update("." + e.strip() for e in section[3:-1].split(","))
        elif section.startswith("*."):
            exts.add(section[1:])
    return exts


def run_vale(repo: Path, vale_ini: Path, files: list[str], kind: str = "prose") -> list[Finding]:
    if not files:
        return []
    vale = shutil.which("vale")
    if vale is None:
        raise RuntimeError("vale is not installed; the workflow installs it, locally see README")
    findings: list[Finding] = []
    pending = [files[i : i + _CHUNK] for i in range(0, len(files), _CHUNK)]
    while pending:
        chunk = pending.pop(0)
        result = subprocess.run(
            [vale, f"--config={vale_ini}", "--output=JSON", "--no-exit", *chunk],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip().startswith("{"):
            findings += _alerts(json.loads(result.stdout), repo, kind)
            continue
        # One unparseable file (bad YAML front matter, say) makes Vale abort
        # the whole batch. Retry the batch file by file so the rest still get
        # checked; a file that fails on its own is reported, not fatal.
        if len(chunk) > 1:
            pending[:0] = [[f] for f in chunk]
            continue
        error = _file_error(result, chunk[0])
        if error is None:
            raise RuntimeError(f"vale failed: {(result.stderr or result.stdout).strip()[:2000]}")
        findings.append(
            Finding(
                check=kind,
                rule="vale-unreadable",
                severity="warning",
                path=chunk[0],
                line=error.get("Line") or None,
                message=f"Vale could not read this file, so its prose was not checked: {error['Text']}.",
                fix="Fix the front matter (quote values that contain ': ') or exclude the file.",
            )
        )
    return findings


def _alerts(data: dict, repo: Path, kind: str) -> list[Finding]:
    out = []
    for path, alerts in data.items():
        rel = Path(path)
        rel = rel.relative_to(repo) if rel.is_absolute() else rel
        for a in alerts:
            if a["Severity"] not in ("error", "warning"):
                continue
            out.append(
                Finding(
                    check=kind,
                    rule=a["Check"],
                    severity=a["Severity"],
                    path=PurePosixPath(rel).as_posix(),
                    line=a["Line"],
                    message=a["Message"],
                )
            )
    return out


def _file_error(result: subprocess.CompletedProcess, path: str) -> dict | None:
    """Vale's error object when it blames this file's content; None otherwise.

    Errors about the config or the styles name another path and stay fatal.
    """
    for stream in (result.stderr, result.stdout):
        text = stream.strip()
        if not text.startswith("{"):
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("Text") and str(data.get("Path", "")).endswith(path):
            return data
    return None


def check(repo: Path, changed: ChangedLines, vale_ini: Path, excluded) -> tuple[list[Finding], int]:
    exts = vale_extensions(vale_ini)
    files = [
        p
        for p in sorted(changed)
        if PurePosixPath(p).suffix.lower() in exts and not excluded(p) and (repo / p).is_file()
    ]
    found = run_vale(repo, vale_ini, files)
    return [f for f in found if in_scope(changed, f.path, f.line)], len(files)


def check_text(vale_ini: Path, label: str, text: str) -> list[Finding]:
    """Lint a free-standing piece of text (PR body, commit message) as Markdown."""
    if not text.strip():
        return []
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp) / "text.md"
        tmp_path.write_text(text, encoding="utf-8")
        found = run_vale(Path(tmp), vale_ini, ["text.md"], kind="pr-text")
    return [Finding(f.check, f.rule, f.severity, label, f.line, f.message, f.fix) for f in found]
