# Preregistered benchmark protocol

## Question and unit of analysis

The benchmark estimates the utility, resource cost, decision efficiency, and marginal utility of coordination architectures, model/runtime variants, and a warm-cache retest completing one identical full-stack game task. A run—not an individual subagent—is the unit of analysis.

This initial experiment uses one replicate per treatment. It is useful as a controlled case study, not as a statistically stable ranking of agent architectures. A defensible general conclusion requires at least five randomized replicates per treatment and confidence intervals.

## Controlled variables

- Same task and infrastructure conditions across models and reasoning efforts. The original architecture treatments use `gpt-5.6-luna` at max; model/runtime extensions use the exact Sol or Luna effort explicitly named below.
- Same candidate task, 45-minute treatment ceiling, starting repository, credentials, account, region choices, and tool permissions.
- Fresh candidate directory and new Supabase/Cloudflare resource names per treatment.
- Runs are sequential to respect Supabase's two-active-project limit.
- External evaluator is architecture-blind and runs before project pause.
- Session JSONL, token use, wall time, subprocess count, A2A/native delegation events, source tree, test output, screenshots, and live endpoint results are retained.
- Rate-limit telemetry is enabled by default. API-key OpenCode runs retain sanitized transport metadata (status, request ID, `Retry-After`, rate-limit headers, and structured error code/type) through a streaming loopback observer; this observer is inside the measured request path and adds a small local-proxy overhead, while classification runs after timing ends. Codex OAuth runs take read-only account-limit snapshots before and after the timed interval. Prompts, authorization headers, and model response content are not retained by the observer. Runtime logs are classified for explicit limit errors, but latency alone is not evidence of throttling. The controller supports `--no-rate-limit-telemetry` and `RATE_LIMIT_TELEMETRY=false`; disabled runs are explicitly marked `not_observed`.

There is unavoidable order risk from shared provider state and changing network conditions. Treatment order for this run is fixed as monolith, native-dynamic, native-isolated, A2A because project provisioning is sequential; follow-up replicated experiments should randomize order.

The first extension order is fixed as monolith-warm, asynchronous A2A, monolith-Sol-Medium, and monolith-OpenCode. Candidate 10, monolith-Sol-Medium-OpenCode, runs afterward as a requested second extension. Candidates 11–13 then run sequentially as monolith-Sol-Low-OpenCode, monolith-Luna-Xhigh-OpenCode, and monolith-Luna-High-OpenCode. Candidate 14, monolith-Luna-Max-Codex-Minimal-Context, runs afterward to isolate the effect of Codex's optional context surfaces. Candidates 15–17 form a contemporaneous OpenCode methodology block, in fixed order: fresh plain control, Spec Kit, then Superpowers. The next requested extension runs asynchronous-streaming A2A in OpenCode followed by a Sol High OpenCode monolith. These extensions are not randomized.

The next preregistered batch runs sequentially in this fixed requested order: Sol Low Fast OpenCode, Sol Medium Fast OpenCode, Grok 4.5 Medium Cursor, Grok 4.5 High Cursor, Grok 4.5 Medium Fast Cursor, Grok 4.5 High Fast Cursor, and Cursor Auto Cost. All are monolithic treatments with delegation prohibited. OpenCode Fast acceptance requires the outgoing base model, reasoning effort, and `service_tier: priority` plus a completed response reporting `priority`. Cursor acceptance requires the requested model ID to be present in the live account model list and the server initialization event to report the matching Standard, Fast, or Auto label. On the authenticated individual Pro account, the CLI exposes the bare `auto` selector without a Balance or Intelligence optimization parameter; this is classified as Auto Cost, the continuation of Cursor's previous Auto routing behavior. Auto Cost does not expose its downstream routed model in terminal result telemetry and is therefore a router treatment rather than a controlled model treatment.

The subsequent authentication-mode comparison reruns Sol Medium OpenCode with an isolated API-key credential instead of the earlier OAuth session. It retains the model, medium reasoning effort, monolithic constraint, task, and Standard service tier. A preflight must confirm the exact model and effort, API-key isolation, and a completed `default` tier response before timing begins. This single sequential pair is exploratory and cannot by itself attribute output differences to authentication mode.

