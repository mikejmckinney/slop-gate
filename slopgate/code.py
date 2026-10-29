"""Code checks: hidden Unicode, generation residue, Ruff, and Biome."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import unicodedata
from pathlib import Path, PurePosixPath

from .findings import Finding
from .gitdiff import ChangedLines, in_scope

CODE_EXTS = {
    ".py",
    ".pyi",
    ".js",
    ".mjs",
    ".cjs",
    ".jsx",
    ".ts",
    ".mts",
    ".cts",
    ".tsx",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".cs",
    ".rb",
    ".php",
    ".swift",
    ".c",
    ".h",
    ".cc",
    ".cpp",
    ".hpp",
    ".sh",
    ".bash",
    ".ps1",
    ".lua",
    ".sql",
    ".yml",
    ".yaml",
    ".toml",
    ".json",
    ".html",
    ".css",
    ".scss",
    ".vue",
    ".svelte",
    ".gd",
    ".tf",
    ".bicep",
}
PROSE_EXTS = {".md", ".mdx", ".txt", ".rst"}
# Markup and data files legitimately carry no-break spaces (typography, i18n
# strings), so an exotic space there is advice rather than a block.
SOFT_SPACE_EXTS = {".html", ".vue", ".svelte", ".json", ".yml", ".yaml", ".toml", ".css", ".scss", ".sql"}
JS_EXTS = {".js", ".mjs", ".cjs", ".jsx", ".ts", ".mts", ".cts", ".tsx"}
BINARY_SNIFF = 8192

# ---------------------------------------------------------------- unicode

# Cyrillic and Greek letters that render like Latin ones. A word mixing these
# with Latin letters is how homoglyph attacks spell `paypal` or `isAdmin`.
CONFUSABLES = set(
    "аАвВеЕкКмМнНоОрРсСтТуУхХіІјЈѕЅԁԛԝүһӏ"  # Cyrillic
    "οΟαΑβΒεΕζΖηΗιΙκΚΜνΝρΡτΤυΥχΧ"  # Greek
)
_WORD = re.compile(r"[^\W\d_][\w]*")


def _is_emoji_context(prev: str) -> bool:
    if not prev:
        return False
    cat = unicodedata.category(prev)
    return cat in ("So", "Sk", "Sm") or prev in "0123456789#*" or ord(prev) > 0x2000


def scan_unicode_line(text: str, strict_spaces: bool) -> list[tuple[str, str, str]]:
    """Return (severity, rule, message) tuples for one line of text."""
    hits: list[tuple[str, str, str]] = []
    hidden: list[str] = []
    spaces: list[str] = []
    prev = ""
    vs_run = 0
    for ch in text:
        cp = ord(ch)
        cat = unicodedata.category(ch)
        name = unicodedata.name(ch, f"U+{cp:04X}")
        label = f"U+{cp:04X} {name}"
        is_vs = 0xFE00 <= cp <= 0xFE0F or 0xE0100 <= cp <= 0xE01EF
        vs_run = vs_run + 1 if is_vs else 0
        if is_vs:
            # Emoji presentation selectors after a symbol are normal; anything
            # else (or a run of them) is the steganography pattern.
            if cp in (0xFE0E, 0xFE0F) and vs_run == 1 and _is_emoji_context(prev):
                pass
            else:
                hidden.append(label)
        elif cat == "Cf":
            if cp == 0x200D and _is_emoji_context(prev):
                pass  # zero-width joiner inside an emoji sequence
            elif cp == 0xFEFF and not prev and not hits:
                pass  # byte-order mark; checked per file, not per line
            else:
                hidden.append(label)
        elif cat in ("Zl", "Zp"):
            hidden.append(label)
        elif cat == "Zs" and ch != " ":
            spaces.append(label)
        if not is_vs and not (cat == "Cf" and cp == 0x200D):
            prev = ch
    if hidden:
        hits.append(
            (
                "error",
                "hidden-character",
                f"Invisible character(s) {', '.join(sorted(set(hidden)))}. They change what "
                "the code or prompt means without showing up in the diff.",
            )
        )
    if spaces:
        hits.append(
            (
                "error" if strict_spaces else "warning",
                "exotic-space",
                f"Non-ASCII space {', '.join(sorted(set(spaces)))}. Replace it with a normal space.",
            )
        )
    for word in _WORD.findall(text):
        if any(c in CONFUSABLES for c in word) and any("a" <= c.lower() <= "z" for c in word):
            bad = sorted({f"'{c}' U+{ord(c):04X}" for c in word if c in CONFUSABLES})
            hits.append(
                (
                    "error",
                    "homoglyph",
                    f"'{word}' mixes Latin letters with look-alike {', '.join(bad)}. "
                    "Retype the word in plain ASCII.",
                )
            )
    return hits


# ---------------------------------------------------------------- residue

RESIDUE_RULES: list[tuple[str, str, re.Pattern[str], str]] = [
    (
        "truncation-marker",
        "error",
        re.compile(
            r"\.\.\.\s*\(?(?:the )?(?:rest of|remaining|existing|other|previous|same as before)"
            r"(?: the)? (?:code|implementation|logic|methods?|functions?|file|content)",
            re.I,
        ),
        "A model truncation marker: the code it stands for is missing. Write the code out.",
    ),
    (
        "placeholder-secret",
        "error",
        re.compile(
            r"\b(?:your|my)[_\- ]?(?:openai[_\- ]?)?(?:api[_\- ]?key|token|secret|password)"
            r"[_\- ]?here\b|\bYOUR_API_KEY\b|<your[_\- ]api[_\- ]key>",
            re.I,
        ),
        "Placeholder credential. Read it from an environment variable or secret store.",
    ),
    (
        "ai-banner",
        "error",
        re.compile(
            r"\b(?:generated|written|created|authored)\s+(?:by|with|using)\s+(?:chat ?gpt|gpt-?\d[\w.]*|"
            r"claude(?: code)?|copilot|gemini|grok|cursor|an? ai\b|ai assistant)",
            re.I,
        ),
        "AI attribution banner in source. Remove it; authorship lives in git history.",
    ),
    (
        "assistant-residue",
        "error",
        re.compile(
            r"\bas an ai (?:language model|model|assistant|chatbot|system)\b|"
            r"\bas an ai (?:developed|created|trained|built|made) by\b|\bas an ai, i\b|"
            r"^\s*(?:#|//|/\*|\*)\s*(?:here'?s|here is) "
            r"(?:the|an?|your) (?:updated|complete|full|revised|fixed|modified)",
            re.I,
        ),
        "Chat-assistant text pasted into source. Delete it.",
    ),
    (
        "stub-admission",
        "error",
        re.compile(
            r"\b(?:TODO|FIXME)\b[:\s-]*(?:implement (?:this|the actual|real)|add (?:actual|real) "
            r"(?:logic|implementation)|replace with (?:actual|real))|\bplaceholder "
            r"(?:implementation|logic|code|function)\b",
            re.I,
        ),
        "A stub left where real logic belongs. Implement it, or open an issue and fail loudly "
        "(raise/throw) instead of returning a dummy value.",
    ),
    (
        "hedged-implementation",
        "warning",
        re.compile(
            r"\bin a (?:real|production)[- ](?:app|application|implementation|system|world|"
            r"scenario|environment)\b|\bthis is a (?:simplified|mock|dummy|naive|basic) "
            r"(?:implementation|version|example)\b",
            re.I,
        ),
        "The comment admits the code is not the real thing. Make it real, or say exactly "
        "what is missing and link the issue.",
    ),
]


# Residue rules can be silenced per line; Unicode rules cannot, because an
# attacker could add the same comment.
SUPPRESS = "slop-gate: ignore"


def _read_text(path: Path) -> str | None:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\0" in raw[:BINARY_SNIFF]:
        return None
    return raw.decode("utf-8", errors="replace")


def scan_files(repo: Path, changed: ChangedLines, excluded) -> tuple[list[Finding], int]:
    findings: list[Finding] = []
    scanned = 0
    for rel in sorted(changed):
        if excluded(rel):
            continue
        suffix = PurePosixPath(rel).suffix.lower()
        is_code = suffix in CODE_EXTS
        if not (is_code or suffix in PROSE_EXTS):
            continue
        text = _read_text(repo / rel)
        if text is None:
            continue
        scanned += 1
        for n, line in enumerate(text.splitlines(), start=1):
            if not in_scope(changed, rel, n):
                continue
            for sev, rule, msg in scan_unicode_line(line, is_code and suffix not in SOFT_SPACE_EXTS):
                findings.append(Finding("unicode", rule, sev, rel, n, msg))
            if is_code and SUPPRESS not in line:
                for rule, sev, pattern, msg in RESIDUE_RULES:
                    if pattern.search(line):
                        findings.append(Finding("residue", rule, sev, rel, n, msg))
    return findings, scanned


# ---------------------------------------------------------------- ruff

# AI-typical failure modes Ruff can see. By default these are the only rules
# run, so the gate doesn't enforce style choices the repo never adopted; set
# `code.ruff_select: repo` to run them on top of the repo's own selection.
RUFF_EXTRA = [
    "F",  # unused imports/variables, undefined names
    "E9",  # syntax errors
    "BLE001",  # blind `except Exception`
    "S110",  # try/except/pass
    "S112",  # try/except/continue
    "ERA001",  # commented-out code
    "PIE790",  # unnecessary `pass`
    "B006",  # mutable default argument
    "B904",  # re-raise without `from`
    "RET504",  # assign-then-return
]
RUFF_FIX = {
    "F401": "Delete the unused import.",
    "F841": "Delete the unused variable or use it.",
    "BLE001": "Catch the specific exception you expect, and handle or re-raise it.",
    "S110": "Don't swallow the error. Log it with context or re-raise.",
    "S112": "Don't silently skip. Log why the item was skipped.",
    "ERA001": "Delete commented-out code; git keeps the history.",
}


def _ruff() -> str | None:
    return os.environ.get("SLOPGATE_RUFF") or shutil.which("ruff")


def ruff_check(
    repo: Path,
    changed: ChangedLines,
    excluded,
    format_level: str,
    select: str = "slop-gate",
    format_scope: str = "added",
) -> tuple[list[Finding], int]:
    files = [
        p
        for p in sorted(changed)
        if PurePosixPath(p).suffix in (".py", ".pyi") and not excluded(p) and (repo / p).is_file()
    ]
    if not files:
        return [], 0
    ruff = _ruff()
    if ruff is None:
        raise RuntimeError("ruff is not installed")
    findings: list[Finding] = []
    result = subprocess.run(
        [
            ruff,
            "check",
            "--no-fix",
            "--output-format=json",
            "--select" if select == "slop-gate" else "--extend-select",
            ",".join(RUFF_EXTRA),
            "--force-exclude",
            *files,
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(f"ruff check failed: {result.stderr.strip()[:2000]}")
    for d in json.loads(result.stdout or "[]"):
        rel = PurePosixPath(Path(d["filename"]).resolve().relative_to(repo.resolve())).as_posix()
        line = d["location"]["row"]
        if in_scope(changed, rel, line):
            code = d.get("code") or "syntax"
            findings.append(
                Finding(
                    "ruff", code, "error", rel, line, d["message"].rstrip(".") + ".", RUFF_FIX.get(code, "")
                )
            )
    if format_level != "off":
        findings += _ruff_format(ruff, repo, changed, files, format_level, format_scope)
    return findings, len(files)


def _ruff_format(
    ruff: str, repo: Path, changed: ChangedLines, files: list[str], level: str, scope: str
) -> list[Finding]:
    result = subprocess.run(
        [ruff, "format", "--diff", "--force-exclude", *files],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        return []  # syntax errors already reported by ruff check
    findings = []
    current = None
    for line in result.stdout.splitlines():
        if line.startswith("--- "):
            current = line[4:].strip()
        elif line.startswith("@@") and current:
            m = re.match(r"@@ -(\d+)(?:,(\d+))?", line)
            start, count = int(m.group(1)), int(m.group(2) or 1)
            rel = PurePosixPath(Path(current)).as_posix()
            hunk = range(start, start + max(count, 1))
            hit = next((n for n in hunk if in_scope(changed, rel, n)), None)
            if hit:
                # With scope "added", blocking on an old, never-formatted file
                # would force a whole-file reformat into an unrelated PR, so
                # only files the PR adds get the configured level and edits
                # get advice. Scope "changed" applies the level to edits too.
                strict = scope == "changed" or _is_new_file(repo, changed, rel)
                severity = level if strict else "warning"
                findings.append(
                    Finding(
                        "ruff",
                        "format",
                        severity,
                        rel,
                        hit,
                        "This code is not in `ruff format` style.",
                        f"Run `ruff format {rel}`.",
                    )
                )
    return findings


def _is_new_file(repo: Path, changed: ChangedLines, rel: str) -> bool:
    lines = changed.get(rel)
    if lines is None:
        return True
    text = _read_text(repo / rel) or ""
    return len(lines) >= len(text.splitlines())


# ---------------------------------------------------------------- biome

BIOME_FIX = {
    "noUnusedImports": "Delete the unused import.",
    "noUnusedVariables": "Delete the unused variable or use it.",
    "noEmptyBlockStatements": "An empty block (often a `catch {}`) hides failures. Handle or log it.",
    "noUselessCatch": "A catch that only rethrows adds nothing. Remove the try/catch.",
    "noUnreachable": "Delete the unreachable code.",
}


def biome_check(repo: Path, changed: ChangedLines, excluded, config: Path) -> tuple[list[Finding], int]:
    files = [
        p
        for p in sorted(changed)
        if PurePosixPath(p).suffix in JS_EXTS and not excluded(p) and (repo / p).is_file()
    ]
    if not files:
        return [], 0
    biome = os.environ.get("SLOPGATE_BIOME") or shutil.which("biome")
    if biome is None:
        raise RuntimeError("biome is not installed")
    findings: list[Finding] = []
    for i in range(0, len(files), 200):
        result = subprocess.run(
            [
                biome,
                "lint",
                f"--config-path={config}",
                "--reporter=json",
                "--max-diagnostics=none",
                "--diagnostic-level=warn",
                *files[i : i + 200],
            ],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        out = result.stdout.strip()
        if not out.startswith("{"):
            raise RuntimeError(f"biome failed: {(result.stderr or out)[:2000]}")
        for d in json.loads(out).get("diagnostics", []):
            loc = d.get("location") or {}
            if not loc.get("path") or not loc.get("start"):
                continue
            rel = PurePosixPath(loc["path"]).as_posix()
            line = loc["start"]["line"]
            rule = d["category"].rsplit("/", 1)[-1]
            if in_scope(changed, rel, line):
                sev = "error" if d["severity"] in ("error", "fatal") else "warning"
                findings.append(Finding("biome", rule, sev, rel, line, d["message"], BIOME_FIX.get(rule, "")))
    return findings, len(files)
