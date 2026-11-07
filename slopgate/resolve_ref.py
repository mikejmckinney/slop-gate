"""Pick the slop-gate commit that a reusable workflow should check out.

A called workflow has no context variable holding its own ref: the `github`
context belongs to the caller (actions/runner#2417). The caller's run does list
every reusable workflow it called, with the SHA each one resolved to, in
`referenced_workflows` of GET /repos/{repo}/actions/runs/{id}. Reading that
needs `actions: read` in the caller.

Rules, in order:
- An override (the `slop-gate-ref` input) always wins. When it names something
  other than the detected commit, a warning says so, except inside slop-gate's
  own repo, where CI passes the PR head on purpose and the detected commit is the
  merge commit.
- Otherwise the detected SHA is used.
- When the lookup fails or finds no single match, the result is the override,
  or `v1` without one, with one warning that says to grant `actions: read`.

The workflow runs this file directly from a one-file checkout, so it uses only
the standard library and runs on the runner's own python3. That checkout is at
the override or `v1`, since the commit isn't known yet, so a workflow pinned to an
older commit can run a newer copy of this file. Keep the interface stable: the
path, the one argument, the environment below and the `ref` output.

Usage: resolve_ref.py WORKFLOW_FILE
Environment: GITHUB_REPOSITORY, GITHUB_RUN_ID, GITHUB_RUN_ATTEMPT, GH_TOKEN,
SLOP_GATE_REF (the override, may be empty), GITHUB_OUTPUT.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field

SLOP_GATE_REPO = "mikejmckinney/slop-gate"
DEFAULT_REF = "v1"
FIX = (
    "Add `actions: read` to the permissions of the job that calls slop-gate so it runs "
    "the exact commit your `uses:` line resolved to."
)


class LookupFailed(Exception):
    """The caller's run could not be read."""


@dataclass
class Resolution:
    ref: str
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def find_entries(run: dict, workflow_file: str) -> list[dict]:
    """Entries in `referenced_workflows` for this slop-gate workflow file."""
    prefix = f"{SLOP_GATE_REPO}/.github/workflows/{workflow_file}@".lower()
    entries = run.get("referenced_workflows") or []
    return [e for e in entries if str(e.get("path", "")).lower().startswith(prefix) and e.get("sha")]


def written_ref(entry: dict) -> str:
    """The ref after the @ in the caller's `uses:` line."""
    return str(entry["path"]).rsplit("@", 1)[1]


def detect(run: dict, workflow_file: str) -> tuple[dict | None, str]:
    """The single matching entry, or None with the reason there isn't one."""
    entries = find_entries(run, workflow_file)
    if not entries:
        return None, f"the run lists no call to {SLOP_GATE_REPO}/.github/workflows/{workflow_file}"
    shas = {e["sha"] for e in entries}
    if len(shas) > 1:
        refs = ", ".join(sorted(written_ref(e) for e in entries))
        return None, f"the run calls {workflow_file} at more than one commit ({refs})"
    return entries[0], ""


def names_same_commit(override: str, entry: dict) -> bool:
    """True when the override is the detected SHA, a prefix of it, or the ref the caller wrote."""
    sha = entry["sha"].lower()
    value = override.strip().lower()
    if len(value) >= 7 and sha.startswith(value):
        return True
    if value == written_ref(entry).lower():
        return True
    full_ref = str(entry.get("ref") or "").lower()
    return bool(full_ref) and value in (
        full_ref,
        full_ref.removeprefix("refs/heads/"),
        full_ref.removeprefix("refs/tags/"),
    )


def resolve(workflow_file: str, override: str, caller_repo: str, fetch_run: Callable[[], dict]) -> Resolution:
    override = override.strip()
    local = caller_repo.lower() == SLOP_GATE_REPO

    try:
        entry, reason = detect(fetch_run(), workflow_file)
    except LookupFailed as exc:
        entry, reason = None, f"could not read this run ({exc})"

    if entry is None:
        if override:
            res = Resolution(override, notes=[f"Using slop-gate-ref {override}; {reason}."])
            if not local:
                res.warnings.append(f"slop-gate could not check its own version: {reason}. {FIX}")
            return res
        return Resolution(
            DEFAULT_REF,
            warnings=[
                f"slop-gate is running {DEFAULT_REF}, not the commit in your `uses:` line: {reason}. {FIX}"
            ],
        )

    sha = entry["sha"]
    found = f"{entry['path']} resolved to {sha}"
    if not override:
        return Resolution(sha, notes=[f"Using {sha}: {found}."])
    if names_same_commit(override, entry):
        # Same commit as the `uses:` line; check out the SHA so a ref that moves mid-run can't change it.
        return Resolution(sha, notes=[f"Using {sha}: slop-gate-ref {override} matches {found}."])
    res = Resolution(override, notes=[f"Using slop-gate-ref {override}; {found}."])
    if not local:
        res.warnings.append(
            f"slop-gate-ref is {override}, but {found}. The workflow YAML and the scripts it runs come from "
            "different commits. Remove slop-gate-ref to run the scripts that match your `uses:` line."
        )
    return res


def gh_fetch_run(repo: str, run_id: str, attempt: str) -> Callable[[], dict]:
    def fetch() -> dict:
        path = f"repos/{repo}/actions/runs/{run_id}"
        if attempt:
            path += f"/attempts/{attempt}"
        try:
            proc = subprocess.run(["gh", "api", path], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LookupFailed(str(exc)) from exc
        if proc.returncode != 0:
            detail = " ".join(proc.stderr.split()) or f"gh exited {proc.returncode}"
            raise LookupFailed(detail)
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise LookupFailed(f"bad JSON from the API: {exc}") from exc

    return fetch


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: resolve_ref.py WORKFLOW_FILE", file=sys.stderr)
        return 2
    env = os.environ
    repo = env.get("GITHUB_REPOSITORY", "")
    fetch = gh_fetch_run(repo, env.get("GITHUB_RUN_ID", ""), env.get("GITHUB_RUN_ATTEMPT", ""))
    res = resolve(argv[1], env.get("SLOP_GATE_REF", ""), repo, fetch)
    for note in res.notes:
        print(note)
    for warning in res.warnings:
        print(f"::warning title=slop-gate version::{warning}")
    out = env.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"ref={res.ref}\n")
    else:
        print(f"ref={res.ref}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