The next methodology extension runs AI Repo Template after the authentication-mode comparison. It uses the same Luna Max OpenCode model, max reasoning effort, OAuth authentication, monolithic constraint, 45-minute ceiling, and standard service tier as the fresh methodology control. The private template archive is pinned to commit `9fa3f87d4ea2c800829fa1fe1c215790199f838c`; authenticated download, repository initialization, template-seed onboarding, setup, adaptation, delivery, deployment, and verification all occur inside the measured interval. This extension is sequential and not randomized.

The next runtime/model extension runs two Claude Code monoliths sequentially in the user-requested order: Claude Opus 5 Medium, then Claude Sonnet 5 Medium. Both use Claude Code `2.1.226`, Claude.ai first-party OAuth, Standard speed/service tier, medium effort, the unchanged 45-minute task, and no native or background agents. Preflight must resolve the aliases to `claude-opus-5` and `claude-sonnet-5` and observe a successful Standard-tier response. Claude Code's `Agent`, `ListAgents`, `SendMessage`, `RemoteTrigger`, and worktree-agent controls are disabled; ordinary coding/deployment tools, skills, and MCPs remain available. This extension is sequential, unrandomized, and changes model, runtime, system context, tool surface, authentication, cache state, and provider conditions relative to earlier candidates.

The following runtime comparison runs the same model/effort pair through OpenCode `1.18.16`, sequentially in the user-requested order: Claude Opus 5 Medium, then Claude Sonnet 5 Medium. Both are plain monoliths using Anthropic API authentication, Standard service tier, the unchanged prompt/task and 45-minute ceiling, and an explicit prohibition on delegation or launching other agents. Excluded minimal preflights must resolve `anthropic/claude-opus-5` and `anthropic/claude-sonnet-5`, produce a successful response, and record nonzero provider-reported cost before either timed run. The fixed order is unrandomized and remains subject to cache and provider drift.

The subsequent authentication-mode extension reruns Claude Opus 5 Medium through OpenCode `1.18.16` using the community `opencode-claude-auth` plugin pinned to `2.1.6` and the user's Claude Pro OAuth subscription rather than an Anthropic API key. An excluded minimal preflight must remove Anthropic API-key environment variables, observe the plugin's OAuth loader, resolve `anthropic/claude-opus-5` with the `medium` variant, and receive a successful response. The task, monolithic restriction, standard tier, prompt, and 45-minute ceiling are unchanged. This one sequential replicate is exploratory; it also changes provider authentication headers and occurs after the API run, so cache and provider drift remain uncontrolled. Anthropic does not officially support this third-party subscription route, creating account-enforcement and reproducibility risk.

## Treatments

### `monolith`

One Codex session. Native multi-agent support is disabled. It must do all planning, implementation, infrastructure, and verification itself.

### `native_dynamic`

One parent Codex session with native multi-agent support enabled. The parent is explicitly told to use as many subagents as useful and may choose decomposition, overlap, and communication.

### `native_isolated`

One parent Codex session with native multi-agent support enabled. Each subagent must receive one bounded issue with exclusive file ownership or a separate worktree. Integration and cross-cutting verification belong to the parent; subagents cannot be used as free-form co-owners of the whole task.

### `a2a`

A Luna Max coordinator collaborates with at least two separately hosted Luna Max worker agents through A2A Agent Cards and JSON-RPC `message/send` requests over HTTP. Workers are opaque Codex sessions with distinct roles. The coordinator integrates and verifies their artifacts. Protocol traffic and per-agent usage are logged.

### `monolith_warm`

The original monolithic Luna Max treatment is repeated after dependencies and provider/tool caches have been warmed by prior runs. Native subagents remain disabled.

### `a2a_async`

A Luna Max coordinator submits bounded work to two Luna Max workers using A2A `message/send` with `return_immediately`, then polls with `tasks/get`. Stable idempotency keys are durably mapped to accepted task IDs. Repeating a submit with the same key resolves the existing task, and only the coordinator may create a new logical retry; transient polling never resubmits accepted work.

### `a2a_async_streaming_opencode`

