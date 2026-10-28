"""Command line entry point: `python -m slopgate check|media-init`."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path, PurePosixPath

from . import code, config, gitdiff, judge, media, media_init, prose, prtext
from .findings import Finding, annotations, apply_overrides, exit_code, markdown_report, to_json


def run_check(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    cfg = config.load(repo)
    if args.all:
        changed = gitdiff.all_files(repo)
    else:
        if not (args.base and args.head):
            raise SystemExit("--base and --head are required unless --all is given")
        changed = gitdiff.changed_lines(repo, args.base, args.head)
    only = set(args.only.split(",")) if args.only else None

    def enabled(name: str) -> bool:
        return cfg.checks.get(name, True) and (only is None or name in only)

    findings: list[Finding] = []
    checked: dict[str, int] = {}
    if enabled("prose"):
        found, checked["prose files"] = prose.check(repo, changed, cfg.vale_config, cfg.excluded)
        findings += found
    if enabled("code"):
        found, checked["text files for hidden characters"] = code.scan_files(repo, changed, cfg.excluded)
        findings += found
        found, checked["Python files"] = code.ruff_check(
            repo, changed, cfg.excluded, cfg.ruff_format, cfg.ruff_select, cfg.ruff_format_scope
        )
        findings += found
        found, checked["JS/TS files"] = code.biome_check(repo, changed, cfg.excluded, cfg.biome_config)
        findings += found
    if enabled("media"):
        found, checked["media files"] = media.check(repo, changed, cfg.excluded, cfg.media)
        findings += found
    if enabled("pr_text"):
        if args.pr_body_file or args.pr_title:
            body = Path(args.pr_body_file).read_text(encoding="utf-8") if args.pr_body_file else ""
            findings += prtext.check_pr(cfg.vale_config, args.pr_title or "", body)
            checked["PR description"] = 1
        if args.base and args.head and not args.all:
            commits = gitdiff.commit_messages(repo, args.base, args.head)
            findings += prtext.check_commits(cfg.vale_config, commits, cfg.commit_trailers)
            checked["commits"] = len(commits)

    findings = apply_overrides(findings, cfg.overrides)
    report = markdown_report(findings, checked, args.head or "")
    if args.report_md:
        Path(args.report_md).write_text(report, encoding="utf-8")
    if args.report_json:
        Path(args.report_json).write_text(to_json(findings), encoding="utf-8")
    if args.github:
        print(annotations(findings))
    else:
        print(report)
    errors = sum(f.severity == "error" for f in findings)
    print(f"slop-gate: {errors} blocking, {len(findings) - errors} advisory", file=sys.stderr)
    return exit_code(findings)


def run_judge(args: argparse.Namespace) -> int:
    """Advisory vision review of changed images. Exits 0 unless it cannot run."""
    repo = Path(args.repo).resolve()
    cfg = config.load(repo)
    if not os.environ.get("OPENROUTER_API_KEY"):
        raise RuntimeError("OPENROUTER_API_KEY is not set; add it as a repository secret")
    changed = gitdiff.changed_lines(repo, args.base, args.head)
    models = args.models.split(",") if args.models else cfg.judge.get("models", judge.DEFAULT_MODELS)
    limit = (
        args.max_images
        if args.max_images is not None
        else cfg.judge.get("max_images", judge.DEFAULT_MAX_IMAGES)
    )
    images = []
    for rel in sorted(changed):
        if PurePosixPath(rel).suffix.lower() not in media.IMAGE_EXTS or cfg.excluded(rel):
            continue
        if not (repo / rel).is_file():
            continue
        rule = media.rules_for(rel, cfg.media)
        if rule.get("skip"):
            continue
        spec, source = judge.find_spec(repo, rel, rule)
        images.append((rel, spec, source))
    skipped = max(0, len(images) - limit)
    results = judge.run(repo, images[:limit], models)
    text = judge.report(results, models, skipped)
    if args.report_md:
        Path(args.report_md).write_text(text, encoding="utf-8")
    print(text)
    return 0


def run_strip_footer(args: argparse.Namespace) -> int:
    """Rewrite the body file without its footer; print how many lines went."""
    path = Path(args.body_file)
    body, removed = prtext.strip_footers(path.read_text(encoding="utf-8"))
    if removed:
        path.write_text(body, encoding="utf-8")
    print(removed)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="slopgate", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="lint the lines a PR changed (or the whole repo with --all)")
    c.add_argument("--repo", default=".")
    c.add_argument("--base", help="base commit or ref of the PR")
    c.add_argument("--head", help="head commit or ref of the PR")
    c.add_argument("--all", action="store_true", help="check every tracked file, every line")
    c.add_argument("--only", help="comma list: prose,code,media,pr_text")
    c.add_argument("--pr-title")
    c.add_argument("--pr-body-file")
    c.add_argument("--report-md")
    c.add_argument("--report-json")
    c.add_argument("--github", action="store_true", help="print GitHub annotations instead of Markdown")
    c.set_defaults(func=run_check)

    j = sub.add_parser("judge", help="advisory vision-model review of changed images")
    j.add_argument("--repo", default=".")
    j.add_argument("--base", required=True)
    j.add_argument("--head", required=True)
    j.add_argument("--models", help="comma list of OpenRouter model ids")
    j.add_argument("--max-images", type=int)
    j.add_argument("--report-md")
    j.set_defaults(func=run_judge)

    s = sub.add_parser("strip-footer", help="remove tool attribution footers from a PR description file")
    s.add_argument("--body-file", required=True, help="rewritten in place")
    s.set_defaults(func=run_strip_footer)

    m = sub.add_parser("media-init", help="print a draft media manifest from existing assets")
    m.add_argument("--repo", default=".")
    m.set_defaults(func=lambda a: print(media_init.draft(Path(a.repo).resolve()), end="") or 0)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (RuntimeError, ValueError) as exc:
        # A check that cannot run must fail the job, never pass silently.
        print(f"::error title=slop-gate could not run::{exc}", file=sys.stderr)
        return 2
