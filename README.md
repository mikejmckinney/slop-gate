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

Consumer repos call the workflow at `@v1`, a release branch that moves
forward only to a commit whose CI passed on `main`. A rule change here reaches
consumers when `v1` moves; pin a commit SHA instead to control that yourself.

## What it checks

| Family | Blocks on | Warns on |
|---|---|---|
| `prose` (Markdown, text, and comments in code) | Stock openers and closers, "it's not X, it's Y", hedging preambles, unnamed experts, inflated significance, assistant residue, unfilled placeholders, stacked buzzwords, more than two dashes per paragraph | Single inflated words, prestige labels, corporate verbs, `serves as` for `is`, trailing "-ing" commentary, filler transitions, stacked hedges, both-sides framing |
| `pr-text` (PR title, body, commits) | The prose rules above on the PR body; tool attribution footers | Attribution trailers in commits (level set by `commit_trailers`) |
| `unicode` (every changed text file) | Bidi controls, zero-width and tag characters, variation-selector runs, Latin look-alikes mixed into words, non-ASCII spaces in source code | Non-ASCII spaces in prose, markup, and data files |
| `residue` (source files) | Truncation markers (`... rest of the code`), placeholder secrets, AI banners, pasted assistant text, stub admissions | "In a real application you would…" comments |
| `ruff` (Python) | Unused imports and variables, undefined names, blind `except`, `try/except/pass`, commented-out code, mutable defaults, assign-then-return; formatting in files the PR adds (or every changed line with `ruff_format_scope: changed`) | Formatting in existing files the PR edits |
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

## Vision review (advisory)

Deterministic checks can't see six fingers, a floating character, or text on
a sign that should be blank. The optional `slop-judge` job sends each changed
image to two vision models, `anthropic/claude-opus-5.5` and
`google/gemini-4-argon`, chosen by the [bakeoff](bakeoff/judge/README.md):
together they caught 18 known defects in 33 of 36 defect-runs (two runs each)
with no false claims, at about $0.03 per image.

To use it, add an `OPENROUTER_API_KEY` repository secret, add the
`slop-judge` job from the [example workflow](examples/.github/workflows/slop-gate.yml),
and put the `slop-judge` label on a PR. While the label is on, every push
re-runs the review on the images the PR changed and updates one comment.

- **It never blocks.** Models change their verdict on 4–12% of images between
  identical runs, so the job reports and passes. A finding both models raise
  is likely real; a finding from one model is a place to look.
- **It judges against a spec when it has one.** A `<name>.prompt.txt` file next
  to the image wins, then a `spec` on the matching media manifest rule.
  Without a spec, only contradictions inside the image are checked.
- **It caps cost.** At most 20 images per run by default; set `judge.max_images`
  or `judge.models` in `.slop-gate.yml` to change that.
- **Fork PRs are skipped**, because GitHub doesn't give them repository secrets.

## Settings to pair with it

- **Attribution at the source.** In Claude Code, `attribution.commit` and
  `attribution.pr` set to `""` in `.claude/settings.json` turn off the
  commit trailer and PR footer
  ([example](examples/.claude/settings.json)). Some Claude Code versions have
  ignored the setting, so the PR check stays as the backstop.
- **Footers you can't turn off.** Some agent tooling adds a footer to the PR
  description each time it opens or edits a PR, and no repo setting stops it.
  Pass `strip-attribution-footer: true` to the workflow and it deletes footer
  lines (and the rule above them) from the description before the check
  reads it. A footer quoted inside a code fence stays, and fork PRs are
  skipped, since their token can't edit the PR.
- **Squash merges.** Set the repository's default squash message to "pull
  request title and description" so commit bodies, trailers included, don't
  reach the default branch.
- **Review.** Require one approving review from an account other than the
  PR author, dismiss stale approvals on new pushes, and require the
  `slop-gate` check.

## Audit a whole repo

The PR gate only looks at changed lines. To map the debt a repo already has,
copy [`examples/.github/workflows/slop-audit.yml`](examples/.github/workflows/slop-audit.yml)
into the repo and run it from the Actions tab. It checks every tracked file,
puts the report in the job summary, saves the full list as the
`slop-audit-findings` artifact (JSON and Markdown), and doesn't fail unless
you set `fail-on-errors`.

To fix what it finds:

1. Mechanical fixes, run locally and reviewed like any other diff:
   `ruff check --fix --select F401,F841,F541 .`, `ruff format .`, and
   `npx @biomejs/biome@2.6.3 lint --write --config-path <slop-gate>/configs/biome.json .`.
2. Everything else needs a person or an agent: give it `slop-audit.json`,
   one file at a time, and have it rewrite the flagged passages. Each finding
   carries the rule and the fix.
3. Re-run the audit, then turn on the PR gate so the debt doesn't come back.

Split a large cleanup into several PRs by check family (`only: prose`, then
`code`, then `media`) so each one is reviewable.

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
code. Image defects such as malformed hands or garbled lettering get the
advisory vision review described above, which is a second opinion, not a gate.
