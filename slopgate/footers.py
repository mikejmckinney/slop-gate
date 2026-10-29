"""Remove tool attribution footers from PR descriptions, issue bodies and comments.

Some agent tooling appends a footer to everything it writes through GitHub and
puts it back on every edit. These helpers strip it with the workflow's token,
whose edits don't start new workflow runs, so nothing here can loop.

Run as `python -m slopgate.footers strip|backfill`. It needs only the standard
library and the gh CLI, so a workflow can run it without installing anything.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field

from .prtext import strip_footers

# Only text written by people with write access is edited; anyone else's
# words are theirs to keep.
TRUSTED = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
REPORT_MARKERS = ("<!-- slop-gate-report -->", "<!-- slop-judge-report -->")

# Backfill sources: (label, list endpoint, path for one item).
SOURCES = [
    ("PR description", "pulls?state=all&per_page=100", "pulls/{number}"),
    ("issue body", "issues?state=all&per_page=100", "issues/{number}"),
    ("issue or PR comment", "issues/comments?per_page=100", "issues/comments/{id}"),
    ("review comment", "pulls/comments?per_page=100", "pulls/comments/{id}"),
]


class GhClient:
    """GitHub REST calls through the gh CLI, which reads GH_TOKEN."""

    def __init__(self, repo: str):
        self.repo = repo

    def _api(self, *args: str, stdin: str | None = None) -> str:
        result = subprocess.run(
            ["gh", "api", *args], input=stdin, capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            raise RuntimeError(f"gh api {' '.join(args)} failed: {result.stderr.strip()[:500]}")
        return result.stdout

    def get(self, path: str) -> dict:
        return json.loads(self._api(f"repos/{self.repo}/{path}"))

    def list(self, path: str) -> list[dict]:
        pages = json.loads(self._api("--paginate", "--slurp", f"repos/{self.repo}/{path}"))
        return [item for page in pages for item in page]

    def set_body(self, path: str, body: str) -> None:
        self._api(
            "--method", "PATCH", f"repos/{self.repo}/{path}", "--input", "-", stdin=json.dumps({"body": body})
        )


def decide(item: dict) -> tuple[str | None, str]:
    """The new body for one PR, issue or comment, or None and why it stays."""
    body = item.get("body") or ""
    if any(body.lstrip().startswith(m) for m in REPORT_MARKERS):
        return None, "slop-gate report"
    association = item.get("author_association", "NONE")
    if association not in TRUSTED:
        return None, f"author is {association.lower()}"
    new, removed = strip_footers(body)
    if not removed:
        return None, "no footer"
    return new, f"{removed} footer line(s)"


@dataclass
class Result:
    path: str
    label: str
    url: str
    new_body: str | None  # None when the text stays as it is
    reason: str
    body: str = ""  # the text after this run, edited or not


def strip_one(client: GhClient, path: str, label: str = "item", apply: bool = True) -> Result:
    item = client.get(path)
    new, reason = decide(item)
    if new is not None and apply:
        client.set_body(path, new)
    final = new if new is not None and apply else (item.get("body") or "")
    return Result(path, label, item.get("html_url", path), new, reason, final)


@dataclass
class Backfill:
    results: list[Result] = field(default_factory=list)

    def counts(self) -> dict[str, Counter]:
        out: dict[str, Counter] = {}
        for r in self.results:
            c = out.setdefault(r.label, Counter())
            c["scanned"] += 1
            if r.new_body is not None:
                c["with footer"] += 1
            elif r.reason.startswith("author is"):
                c["skipped (author without write access)"] += 1
        return out

    def summary(self, applied: bool) -> str:
        verb = "Edited" if applied else "Would edit"
        lines = [
            f"## Attribution footer backfill ({'applied' if applied else 'dry run'})",
            "",
            f"| Kind | Scanned | {verb} | Skipped: author without write access |",
            "|---|---|---|---|",
        ]
        total = 0
        for label, _, _ in SOURCES:
            c = self.counts().get(label, Counter())
            total += c["with footer"]
            lines.append(
                f"| {label} | {c['scanned']} | {c['with footer']} | "
                f"{c['skipped (author without write access)']} |"
            )
        lines += ["", f"{verb} {total} item(s) in total."]
        if not applied and total:
            lines.append("Run again with `apply` set to edit them.")
        return "\n".join(lines) + "\n"


def backfill(client: GhClient, apply: bool) -> Backfill:
    out = Backfill()
    for label, listing, one in SOURCES:
        for item in client.list(listing):
            # The issues endpoint lists PRs too; their descriptions come from pulls.
            if label == "issue body" and "pull_request" in item:
                continue
            path = one.format(number=item.get("number"), id=item.get("id"))
            new, reason = decide(item)
            if new is not None and apply:
                client.set_body(path, new)
            out.results.append(Result(path, label, item.get("html_url", path), new, reason))
    return out


def event_path(event_name: str, payload: dict) -> tuple[str, str] | None:
    """The API path and label for the text an event created or edited."""
    if event_name == "issue_comment":
        return f"issues/comments/{payload['comment']['id']}", "issue or PR comment"
    if event_name == "pull_request_review_comment":
        return f"pulls/comments/{payload['comment']['id']}", "review comment"
    if event_name == "issues":
        return f"issues/{payload['issue']['number']}", "issue body"
    if event_name == "pull_request":
        return f"pulls/{payload['pull_request']['number']}", "PR description"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m slopgate.footers", description=__doc__)
    parser.add_argument("action", choices=["strip", "backfill"])
    parser.add_argument("--repo", required=True, help="owner/name")
    target = parser.add_mutually_exclusive_group()
    target.add_argument(
        "--api-path", help="strip: pulls/N, issues/N, issues/comments/ID or pulls/comments/ID"
    )
    target.add_argument("--event", action="store_true", help="strip: the item the triggering event names")
    parser.add_argument("--body-out", help="strip: write the item's text, after any edit, to this file")
    parser.add_argument("--apply", action="store_true", help="backfill: edit; without it, only count")
    parser.add_argument("--summary-md", help="backfill: write the Markdown summary here")
    args = parser.parse_args(argv)
    client = GhClient(args.repo)
    try:
        return _run(args, client)
    except (RuntimeError, ValueError, KeyError) as exc:
        print(f"::error title=slop-gate footers::{exc}", file=sys.stderr)
        return 2


def _run(args: argparse.Namespace, client: GhClient) -> int:
    if args.action == "backfill":
        result = backfill(client, apply=args.apply)
        text = result.summary(applied=args.apply)
        if args.summary_md:
            with open(args.summary_md, "w", encoding="utf-8") as fh:
                fh.write(text)
        print(text)
        return 0
    if args.event:
        with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as fh:
            payload = json.load(fh)
        target = event_path(os.environ["GITHUB_EVENT_NAME"], payload)
        if target is None:
            print(f"Nothing to strip for a {os.environ['GITHUB_EVENT_NAME']} event.")
            return 0
        path, label = target
    elif args.api_path:
        path, label = args.api_path, "item"
    else:
        raise ValueError("strip needs --api-path or --event")
    result = strip_one(client, path, label, apply=True)
    if args.body_out:
        with open(args.body_out, "w", encoding="utf-8") as fh:
            fh.write(result.body)
    if result.new_body is not None:
        print(f"::notice title=slop-gate::Removed {result.reason} from {result.label} {result.url}.")
    else:
        print(f"Left {result.label} {result.url} unchanged: {result.reason}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
