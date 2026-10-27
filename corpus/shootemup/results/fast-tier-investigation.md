# OpenCode Fast-tier investigation

Date: 2026-08-07

Status: the initial OAuth attempts were invalidated and archived. After API credits and `OPENAI_API_KEY` became available, the harness was corrected to isolate OpenCode's credential store, fail on zero provider cost, and run two valid API-key Fast candidates.

## Initial OAuth observations — invalid as Fast treatments

| Run | Requested route | Elapsed | Turns | Total tokens | Corrected score |
|---|---|---:|---:|---:|---:|
| Historical Luna Xhigh OpenCode | Standard | 12:02 | 43 | 1,308,350 | 95 |
| Luna Xhigh Fast OpenCode (archived) | Priority requested / Default served | 13:03 | 43 | 1,307,433 | 95 |
| Fresh Luna Xhigh OpenCode control | Standard | 10:04 | 28 | 667,252 | 92 |
| Luna Max Fast OpenCode (archived) | Priority requested / Default served | 18:45 | 41 | 2,005,392 | 91 |

The historical Standard Xhigh and Fast Xhigh runs followed nearly identical trajectories: 43 turns and about 1.308 million tokens each. Fast was 61 seconds slower. Their largest generation steps were also effectively identical at 55.3 and 55.4 output tokens per second. The fresh Standard control's largest generation step was 55.5 output tokens per second, which argues against a general environment slowdown.

## Client-side trace

OpenCode 1.18.15 did select `openai/gpt-5.6-luna-fast`. Its catalog maps that alias to API model `gpt-5.6-luna` with `serviceTier: "priority"`.

The tagged OpenCode source shows the complete request path:

1. Model options are merged into `providerOptions.openai`.
2. The OpenAI provider serializes `serviceTier` as `service_tier` in the Responses request.
3. With OAuth authentication, OpenCode rewrites the URL to `https://chatgpt.com/backend-api/codex/responses` while preserving the request body.

This rules out OpenCode silently ignoring the Fast alias or dropping the tier before transport.

## Direct OAuth transport probe

A minimal diagnostic A/B request was sent to the same Codex OAuth backend with the same Luna API model. No candidate was launched. Only the requested/effective tier, duration, and usage were retained.

| Requested tier | HTTP | Response-created tier | Response-completed tier | Duration | Tokens |
|---|---:|---|---|---:|---:|
| Omitted | 200 | `auto` | `default` | 6.560 s | 212 |
| `priority` | 200 | `auto` | `default` | 4.920 s | 256 |

The backend accepted the Priority-bearing request but reported that it actually completed on the Default tier. A single short latency comparison is not a performance benchmark; the effective-tier fields are the diagnostic result.

## Initial conclusion

The Fast benchmark treatments were configured correctly, but the ChatGPT OAuth Codex backend did not service the verified Priority request on the Priority tier. The most likely immediate cause of the discrepancy is silent server-side fallback to Default for this headless OAuth route or account state, not candidate branching, subagents, the OpenCode model alias, or a system-wide Codespace regression.

The available response does not reveal why the server declined Priority. Entitlement, headless-route policy, quota, capacity, or rollout state remain possible. Recent upstream Codex reports document the same `priority` request followed by a `default` completed response in ChatGPT-authenticated headless runs:

- <https://github.com/openai/codex/issues/30413>
- <https://github.com/openai/codex/issues/32191>

Those two attempts are labeled **Fast requested; Default served**, retained under the archive directories, and excluded from benchmark results.

## Valid API-key reruns

The first API-key attempt exposed a second harness problem: OpenCode reused its stored OAuth login despite `OPENAI_API_KEY` being present. The OpenAI dashboard therefore showed only nine probe requests and 25,803 tokens while the candidate's local OAuth ledger continued growing. That attempt was stopped, its evidence was archived as `shared-oauth-fallback-2026-08-07`, and it is excluded.

The corrected harness gives API-key candidates an isolated `XDG_DATA_HOME`, injects only the environment API key into provider configuration, and exits with code 78 if the completed candidate reports zero provider cost. A timed-style smoke test and each candidate-specific preflight confirmed an outgoing `service_tier: priority` request and Priority in both `response.created` and `response.completed`.

| Valid run | Effective tier | Elapsed | Turns | Total tokens | Provider cost | Corrected score | Gate-adjusted ROI |
|---|---|---:|---:|---:|---:|---:|---:|
| Luna Xhigh Fast OpenCode | Priority | 6:11 | 36 | 906,654 | $0.119823 | 96 | 111.5295 |
| Luna Max Fast OpenCode | Priority | 10:10 | 45 | 1,827,736 | $0.196836 | 93 | 65.7418 |

The compiler independently calculates the same costs from the current Fast Luna rates—$0.40/M uncached input, $0.04/M cached input, $0.50/M cache writes, and $2.40/M output—and now rejects a discrepancy above 1%. Together the valid runs contain 81 metered model turns and $0.316659 of provider-reported cost, separate from the preflight probes.

Against the contemporaneous standard Xhigh control, Fast was 38.6% quicker but cost 2.1× as much and had 8.3% lower gate-adjusted ROI. Max Fast was 23.4% quicker than the original standard Max OpenCode run but cost 2.2× as much, scored two points lower, and had 24.3% lower ROI. With one replicate each, quality and provider variance prevent a causal performance claim; the valid conclusion is that Priority materially reduced wall time in both selected comparisons but did not improve the benchmark's cost/time/quality ROI.

## Implemented protocol change

Future Fast candidates must pass the instrumented candidate-specific preflight, use isolated OpenCode credential state, retain effective-tier evidence, and report nonzero provider cost. The harness now enforces the API-key and provider-cost portions automatically; the retained preflight verifies effective Priority service before candidate timing begins.