One Luna Max OpenCode coordinator submits bounded work to two opaque Luna Max OpenCode workers through A2A 1.0 JSON-RPC `message/stream`. Each submission disconnects after the initial task event while accepted work continues asynchronously; the coordinator later receives server-sent status and artifact events through `tasks/resubscribe`. Stable idempotency keys map logical requests to accepted task IDs, and the coordinator owns retry decisions. This treatment changes both transport and runtime relative to `a2a_async`, so it measures the requested combined system rather than isolating the causal effect of streaming alone.

### `monolith_sol_medium`

One Codex session using `gpt-5.6-sol` at medium reasoning effort. Native subagents are disabled.

### `monolith_opencode`

One OpenCode session using `gpt-5.6-luna` at max reasoning effort. Delegation is prohibited so it remains a monolithic runtime comparison.

### `monolith_sol_medium_opencode`

One OpenCode session using `gpt-5.6-sol` at medium reasoning effort. Delegation is prohibited. This pairs with `monolith_sol_medium` to compare the Codex and OpenCode runtimes while holding the model and reasoning effort fixed, subject to sequential-run and cache effects.

### `monolith_sol_medium_opencode_api`

One isolated OpenCode session using `gpt-5.6-sol` at medium reasoning effort through `OPENAI_API_KEY` rather than stored OAuth. Delegation is prohibited. The controller uses a fresh OpenCode data directory, explicitly configures the API key, rejects zero provider-reported cost, and records sanitized request-level rate-limit telemetry. This pairs with `monolith_sol_medium_opencode`, subject to one-replicate, sequential-run, provider-load, and cache effects.

### `monolith_sol_low_opencode`

One OpenCode session using `gpt-5.6-sol` at low reasoning effort. Delegation is prohibited.

### `monolith_sol_high_opencode`

One OpenCode session using `gpt-5.6-sol` at high reasoning effort. Delegation is prohibited.

### `monolith_luna_xhigh_opencode`

One OpenCode session using `gpt-5.6-luna` at xhigh reasoning effort. Delegation is prohibited.

### `monolith_luna_high_opencode`

One OpenCode session using `gpt-5.6-luna` at high reasoning effort. Delegation is prohibited.

### `monolith_luna_max_codex_minimal`

One Codex session using `gpt-5.6-luna` at max reasoning effort with native subagents disabled. It runs under an ephemeral `CODEX_HOME` containing only a copied OAuth `auth.json` and a minimal configuration that disables configured MCP servers, apps, plugins, hooks, memories, web search, browser/computer-use integrations, and project instruction files. Core Codex runtime instructions, essential shell/file tools, and any non-disableable bundled system metadata remain, so this is a minimal-context Codex treatment rather than a raw-model treatment. The temporary credential-bearing home is deleted after the run; only its non-secret config and manifest are retained.

### `monolith_luna_max_opencode_retest`

A fresh contemporaneous control using one OpenCode session with `gpt-5.6-luna` at max reasoning effort. It has the same credentials, standard tools, MCP availability, permissions, repository baseline, and 45-minute ceiling as the two methodology treatments below, but no Spec Kit or Superpowers installation. Delegation is prohibited.

### `monolith_luna_max_opencode_speckit`

One monolithic OpenCode session using `gpt-5.6-luna` at max reasoning effort and GitHub Spec Kit pinned to `v0.16.0`. The controller installs and initializes Spec Kit inside the measured interval. In one continuing OpenCode session, the controller invokes `speckit.constitution`, `speckit.specify`, `speckit.plan`, `speckit.tasks`, `speckit.analyze`, `speckit.implement`, `speckit.converge`, a second `speckit.implement`, and a final deployment-verification turn. Spec Kit does not change the candidate's standard tool access. Delegation remains prohibited, isolating the structured specification workflow rather than a multi-agent architecture.

### `dynamic_luna_max_opencode_superpowers`

One parent OpenCode session using `gpt-5.6-luna` at max reasoning effort and Superpowers pinned to `v6.2.0`. The project-local plugin installs during the measured interval. The candidate is instructed to use the complete Superpowers methodology as designed, including brainstorming, planning, worktrees, test-driven development, task-specific subagents, reviews, verification, and branch completion where applicable. Standard tools remain available. Subagent and coordination costs are intentional parts of this treatment, not controlled away.

