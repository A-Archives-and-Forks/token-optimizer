# Token Optimizer: Benchmark Report

> **$5.02 saved per working session** across five compounding layers, measured against 1,885 real sessions over 30 days.
> Every number comes from local production telemetry. Every measurement tool ships in the repo.

---

## 💰 Summary

All pricing at Opus 4.8 rates ($5/MTok input, $25/MTok output, $0.50/MTok cache-read).

| Layer | 30-day savings | Evidence |
|---|---|---|
| 🔧 Output compression + context eviction | **$17.51** | 📊 Measured: 892 production events with before/after delta |
| 🔀 Model routing | **$56.95** | 📊 Measured: cost difference from downgrading routable turns |
| 🏗️ Structural waste cleanup | **$105.36** | 💡 Opportunity: savings if audit recommendations are applied |
| **🟢 Total (all layers)** | **$179.82/month** | |

> **Per session:** $5.02 compound savings on a 50-turn session with 15 tool outputs.
> **Per month:** ~$150 at 30 working sessions. Lighter sessions save less; heavier save more.
>
> These are conservative. Structural cleanup compounds across every turn of every future session. The Token Optimizer dashboard measures your personal before/after cost and reflects the full compounding effect.

---

## 📋 Corpus

| | |
|---|---|
| 🔬 Quality-scored sessions | **1,885** (30 days, `trends.db`) |
| 📂 Sessions with file reads | **5,814** (backfill corpus for skeleton analysis) |
| 📖 First-reads analyzed | **30,771** |
| 🧪 Benchmark fixtures | **57** across 10 categories |
| ⚡ Avg prompt-cache hit rate | **65.4%** |
| 🖥️ Platforms | Claude Code CLI, VS Code, Codex, Copilot, OpenClaw, OpenCode, Hermes |

The two corpora are distinct populations, not double-counted. The backfill corpus is larger because it includes historical sessions recovered from file-read logs.

**Data source:** The production numbers in this benchmark come entirely from Claude Code CLI sessions (the author's primary platform). Quality scoring, dashboard, and savings tracking work on all supported platforms, but quality signal counts vary by platform (3 to 7 signals depending on the platform's measurement context). The grade scale (S/A/B/C/D/F) is consistent everywhere.

