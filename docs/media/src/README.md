# README recordings

The animated WebPs in [`docs/media`](..) are recordings of slop-gate running,
not mock-ups. This folder holds what made them, so they can be re-recorded
when the report format or the UI changes.

| File | Shows | Made with |
|---|---|---|
| `hero.webp` | PR #11 blocked, fixed and green, with captions | Screen recording of headed Chromium |
| `pr-blocked-then-fixed.webp` | The same PR, uncut | Screen recording of headed Chromium |
| `residue.webp` | Hidden characters and generation residue, before and after | VHS terminal recording |
| `scope.webp` | Changed lines only, then a whole-repo audit | VHS terminal recording |
| `footer.webp` | A comment's tool footer removed by the footer workflow | Screen recording of headed Chromium |

The browser scenes were recorded logged out on
[PR #11](https://github.com/mikejmckinney/slop-gate/pull/11), a demo PR on
this repo that was closed without merging. Its first commit carries deliberate
slop; the second fixes it.

## Tools

Versions used for the current recordings:

- Python 3.13 with Pillow 12 (WebP assembly), ffmpeg 6.1 with libx264
- Node 22 with Playwright and its Chromium, Xvfb
- [VHS](https://github.com/charmbracelet/vhs) v0.10.0 with ttyd, and
  [glow](https://github.com/charmbracelet/glow) v2.1.1 to render the report in
  the terminal: `go install github.com/charmbracelet/vhs@v0.10.0` and
  `go install github.com/charmbracelet/glow/v2@v2.1.1`; ttyd from the
  distribution's packages
- Fonts: JetBrains Mono and Noto Color Emoji (terminal), Inter (captions,
  read from `/usr/share/fonts/opentype/inter/`)

VHS drives a Chromium of its own. Put Playwright's Chromium first on `PATH` as
`chrome` and, when running as root, set `VHS_NO_SANDBOX=true`; otherwise VHS
downloads a browser.

## Re-recording

Run everything from this folder.

1. Terminal clips:

   ```sh
   python make_demos.py demos
   SLOPGATE_DIR=$(git rev-parse --show-toplevel) DEMOS_DIR=$PWD/demos vhs tapes/residue.tape
   SLOPGATE_DIR=$(git rev-parse --show-toplevel) DEMOS_DIR=$PWD/demos vhs tapes/scope.tape
   ```

2. Browser scenes, against a PR whose report is blocked:

   ```sh
   ./screenrec.sh blocked out
   ```

   Push the fix, wait for the gate to pass, then:

   ```sh
   FIX_SHA=<fix commit> ./screenrec.sh diff out
   ./screenrec.sh fixed out
   ```

   For the footer, post a comment from a session that adds a footer, then
   start the recording straight away, before the footer workflow runs:

   ```sh
   COMMENT_TEXT="<first words of the comment>" ./screenrec.sh footer out
   ```

   Set `PR=<number>` for a PR other than #11. The cut points in `encode.py`
   are tuned to these scenes, so check the hero's crops after recording a new
   PR.

3. Encode, then check each file by playing it in a browser:

   ```sh
   python encode.py out ..
   ```

Judge the result in a browser. Reading these files back with Pillow showed
ghosting and odd frame timings that Chromium didn't.