### `monolith_luna_max_opencode_ai_repo_template`

One monolithic OpenCode session using `gpt-5.6-luna` at max reasoning effort and AI Repo Template pinned to commit `9fa3f87d4ea2c800829fa1fe1c215790199f838c`. The controller downloads the authenticated archive and creates a fresh local Git baseline with no remote inside the measured interval. The first phase invokes the template's `repo-onboarding` command with explicit authorization to complete its documented `template-seed` lifecycle; the second phase resumes the same OpenCode session to deliver and verify the benchmark task. The pinned template's current `AGENTS.md` mandates one monolithic implementing agent because its earlier multi-role pipeline was retired, so delegation remains prohibited. Inherited `GITHUB_REPOSITORY`, `GH_REPO`, and `GITHUB_ACTIONS` variables are removed from the candidate process to prevent the template's setup automation from targeting the benchmark repository; task deployment credentials and the template's normal project-local tools, skills, instructions, and MCP configuration remain available.

### `monolith_opus_5_medium_claude_code`

One Claude Code session using `claude-opus-5` at medium effort and Standard speed through authenticated Claude.ai first-party OAuth. Agent/delegation and worktree-agent tools are disabled. The candidate otherwise retains Claude Code's ordinary tool, skill, and MCP surface, and all visible system/context/tool tokens are included in the terminal usage record.

### `monolith_sonnet_5_medium_claude_code`

One Claude Code session using `claude-sonnet-5` at medium effort and Standard speed through authenticated Claude.ai first-party OAuth. It uses the same monolithic restrictions, candidate task, runtime version, tool surface, credentials, and 45-minute ceiling as the Opus treatment, subject to the fixed sequential order and provider/cache drift.

### `monolith_opus_5_medium_opencode`

One OpenCode `1.18.16` session using `anthropic/claude-opus-5` at medium effort and Standard service tier through Anthropic API authentication. The candidate is instructed to act as the sole implementation agent and not delegate or launch other agents. All work occurs in a fresh candidate directory and counts toward the 45-minute ceiling.

### `monolith_sonnet_5_medium_opencode`

One OpenCode `1.18.16` session using `anthropic/claude-sonnet-5` at medium effort and Standard service tier through Anthropic API authentication. It uses the same monolithic restrictions, prompt, task, runtime, credentials, and ceiling as the Opus OpenCode treatment, subject to fixed-order cache and provider drift.

### `monolith_opus_5_medium_opencode_oauth`

One OpenCode `1.18.16` session using `anthropic/claude-opus-5` at medium effort and Standard service tier through the user's Claude Pro OAuth subscription. The controller loads `opencode-claude-auth@2.1.6`, removes Anthropic API-key environment variables, and rejects the run unless the excluded preflight observes the plugin's OAuth loader plus the requested model and variant. Delegation remains prohibited. API-equivalent token cost is retained for cross-candidate ROI; it is not an Anthropic API charge for this subscription-authenticated treatment.

### Methodology-extension accounting

- The original three-candidate block begins from a fresh standalone Git repository with the benchmark task committed, so Superpowers can create worktrees without receiving a repository advantage unavailable to its controls. AI Repo Template instead begins with its pinned archive because the repository scaffold is the treatment; archive download and creation of its fresh no-remote Git baseline are charged to that candidate.
- The 45-minute clock starts before treatment bootstrap. Spec Kit installation and initialization, Superpowers startup/plugin installation, AI Repo Template download/onboarding/setup, workflow artifacts, child-agent work where allowed, reviews, retries, integration, deployment, and verification all count toward elapsed time.
- OpenCode's SQLite session ledger is the authoritative token source for these runs. The controller selects sessions rooted in the candidate directory during the timed interval and recursively includes `parent_id` descendants, including descendants operating in worktrees. Input, cache-read, cache-write, output, and reasoning tokens are summed across all selected sessions.
- The raw parent JSONL, bootstrap logs and manifest, complete selected-session inventory, tool counts, child-session count, and an interval-based peak-concurrency estimate are retained. PAYG-equivalent API cost is calculated from the aggregated token ledger under the same pricing rules as other candidates.
- The fresh plain candidate is the primary comparator for Spec Kit and Superpowers. It is also the closest existing control for the later AI Repo Template extension, but the additional sequential delay means that comparison is exploratory and exposed to greater runtime, cache, provider, and infrastructure drift.
- This block measures the complete Superpowers package. It does not isolate whether any difference comes from templates, TDD, reviews, subagents, worktrees, or their interaction. A later factorial experiment would be required for that decomposition.
- After the primary AI Repo Template run was scored and its database paused, a user-requested exploratory continuation resumed the exact root session and workspace. Its additional time and recursive session-ledger tokens are recorded separately and then combined with the original run only in the exploratory continuation table. The continuation reused and restored the same Supabase project, was evaluated with the same automated, manual, and post-hoc rubric, and was paused again afterward; it does not replace the primary timed score.

