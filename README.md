# slop-gate

A reusable GitHub Actions workflow that blocks pull requests containing the
patterns AI tools leave behind: stock phrasing in docs and PR text, swallowed
errors and truncation markers in code, hidden Unicode, and broken image and
audio assets. It checks only the lines a PR changes, posts one report comment
that it rewrites on every push, and never commits to the branch. The PR author
reads the report, fixes the findings, pushes, and the check runs again.

## Add it to a repo

1. Copy [`examples/.github/workflows/slop-gate.yml`](examples/.github/workflows/slop-gate.yml)
   into the repo's `.github/workflows/`.
2. Optional: copy [`examples/.slop-gate.yml`](examples/.slop-gate.yml) to the repo
   root and trim it. Without it, the defaults apply and media gets only the
   universal checks.
3. In the branch ruleset for `main`, require the `slop-gate / slop-gate` status
   check.

Consumer repos call the workflow at a tag (`@v1`), so a rule change here
reaches them only when they move the tag.

## What it checks

| Family | Blocks on | Warns on |
|---|---|---|
| `prose` (Markdown, text, and comments in code) | Stock openers and closers, "it's not X, it's Y", hedging preambles, unnamed experts, inflated significance, assistant residue, unfilled placeholders, stacked buzzwords, more than two dashes per paragraph | Single inflated words, prestige labels, corporate verbs, `serves as` for `is`, trailing "-ing" commentary, filler transitions, stacked hedges, both-sides framing |
| `pr-text` (PR title, body, commits) | The prose rules above on the PR body; tool attribution footers | Attribution trailers in commits (level set by `commit_trailers`) |
| `unicode` (every changed text file) | Bidi controls, zero-width and tag characters, variation-selector runs, Latin look-alikes mixed into words, non-ASCII spaces in source code | Non-ASCII spaces in prose, markup, and data files |
| `residue` (source files) | Truncation markers (`... rest of the code`), placeholder secrets, AI banners, pasted assistant text, stub admissions | "In a real application you would…" comments |
| `ruff` (Python) | Unused imports and variables, undefined names, blind `except`, `try/except/pass`, commented-out code, mutable defaults, assign-then-return; formatting in files the PR adds | Formatting in existing files the PR edits |
| `biome` (JS/TS) | Unused imports and variables, empty blocks such as `catch {}`, catch-and-rethrow, unreachable code | |
| `media` (images and audio) | Undecodable files, wrong format for the extension, oversized files, clipping, true peak above 0 dBTP; and when the manifest declares it: size, frame grid, palette size, soft alpha in pixel art, off-grid pixels, tile seams, loudness target, loop gaps, loop clicks, level jumps at the loop point | True peak between −1 and 0 dBTP, long leading or trailing silence on one-shots |

The prose rules come from a house style table of AI-writing patterns. Each
rule in [`styles/SlopGate`](styles/SlopGate) has a fixture in
[`tests/fixtures/prose`](tests/fixtures/prose) that must trigger it, because a
Vale rule that matches nothing fails silently.

## Working with the report

The report comment groups findings by family. Each row has the file and line,
the rule id, what is wrong, and how to fix it. Errors block the merge;
warnings are advice. The same table is in the job summary, and the full list
is in the `slop-gate-findings` artifact as JSON.

Nothing here rewrites code or prose for you. Mechanical fixes are one command
(`ruff format <file>`), and the prose findings need a rewrite that a word swap
won't produce.

### Suppressing a finding

Sometimes a flagged pattern is the right call. Make that explicit in the file
so a reviewer sees it:

- **Prose in Markdown:** wrap the passage in
  `<!-- vale SlopGate.ContrastFormula = NO -->` …
  `<!-- vale SlopGate.ContrastFormula = YES -->`.
- **Residue in code:** add `slop-gate: ignore` in a comment on the same line.
  Unicode findings can't be suppressed this way, since an attacker could add
  the same comment.
- **A rule for the whole repo:** set it to `warning` or `off` under
  `overrides` in `.slop-gate.yml`.

## Media manifest

A file can't say whether it is right. `player.png` might be a 32×32 sprite
sheet in 16 colors, and `level1.ogg` might need to loop. The manifest in
`.slop-gate.yml` declares that intent with glob rules, and the media checks
compare each changed file against it. To draft one from the assets a repo
already has:

```bash
PYTHONPATH=path/to/slop-gate python -m slopgate media-init --repo .
```

The draft records what exists today. Keep the values that are intentional and
fill in the commented hints, such as which tracks loop.

For loops, prefer Ogg Vorbis or Opus. MP3 encoders pad the start and end of
the stream, and the padding plays as a gap at the loop point.

## Settings to pair with it

- **Attribution at the source.** In Claude Code, `attribution.commit` and
  `attribution.pr` set to `""` in `.claude/settings.json` turn off the
  commit trailer and PR footer
  ([example](examples/.claude/settings.json)). Some Claude Code versions have
  ignored the setting, so the PR check stays as the backstop.
- **Squash merges.** Set the repository's default squash message to "pull
  request title and description" so commit bodies, trailers included, don't
  reach the default branch.
- **Review.** Require one approving review from an account other than the
  PR author, dismiss stale approvals on new pushes, and require the
  `slop-gate` check.

## Run it locally

Requires Python 3.13, [Vale](https://vale.sh) 3.25+, Ruff, Node (for Biome),
and ffmpeg for audio.

```bash
pip install -r requirements.txt
PYTHONPATH=. python -m slopgate check --repo ../my-repo --base origin/main --head HEAD
PYTHONPATH=. python -m slopgate check --repo ../my-repo --all --only prose  # whole repo
python -m unittest discover -s tests -t .
```

`SLOPGATE_BIOME` and `SLOPGATE_RUFF` point at specific binaries when they
aren't on `PATH`.

## Limits

These checks are patterns, not authorship detection, and plenty of human
writing trips them. They also miss what needs judgment: invented citations
and statistics, paragraphs that repeat each other, comments that restate the
code, and image defects such as malformed hands or garbled lettering. A
vision-model review for images is planned as an advisory, label-triggered
job.
