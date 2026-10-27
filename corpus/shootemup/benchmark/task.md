# Candidate task: Neon Barrage

Build and deploy a polished browser shoot-'em-up game named **Neon Barrage**. The frontend must be deployed to Cloudflare Pages (or Cloudflare Workers static assets) and its persistent leaderboard must use a newly created Supabase project.

You own delivery end to end. Install any tools, skills, packages, or MCP servers you need. Work only inside your candidate directory except for normal tool caches. Do not inspect sibling candidate directories, `results/`, or evaluator implementation. Do not pause the Supabase project yourself: the benchmark controller must verify the live leaderboard first and will pause it immediately afterward.

## Fixed environment

- Model and reasoning effort: assigned by the benchmark controller for this treatment.
- Supabase management credential: `SUPABASE_API_KEY` (map to `SUPABASE_ACCESS_TOKEN` if a CLI expects that name).
- Cloudflare credential: `CLOUDFLARE_API_KEY` (map to `CLOUDFLARE_API_TOKEN` for Wrangler).
- Supabase organization slug: `xfbsknprvxldvgioaows`.
- Your Cloudflare and Supabase resource names must begin with `shootemup-bench-<treatment>-`.
- You have at most 45 minutes of wall-clock time.

## Product requirements

The deployed game must:

- render a real-time 2D arcade shooter in a canvas;
- support keyboard movement with Arrow keys or WASD and firing with Space;
- work with touch controls on narrow/mobile viewports;
- include enemies, projectiles, collision detection, score, lives, escalating difficulty, a game-over state, and restart;
- have enough visual polish to feel deliberately designed: coherent art direction, readable hierarchy, animation/feedback, and responsive layout;
- have sound with a visible mute control (generated Web Audio is fine and must only begin after user interaction);
- accept a 1-16 character leaderboard name, submit the final score to Supabase, show at least the top 10 in descending order, and persist entries across a page reload;
- handle loading, empty, validation, and network-error states without making the game unplayable;
- expose no Supabase service-role/secret credential to the browser;
- use RLS on every table in an exposed schema and permit only the minimum public leaderboard operations. Validate names and plausible integer scores in the database, not only in JavaScript.

## Required test surface

Use these stable selectors:

| Element | Selector |
|---|---|
| Game canvas | `[data-testid="game-canvas"]` |
| Start control | `[data-testid="start-button"]` |
| Score display | `[data-testid="score"]` |
| Lives display | `[data-testid="lives"]` |
| Mute control | `[data-testid="mute-button"]` |
| Leaderboard list/table | `[data-testid="leaderboard"]` |
| Name input | `[data-testid="player-name"]` |
| Submit score control | `[data-testid="submit-score"]` |
| Touch controls container | `[data-testid="touch-controls"]` |

For deterministic black-box testing, expose this narrow test adapter in production:

```js
window.__NEON_BARRAGE__ = {
  getState: () => ({ phase, score, lives, playerX, playerY, enemyCount, projectileCount }),
  endGameForTest: (score) => { /* transition to game over with this non-negative integer score */ }
};
```

The adapter must drive the same game-over and score-submission UI used by normal gameplay. It must not write directly to Supabase or bypass input/database validation.

## Engineering and evidence

- Pin dependency versions and commit a lockfile if the app has dependencies.
- Include a migration SQL file that reproduces the remote schema and policies.
- Include local automated tests for meaningful gameplay or data behavior, plus build/lint/typecheck commands as appropriate.
- Include `README.md` with local setup, controls, architecture, deployment steps, and security decisions.
- Keep source code in the candidate directory; do not leave the deployed site as the only artifact.
- Verify the production URL and a real Supabase insert/read round trip before finishing.

## Required final artifact

Write `benchmark-result.json` in the candidate root with exactly these fields (the key is public, never a secret/service key):

```json
{
  "treatment": "<treatment>",
  "status": "complete",
  "cloudflare_url": "https://...",
  "cloudflare_project": "...",
  "supabase_url": "https://<ref>.supabase.co",
  "supabase_project_ref": "...",
  "supabase_public_key": "...",
  "verification": {
    "production_http_status": 200,
    "leaderboard_round_trip": true,
    "tests_passed": true
  },
  "notes": "brief factual handoff"
}
```

Do not claim completion unless the live deployment and leaderboard have actually been verified.