## Quality scoring (monolith baseline = 100)

The monolith initially received a quality score of 100 and serves as the comparison baseline; 100 is not a ceiling. Each candidate is compared using retained evidence across the weighted categories below. Demonstrated improvements may add weighted score units and demonstrated regressions subtract them:

`Q_i = 100 + Σ(weighted improvements over monolith) - Σ(weighted regressions from monolith)`

The comparison is open-ended, so a candidate that materially exceeds the monolith can score above 100. For this completed run, the retained rubric evidence showed no net category improvement by a multi-agent candidate; the observed regression totals yield scores of 58, 92, and 93. A replicated run should preregister direct baseline-relative judging rather than derive the comparison after the fact.

### Uniform post-hoc regression review

After users identified missed interaction defects, every preserved production build received the same supplemental browser audit. It refines existing rubric allocations: 2 of the 6 leaderboard UI-submission points cover physical callsign entry, 1 of the 2 HUD points covers consistent initial progression, 1 restart point covers an explicitly advertised keyboard shortcut, and the existing 2 restart points cover an immediate visible click/touch restart. Supabase requests are mocked inside the browser, so this audit does not write leaderboard data.

Original automated and manual evidence remains unchanged. Supplemental deductions are applied directly to every candidate, including the monolith, whose corrected score is 98:

`corrected score = original score - candidate supplemental deductions`

The audit was defined after reported defects, so it is explicitly post hoc and must be preregistered in a replicated benchmark. See `results/posthoc-review.md` and each candidate's `posthoc-regressions.json` and `posthoc-score.json` evidence.

| Category | Comparison weight | Evidence |
|---|---:|---|
| Live Cloudflare deployment | 5 | HTTPS 200, application renders |
| Core control loop | 10 | start, keyboard movement, fire, state changes |
| Combat and progression | 10 | enemies, collisions, score, escalating difficulty |
| Lifecycle | 5 | lives, game over, restart |
| Persistent Supabase leaderboard | 15 | unique UI submission, sorted display, reload persistence, direct REST confirmation |
| Mobile/touch behavior | 5 | narrow viewport layout and visible usable touch controls |
| Visual design and feedback | 12 | blinded screenshot review at desktop/mobile, animation/effects |
| Audio and mute | 3 | user-gesture audio path and working mute control |
| Resilience/accessibility | 5 | loading/empty/error handling, labels/focus/readability |
| Supabase/data security | 10 | RLS, grants/policies, DB constraints, no privileged browser secret |
| Engineering quality | 10 | build, meaningful automated tests, maintainability, pinned lockfile |
| Reproducibility and handoff | 10 | migration, README, valid result artifact, factual verification |

Hard evidence rules:

- A missing or unreachable production URL earns zero in live, browser, and leaderboard categories.
- A leaderboard that is only mocked/local earns zero for persistence.
- Security points require inspection of the checked-in migration and a remote behavior check.
- Automated checks do not award subjective visual points; screenshots are retained for review.

## Resource measures

- Wall time from process start through final response.
- Total, cached input, non-cached input, output, and reasoning tokens from every `turn.completed` event across parent and workers.
- Number of model turns, shell/tool calls, native delegations, A2A messages, agents started, and peak concurrently active agents.
- Deployment/provisioning failures and repair iterations.