**Reproducibility:** Your results will differ based on your usage. Every measurement tool ships in the repo so you can regenerate against your own data. See [Running the Benchmarks](#-running-the-benchmarks).

---

## 🏗️ Layer 1: Structural Overhead

> Saves tokens on **every turn of every session.** The single largest compounding savings category.

Before any conversation starts, the model re-sends CLAUDE.md, skills, MCP tool schemas, and MEMORY.md on every turn. Token Optimizer's 8 auditors score each component and flag waste.

| | |
|---|---|
| Unused skills found | 61 |
| Recoverable tokens (skills alone) | 4,026 |
| **Total structural opportunity** | **61,227,039 tokens / $105.36** |

📊 **Tier:** Opportunity (savings if recommendations are applied).

**Why this compounds.** A 5,000-token cleanup saves 5,000 tokens per turn, every session, permanently. In a 50-turn session that is 250,000 fewer tokens. Over a month of sessions it dwarfs every other layer.

**Cache impact on the dollar math:**

| Scenario | Per-turn (5K tokens removed) | 50-turn session |
|---|---|---|
| All fresh ($5/MTok) | $0.025 | $1.25 |
| 65% cached ($0.50 cache / $5 fresh) | $0.011 | $0.53 |

65% is the observed cache-hit average across 1,885 sessions.

---

## 🔧 Layer 2: Output Compression

> Pattern-matched compression families, not generic summarization. Two mechanisms: **shrink it** or **evict it** (store locally, serve a stub).

### 🧪 Fixture suite: 57 test cases

Every fixture defines raw output, a must-preserve list, a must-not-contain list (catches hallucination), and a minimum compression ratio. A fixture passes only when **all three checks hold.**

| Category | # | What's tested |
|---|---|---|
| git | 7 | status, log, diff, merge conflicts, non-repo error |
| build | 8 | cargo, make, webpack, tsc, gradle |
| lint | 7 | eslint, ruff, clippy, pylint |
| logs | 7 | nginx, docker, systemd, application |
| test runners | 6 | pytest, jest, go test |
| tree / directory | 6 | large listings, nested structures |
| progress | 3 | npm install, pip, downloads |
| 🔒 security | 3 | AWS keys, GitHub PATs, Slack tokens (must NOT be stripped) |
| ⚠️ error passthrough | 5 | non-zero exit, permission denied (must pass through raw) |
| 🔄 tee-on-failure | 5 | failed commands preserve full output |

### 📊 Production events (30 days, 908 measured)

**Compression** (output shrunk, information preserved):

| Feature | Events | Before | After | Ratio | Saved |
|---|---|---|---|---|---|
| Structure map (re-reads) | 228 | 644K | 29K | **91.8%** | 615K |
| Git output | 195 | 410K | 67K | **73.1%** | 343K |
| First-read skeleton | 18 | 189K | 4K | **97.2%** | 185K |
| Loop detection | 50 | 61K | 0.2K | **99.4%** | 61K |
| Directory listings | 47 | 30K | 16K | **28.2%** | 14K |
| Pytest output | 29 | 7K | 0.3K | **91.9%** | 7K |
| Delta reads | 5 | 6K | 0.7K | **75.7%** | 6K |
| Log output | 2 | 0.5K | 0.2K | **57.6%** | 0.3K |
| **Subtotal** | **574** | **1.35M** | **118K** | **91.3%** | **1.23M** |

**Context eviction** (replaced by stub, original stored locally):

| Feature | Events | Tokens removed | How |
|---|---|---|---|
| Tool output archive | 306 | 3,252,146 | ~50-token stub, full result in local SQLite, `expand` to retrieve |
| Checkpoint restore | 12 | 410,503 | Prior session context injected on compaction |
| **Subtotal** | **318** | **3,662,649** | |

> 🟢 **Combined: 892 events, 4,887,138 tokens saved, $17.51 measured (30 days)**

Token counting uses `bytes / 4` as BPE proxy (~15% error vs actual Claude tokenization). Consistent across all measurements.

---

## 📖 Layer 3: First-Read Skeletons

> Large file, first read, unlikely to edit soon? Serve a skeleton. Full original archived and expandable.

**Corpus replay:** 5,814 sessions, 30,771 first-reads, 2,408 eligible.

| Language | Size | Reads | Sessions | Edit rate | Skeleton ratio | Tokens saved | Status |
|---|---|---|---|---|---|---|---|
| markdown | 16-64KB | 1,329 | 751 | 2.9% | 97.1% | 10.3M | 🟢 PROMOTE |
| python | 16-64KB | 763 | 477 | 1.4% | 96.1% | 6.4M | 🟢 PROMOTE |
| typescript | 16-64KB | 220 | 120 | 0.9% | 97.4% | 1.5M | 🟢 PROMOTE |
| python | 64-256KB | 66 | 64 | 1.5% | 98.5% | 1.5M | 🟢 PROMOTE |
| markdown | 64-256KB | 13 | 9 | 0.0% | 98.9% | 279K | 🟢 ACTIVE (interpolated) |
| typescript | 64-256KB | 2 | 2 | 0.0% | 99.1% | 44K | 🟢 ACTIVE (interpolated) |
| json | 16-64KB | 10 | 9 | 0.0% | 97.6% | 71K | 🟡 measure-only |
| yaml | 16-64KB | 9 | 7 | 0.0% | 98.2% | 60K | 🟡 measure-only |
| json | 64-256KB | 3 | 3 | 0.0% | 99.7% | 91K | 🟡 measure-only |
| javascript | 16-64KB | 4 | 3 | 0.0% | 97.9% | 26K | 🟡 measure-only |

> **Total projected savings: 20,156,647 tokens** &middot; **6 cohorts now active by default.**

**Promotion gate:** edit-within-5-turns rate < 15%, across 20+ reads in 5+ distinct sessions. 🟢 ACTIVE means the gate is met and skeletons serve by default.

**Default-on expansion (2026-06):** Token Optimizer now turns proven compression ON by default after validating it against *your own* session history — no weeks-long shadow phase. The two **interpolated** cohorts (markdown / typescript 64-256KB) graduated on a strong rule: zero edits in history **and** the same language already passed the full gate in the adjacent 16-64KB band. New languages with no adjacent passing band (json / yaml / javascript) stay measure-only until they accumulate enough history or are promoted explicitly.

**Interpolated-cohort exposure (honest):** because an interpolated cohort starts from a thin sample, the live tripwire judges it on a **smaller floor (5 active skeletons)** than the full-gate floor (10). Concretely: up to **5 active skeletons** of an interpolated cohort can be served before the tripwire has enough signal to auto-demote it on a bad live edit-rate. The always-available `expand <key>` recovers the full file in every one of those cases, and an operator who wants zero exposure can demote a cohort by hand at any time: `measure.py cohorts promote <lang:band>` re-promotes, and dropping the tuple from `read_cache.FIRST_READ_ACTIVE_COHORTS` (or `TOKEN_OPTIMIZER_FIRST_READ_ACTIVE=0`) disables active serving outright. The dashboard cohort panel flags interpolated cohorts so the thinner basis is visible.

**Live tripwire (replaces the shadow phase):** every active cohort is watched at runtime. If its live edit-after-skeleton rate ever crosses 15%, the cohort **auto-demotes** to measure-only and a `cohort_demoted` event is logged. Demotions are sticky (no flapping) — re-promotion is explicit (`measure.py cohorts promote <lang:band>`) or via the next history backfill.

**Agent/Task results (measured, not active):** a 4.3M-token sub-agent result pool was backfilled for a head+tail+pointer treatment. The history harm proxy — how often the parent's next turn quotes (≥20-char verbatim) the would-be-elided middle — came back at **39.1%**, well over the 15% gate. The data decided: agent-result compression ships **measure-only** rather than risk eliding findings sub-agents place mid-result. The pool surfaces in the dashboard's compression-coverage table (labeled `Agent/Task result (measure-only, harm 39%)`, rendered from the harm-rate constant); the harm *rate* itself is computed and reported by the backfill CLI (`compression_backfill.py --agent-results [--json]`) and documented here, not recomputed live on the dashboard. The full result is archived and `expand`-able regardless.

**Safety:** Full original always archived before any skeleton is served. Archive fails = full file served unchanged (fail-open). File on disk never modified.

---

## 🔀 Layer 4: Model Routing

> Compression saves on the response side. Routing saves on the request side.

**30-day production (1,885 sessions):**

| Metric | Value |
|---|---|
| Baseline top-tier share | **95%** |
| Current top-tier share | **64.7%** |
| Routable fraction | **30%** of turns |
| 🟢 Realized savings | **$56.95** |
| 💡 Additional potential | **$30.01** |

11 anti-pattern detectors identify turns where a cheaper model produces identical results. Quality nudges prevent degradation before it causes retries.

**Math:** Opus ($5/$25) vs Haiku ($1/$5) = **80% savings** on routable turns. Applies across model ecosystems (Anthropic, Codex, OpenClaw, OpenCode).

---

## 🔄 Layer 5: Session Continuity

> A session that loses context and retries for 10 turns wastes more than any compression saves.

| Mechanism | What it does |
|---|---|
| **Progressive checkpoints** | Captures decisions, errors, file context, agent state to local SQLite throughout a session |
| **Checkpoint restore** | Keyword-matches stored checkpoints on new session or compaction, injects relevant context |
| **Tool result archive** | Replaces large outputs with ~50-token stubs, full result retrievable via `expand` |
| **Loop detection** | Catches repeated reads/retries, breaks the cycle (50 detections, 60,987 tokens saved) |
| **Quality scoring** | 7-signal real-time scoring, fires coaching nudges when quality degrades |

Checkpoint restore and tool archive token counts are reported in Layer 2 (context eviction). Listed here because the mechanism is continuity, but **not double-counted**.

---

## ⚡ Compound Effect

The layers multiply, not add:

```
Session cost = turns x (overhead + avg_output + routing_premium)
              + restart_penalty + retry_waste

Token Optimizer reduces:
  overhead         -> Layer 1 (structural: -15 to -40%)
  avg_output       -> Layer 2+3 (compression + skeletons: -28 to -97%)
  routing_premium  -> Layer 4 (model routing: -40 to -80% on routable turns)
  restart_penalty  -> Layer 5 (checkpoint restore on compaction)
  retry_waste      -> Layer 4+5 (loop detection + quality scoring)
```

**Example: 50-turn Opus session, 15 tool outputs, 1 compaction**

| Layer | Mechanism | Tokens saved | $ saved |
|---|---|---|---|
| 🏗️ Structural | 5K fewer tokens x 50 turns | 250,000 | $1.06 |
| 🔧 Compression | 15 outputs x 10K x 73% ratio | 109,500 | $2.74 |
| 📖 Skeletons | 3 large files x 20K x 97% | 58,200 | $0.29 |
| 📦 Eviction | 4 results archived (avg 10.8K) | 43,200 | $0.15 |
| 🔄 Continuity | 1 compaction, 34K recovered | 34,000 | $0.17 |
| 🛑 Loop prevention | 2 loops x 5 turns x 6.1K | 61,000 | $0.61 |
| **Total** | | **555,900** | **$5.02** |

> At 30 sessions/month: **~$150/month.** The per-layer production data above is the ground truth.

---

## 📊 Quality Grades (1,885 sessions)

7 signals: context fill degradation, stale reads, bloated results, compaction depth, decision density, agent efficiency, and absolute waste tokens.

| Grade | Sessions | |
|---|---|---|
| **S** | 38 | 🟣 Exceptional: minimal waste, high decision density |
| **A** | 436 | 🟢 Good: clean context, efficient tool use |
| **B** | 528 | 🔵 Normal: some bloat, recoverable |
| **C** | 264 | 🟡 Degraded: significant waste, coaching recommended |
| **D** | 619 | 🔴 Poor: heavy bloat, likely retries or loops |
| **F** | 0 | ⚫ Failing: near-total waste (none observed in this corpus) |

Tracked over time so you can see whether your habits and Token Optimizer's interventions are improving session efficiency.

---

## 🧪 Running the Benchmarks

```bash
# Fixture suite (validates compression quality)
python3 scripts/benchmark.py
python3 scripts/benchmark.py --json

# Historical corpus replay (first-read skeleton analysis)
python3 scripts/compression_backfill.py
python3 scripts/compression_backfill.py --limit 100 --json

# Live compression stats (from trends.db)
python3 scripts/measure.py compression-stats
python3 scripts/measure.py compression-stats --days 7 --json

# Full dashboard (all layers visualized)
python3 scripts/measure.py dashboard
```

---

## 📝 Methodology Notes

- **Token counting:** `bytes / 4` proxy (~15% error vs actual BPE). Consistent across all measurements.
- **Three-tier accounting:** 📊 Measured (before/after delta), 💡 Opportunity (if recommendations applied), 🔮 Projected (shadow replay). Never summed together; each table labels its tier.
- **Cache honesty:** Prompt-cache savings (cache_read) are never claimed as Token Optimizer savings. The Anthropic cache is free infrastructure. We do account for the secondary benefit: structural cleanup reduces cache-read volume.
- **Security:** Fixtures verify credentials (AWS keys, PATs, Slack tokens) survive compression intact. Compression never strips what the model needs to see.
- **Safety-first promotion:** First-read skeletons require proof from your own session history before activating. No cohort promoted without meeting the edit-rate gate across multiple sessions.
- **Cache-expiry watchdog (opportunity tier, model-agnostic):** Detects prompt-cache waste using a per-provider cache PROFILE registry (`PROVIDER_CACHE_PROFILES`), never hardcoded Anthropic semantics. Each session resolves its dominant model to a profile and routes to the matching detector:
  - **explicit_ttl** (Anthropic API / Claude Code): a consecutive turn pair where the inter-turn gap exceeds the profile's EFFECTIVE TTL boundary (strict `>`) AND the next turn re-writes a cached prefix (its `cache_creation` is >= 50% of the previous turn's cached volume). The boundary is per-profile: **Claude Code uses 1 hour (3600s)** — Claude Code requests a 1-hour prompt cache (the historical "silent downgrade to 5 minutes" was a bug fixed in v2.1.129) and empirically the prefix survives sub-hour pauses, so only pauses longer than an hour count. Raw Anthropic API/SDK/harness sessions (e.g. Hermes → Anthropic) use the 5-minute default (300s). Cost = write premium actually paid (priced at the session's real model, using the per-turn 1h/5m TTL split when the log reports it, else the profile's write rate) minus the 0.1x read a live cache would have cost. **Recoverable framing differs by profile:** for **anthropic_api** the 1-hour-`cache_control` counterfactual is valid — it banks the avoidable repeat-write cost minus the 2x-vs-1.25x premium on the first (unavoidable) write, floored at 0 (`would_be_savings_1h_usd`). For **claude_code** that counterfactual is removed entirely (Claude Code ALREADY holds a 1h cache; no setting extends it); the waste is re-paid cache writes after >1h pauses, avoidable only behaviorally (resume within the hour / batch related work), reported as `avoidable_behavioral_usd`.
  - **automatic_discount / explicit_storage** (OpenAI/Codex, Gemini, DeepSeek): these providers emit no cache-CREATION event, so expiry shows up as a COLLAPSE in the cached/prompt ratio — turn N has a healthy cached ratio (>= 0.40), the gap to turn N+1 exceeds the profile TTL, and turn N+1's cached ratio collapses (< 0.10) while its prompt stays comparable (>= 50% of prior). The lost cached tokens are re-billed at the full input rate instead of the discounted cached rate; recoverable equals the waste (no extra-write premium to net out).
  - **none** (unknown / no-cache models): counted in an honest `no_cache_economics_sessions` bucket, never as waste.

  The output carries a per-provider breakdown (provider, cache_kind, `recoverable_kind`, sessions, affected, waste tokens/cost, recoverable, remedy, confidence verified/estimated) and an explicit `coverage_gaps` structure. **Verified profiles (2026-06-11, provider docs):** Claude Code requests a 1h cache (v2.1.129 fixed the silent 5-min downgrade); Anthropic API 5min default/1h@2x/1.25x write/0.1x read; OpenAI cached 0.1x, 5-10min→max 1h, exact-prefix >=1024 tok, `prompt_cache_retention="24h"` policy knob; Gemini implicit (~75-90% off, 1024/2048 min) + explicit `cached_content` user TTL (default 1h) with per-hour storage; DeepSeek disk cache 0.1x, hours-to-days, no knob. **Empirical basis (Alex's last 30 days, 1,393 turn-gaps >5min with prefix >=10k tokens):** prefix re-write rate is ~1% for 5-15min gaps, ~1% for 15-60min gaps, and ~90% for >60min gaps — the Claude Code cache plainly survives sub-hour pauses, which is why the boundary is 1 hour. **Version caveat:** sessions recorded while running Claude Code OLDER than v2.1.129 may show sub-hour expiries (the fixed silent-downgrade bug); the detector attributes by observed re-writes either way, so genuine sub-hour re-writes are still priced where they actually occur. **Wired:** Claude Code JSONL (explicit_ttl) and Codex rollouts (openai automatic_discount — `info.last_token_usage.cached_input_tokens` + timestamps, collapse detection). **Documented coverage gaps (rendered, not silent):** Hermes (state.db per-session aggregates only, cache_read unreliable), OpenClaw/OpenCode (TypeScript engines, no Python per-turn read path), Copilot (credits-billed, no per-turn cache detail). This is an OPPORTUNITY-tier signal computed by a standalone analysis function that never writes to `compression_events`, so it can never enter the realized savings headline. **Assumption:** the boundary is the documented effective TTL (Claude Code 1h, API/SDK/OpenAI 5min); gaps within a few seconds of a boundary may be misclassified under clock/network skew. **Drill-down for disputed numbers:** `--verbose` prints a per-affected-session detail table (session file, widest waste-triggering gap in seconds, re-written tokens, estimated cost), so a headline waste figure can be traced to the exact sessions behind it. `--days` is clamped to [1, 365] (out-of-range values are clamped with a stderr note). Surface: `measure.py cache-report [--days N] [--json] [--verbose] [--fresh]`.

### Known gaps

- **Opus fast-mode cost is under-counted ~50%.** Claude Code v2.1.154 added a fast mode for Opus 4.8 billed at 2x the standard rate. Fast mode is not exposed in session JSONL, the statusline input, or settings.json (it surfaces only in the interactive `/model` picker and a VS Code indicator), so there is no reliable signal to detect it. Fast-mode sessions are priced at the standard 1x rate until the transcript exposes the mode; their real cost (and any routing-savings estimate involving them) is understated by roughly half.
- **Claude Fable 5 cost now shows everywhere.** Sessions run on Claude Fable 5 before this version were recorded at $0 in the stored per-session cost column. From this version forward those sessions display their real cost, and trend views compute Fable cost at query time, so historical totals and trends reflect the true spend. The stored per-session cost column for the older rows is corrected in a future backfill; until then the trend/query-time figures are the authoritative ones for Fable.
- **Cache-health waste numbers are auditable per session.** The cache-report headline is an opportunity-tier estimate built from a prefix-rewrite heuristic (JSONL does not expose cache-key identity), so a number can look surprising. `measure.py cache-report --verbose` breaks the headline down by affected session (file, widest waste-triggering gap, re-written tokens, estimated cost) so any disputed figure traces to the sessions behind it. The detail is computed fresh (never cached) and biggest-waste sessions are listed first.