Dollar cost uses the official Standard API list price for the model used by each treatment, current when results are compiled. It is reported as a PAYG-equivalent estimate rather than an invoice because the runs execute through Codex/OpenCode and actual subscription, OAuth, contract, regional, service-tier, and tool billing can differ. Captured separately priced built-in tool calls are added when authoritative rates and counts are available. Supabase and Cloudflare free-tier usage contributes zero marginal infrastructure cost.

For the Cursor batch, Grok 4.5 Standard uses Cursor's published $2/M uncached input, $0.50/M cache read, and $6/M output rates, while Fast uses $4/M uncached input, $1/M cache read, and $18/M output. Cursor's live model-pricing data provides no cache-write rate for either Grok variant, so Grok cache writes are costed at zero. Cursor Auto Cost uses $1.25/M uncached input, $1.25/M cache write, $0.25/M cache read, and $6/M output.

For the Claude Code extension, the pricing snapshot dated 2026-08-10 uses Anthropic's first-party Standard API list prices. Claude Opus 5 costs $5/M base input, $6.25/M 5-minute cache writes, $10/M 1-hour cache writes, $0.50/M cache reads, and $25/M output. Claude Sonnet 5 uses the introductory prices effective through 2026-08-31: $2/M base input, $2.50/M 5-minute cache writes, $4/M 1-hour cache writes, $0.20/M cache reads, and $10/M output. Claude Code terminal `total_cost_usd` is retained separately from the canonical calculation; discrepancies are reported rather than silently reconciled.

## Decision model and marginal utility

For run `i` with quality score `Q_i`, estimated API cost in US dollars `C_i`, elapsed time in minutes `M_i`, total tokens `T_i`, and non-cached tokens `N_i`:

- quality gate: `Q_i ≥ 90`;
- quality-adjusted efficiency at an explicitly stated time value `r` in USD per minute: `E_i(r) = Q_i / (C_i + r × M_i)`.

Time is calculated in decimal minutes and displayed as `MM:SS`. There is no universal scalar ranking. Candidates that pass the quality gate are checked for Pareto dominance across higher quality, lower cost, and lower time. Results present the frontier first, followed by quality descending and cost ascending; this is presentation order only.

Efficiency is reported as sensitivity at `$0`, `$3`, `$10`, the Luna/Sol OpenCode break-even value, `$25`, and `$60` per hour of unattended agent time. A quality-gate failure cannot win a scenario. The gate and scenario set were chosen post hoc for this case study and must be preregistered in a replication.

The following are retained as single-dimension diagnostic measures rather than a headline ranking:

- token efficiency = `Q_i / (T_i / 1,000,000)` quality points per million total tokens;
- non-cached-token efficiency = `Q_i / (N_i / 1,000,000)`;
- time efficiency = `Q_i / H_i` quality points per wall-clock hour.
- API cost-effectiveness = `Q_i / C_i` quality points per estimated API dollar, where `C_i` applies the model's distinct uncached-input, cached-input, cache-write, and output rates plus captured separately priced tool calls. This is not conventional financial ROI, which would require a measured dollar benefit and use `(benefit - cost) / cost`.

For each multi-agent treatment relative to monolith `m`:

- marginal utility = `Q_i - Q_m` quality points;
- marginal token cost = `T_i - T_m`;
- marginal time cost = `H_i - H_m`;
- marginal dollar cost = `C_i - C_m`;
- marginal token utility = `(Q_i - Q_m) / ((T_i - T_m) / 1,000,000)` when the denominator is nonzero.

Dominance is reported explicitly: a treatment is dominated if another has at least as much quality with no more API cost and no more wall time, and is strictly better on at least one dimension. Diagnostic token and single-dimension efficiency measures remain available, but no one sensitivity scenario is treated as the universal result.

## Cleanup invariant

Immediately after evaluation, the controller calls `POST /v1/projects/{ref}/pause`, polls project status, and records the response. A treatment is not operationally complete until its newly created Supabase project is confirmed inactive (or a documented provider error explains why it could not be paused).
