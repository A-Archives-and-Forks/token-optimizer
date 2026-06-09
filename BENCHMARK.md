# Token Optimizer: Benchmark Report

This benchmark measures five compounding layers of token savings against real session data, with quality verification at every step. All numbers come from production telemetry stored locally in `trends.db`. No data leaves the user's machine.

**Reproducibility note.** The numbers below come from the author's single-user corpus (1,885 quality-scored sessions over 30 days). Your results will differ based on your usage patterns, model mix, and session length. Every measurement tool is included in the repo so you can regenerate these tables against your own data. See [Running the Benchmarks](#running-the-benchmarks) for instructions.

## Corpus

| | Count |
|---|---|
| Sessions in trends.db (quality-scored) | 1,885 |
| Sessions with file reads (backfill corpus) | 5,814 |
| First-reads analyzed | 30,771 |
| Benchmark fixtures | 57 across 16 categories |
| Average prompt-cache hit rate | 65.4% |
| Platforms covered | Claude Code CLI, VS Code, Codex, OpenClaw, OpenCode |

The trends.db corpus contains 1,885 sessions with full quality scoring. The backfill corpus (5,814 sessions) is larger because it includes historical sessions recovered from file-read logs for first-read skeleton analysis. These are distinct populations, not double-counted.

## Layer 1: Structural Overhead (saves every turn)

Before any conversation starts, the model loads system prompt, CLAUDE.md, skills, MCP tool schemas, and MEMORY.md. Every token in this payload is re-sent on every turn. Token Optimizer's 8 auditors score each component for attention-curve efficiency, flag unused skills and MCP tools, and provide one-click archiving.

**Measured structural opportunity (30-day production):**

| Component | Details |
|---|---|
| Unused skills identified | 61 |
| Recoverable tokens (skills alone) | 4,026 |
| Total structural opportunity | 61,227,039 tokens, $105.36 (read + write combined) |

**Accounting tier:** Structural savings are **opportunity** (what would be saved if recommendations are applied), not measured compression events.

**Cache impact on dollar math:** Structural tokens are re-sent every turn, but after the first turn most of them hit the prompt cache. The token reduction is real regardless. The dollar value depends on what fraction is cached vs. fresh:

| Scenario | Per-turn savings (5,000 tokens removed) | 50-turn session |
|---|---|---|
| All tokens fresh (Opus $5/MTok input) | $0.025 | $1.25 |
| 65% cached (Opus $0.50/MTok cache-read, $5/MTok fresh) | $0.011 | $0.53 |

The 65% cache-hit rate is the observed average across 1,885 sessions. In practice, the split varies by session length and conversation pattern. Structural cleanup also reduces cache-read costs, not just fresh input costs, so both rows are real savings, just at different rates.

## Layer 2: Output Compression (saves per tool result)

Token Optimizer compresses tool outputs through pattern-matched compressor families, not generic summarization. Two distinct mechanisms are at work: compression (shrinking output while preserving information) and context eviction (removing output entirely and storing it locally for on-demand retrieval). Both reduce context window usage, but they work differently.

### Fixture suite: 62 curated test cases

| Category | Fixtures | What's tested |
|---|---|---|
| git | 7 | status, log, diff, clean repo, merge conflicts, non-repo error |
| build | 8 | cargo, make, webpack, tsc, gradle output with warnings/errors |
| lint | 7 | eslint, ruff, clippy, pylint with real violation patterns |
| logs | 7 | nginx, application, docker, systemd log output |
| test runners | 6 | pytest, jest, go test with failures and passes |
| tree / directory | 6 | Large directory listings, nested structures |
| progress / streaming | 3 | npm install, pip, download progress bars |
| security | 3 | AWS keys, GitHub PATs, Slack tokens (must NOT be stripped) |
| error passthrough | 5 | Non-zero exit, permission denied, command not found (must pass through raw) |
| tee-on-failure | 5 | Failed commands preserve full output for debugging |

Each fixture defines:
- **Raw output**: captured from real commands
- **Must-preserve list**: critical information that MUST survive compression
- **Must-not-contain**: fabricated data that must NOT appear (catches hallucination)
- **Minimum compression ratio**: threshold the compressor must meet

A fixture passes only when all three checks hold. This is quality verification that no competing benchmark includes.

### Production compression events (30 days, 908 events, measured)

**Compression** (output is shrunk, information preserved in smaller form):

| Feature | Events | Original tokens | Compressed tokens | Ratio | Tokens saved |
|---|---|---|---|---|---|
| Structure map (re-reads) | 228 | 644,346 | 28,854 | 91.8% | 615,492 |
| Git output | 195 | 409,787 | 67,305 | 73.1% | 342,482 |
| First-read skeleton | 18 | 189,253 | 4,147 | 97.2% | 185,106 |
| Loop detection | 50 | 61,187 | 200 | 99.4% | 60,987 |
| Directory listings | 47 | 30,322 | 16,192 | 28.2% | 14,130 |
| Pytest output | 29 | 7,386 | 259 | 91.9% | 7,127 |
| Delta reads (diff-only) | 5 | 6,499 | 661 | 75.7% | 5,838 |
| Log output | 2 | 544 | 232 | 57.6% | 312 |
| **Compression subtotal** | **574** | **1,349,324** | **117,850** | **91.3%** | **1,231,474** |

**Context eviction** (output replaced by a stub, original stored locally for on-demand retrieval):

| Feature | Events | Tokens removed from context | Mechanism |
|---|---|---|---|
| Tool output archive | 306 | 3,252,146 | Full tool result replaced by ~50-token stub ("archived, use `expand` to retrieve"). Original stored in local SQLite. Model can request the full output back at any time. |
| Checkpoint restore | 12 | 410,503 | Recovered context injected from prior session checkpoints. |

Tool output archive and checkpoint restore are not compression in the shrink-the-output sense. They are context management: removing tokens that are no longer needed from the active window while keeping them retrievable. The token savings are real (those tokens are gone from context), but grouping them with ratio-based compression would be misleading.

**Context eviction subtotal:** 318 events, 3,662,649 tokens removed.

**Combined total: 908 events, 4,887,138 tokens saved, $17.51 measured (30 days).**

### Token counting

All estimates use `bytes / 4` as a BPE proxy. Known error margin: ~15% vs. actual Claude tokenization. Consistent across all measurements. Three-tier accounting ensures measured savings are never conflated with estimates or opportunities.

## Layer 3: First-Read Skeletons (saves per file read)

When a large file is read for the first time and the model is unlikely to edit it soon, Token Optimizer serves a structural skeleton instead of the full file. The full original is archived and expandable on demand.

**Corpus replay (5,814 sessions with reads, 30,771 first-reads analyzed, 2,408 eligible):**

| Language | Size band | First reads | Sessions | Edit-within-5 rate | Avg skeleton ratio | Would-be tokens saved | Promotion status |
|---|---|---|---|---|---|---|---|
| markdown | 16-64KB | 1,329 | 751 | 2.9% | 97.1% | 10,257,707 | PROMOTE |
| python | 16-64KB | 763 | 477 | 1.4% | 96.1% | 6,434,178 | PROMOTE |
| typescript | 16-64KB | 220 | 120 | 0.9% | 97.4% | 1,464,640 | PROMOTE |
| python | 64-256KB | 66 | 64 | 1.5% | 98.5% | 1,504,641 | PROMOTE |
| markdown | 64-256KB | 13 | 9 | 0.0% | 98.9% | 279,176 | hold (low n) |
| json | 64-256KB | 3 | 3 | 0.0% | 99.7% | 90,699 | hold (low n) |
| typescript | 64-256KB | 2 | 2 | 0.0% | 99.1% | 43,533 | hold (low n) |

**Total would-be savings: 20,156,647 tokens.** Accounting tier: **projected** (shadow-mode replay over historical data, not yet actively compressing).

**Promotion status explained:** A cohort graduates from shadow-only to active compression when its edit-within-5-turns rate is below 15% across at least 20 reads in at least 5 distinct sessions. "PROMOTE" means the cohort has met this gate and is eligible for activation. "hold (low n)" means the sample size is too small to be confident. This is a safety mechanism: Token Optimizer proves from your own session history that compression is safe before enabling it for a given file type and size band.

**Safety invariant:** The full original is always archived before any skeleton is served. If the archive fails, the full file is served unchanged (fail-open). The file on disk is never modified.

## Layer 4: Model Routing and Behavioral Coaching (saves per turn)

Output compression saves on the response side. Model routing saves on the request side by ensuring expensive models are only used when they add value.

**From 30-day production data (1,885 sessions):**

| Metric | Value |
|---|---|
| Baseline top-tier model share | 95% |
| Current top-tier model share | 64.7% |
| Routable fraction of turns | 30% |
| Realized routing cost | $56.95 |
| Potential (if fully routed) | $30.01 |

Token Optimizer identifies turns where a cheaper model would produce identical results through 11 anti-pattern detectors. It also fires quality nudges to prevent context degradation before it causes retries.

**Routing applies across model ecosystems.** The Anthropic stack (Opus/Sonnet/Haiku) is the primary platform and the source of these production numbers, but the routing logic applies to any tiered model family. Codex, OpenClaw, and OpenCode each have their own model tiers with similar price spreads. The principle is the same: match model capability to task complexity, and stop paying top-tier prices for grep-and-edit turns.

**Routing math example (Anthropic):** At Opus ($5 input / $25 output per MTok) vs. Haiku ($1 / $5), routing a simple turn from Opus to Haiku saves 80% of that turn's cost. In a session with 20 routable turns, the savings are material.

## Layer 5: Session Continuity (saves per session restart)

When a session compacts or a new session starts on the same project, context is lost. Token Optimizer captures and restores that context through five mechanisms:

**Progressive checkpoints.** Throughout a session, Token Optimizer captures structured checkpoints to local SQLite: key decisions made, errors encountered, file context accumulated, and agent state. These are lightweight snapshots, not full context dumps.

**Checkpoint restore.** When a new session starts or the context window compacts, Token Optimizer keyword-matches against stored checkpoints and injects the relevant ones. The model picks up where it left off instead of re-reading files and re-discovering decisions. Production data: 12 checkpoint restores recovered 410,503 tokens of context (30 days).

**Tool result archive with on-demand retrieval.** Large tool outputs are replaced in-context with a ~50-token stub. The full output is stored locally in SQLite. If the model needs the original data, it calls `expand` to retrieve it. This keeps the context window lean without losing information. Production data: 306 archive events removed 3,252,146 tokens from context (30 days).

**Loop detection.** Token Optimizer detects when the model is repeating the same work (re-reading files it just read, retrying failed commands with no changes). It intervenes to break the loop. Production data: 50 loop detections prevented 60,987 tokens of waste (30 days).

**Quality scoring.** Every session is scored in real-time on 6 signals (stale reads, bloated results, duplicates, compaction depth, decision density, agent efficiency). When quality degrades past thresholds, Token Optimizer fires coaching nudges. This prevents the degradation spiral where a confused model wastes turns and tokens trying to recover.

**Why continuity matters more than compression:** A session that loses context and retries the same work for 10 turns wastes more tokens than any compression could save. Continuity prevents the waste; compression cleans up what remains.

## Compound Effect

These layers do not simply add up. They multiply across a session:

```
Session cost = turns x (overhead + avg_output + routing_premium)
                      + restart_penalty
                      + retry_waste

Token Optimizer reduces:
  overhead         -> Layer 1 (structural audit: -15 to -40%)
  avg_output       -> Layer 2 + 3 (compression + skeletons: -28 to -97% per output)
  routing_premium  -> Layer 4 (model routing: -40 to -80% on routable turns)
  restart_penalty  -> Layer 5 (continuity: checkpoint restore on compaction)
  retry_waste      -> Layer 4 + 5 (loop detection + quality scoring: prevented entirely)
```

**Example: 50-turn Opus session with 15 tool outputs and 1 compaction**

This uses numbers derived from the production data above. Structural savings account for the 65% prompt-cache hit rate.

| Layer | Savings mechanism | Tokens saved | Dollar math |
|---|---|---|---|
| Structural cleanup | 5,000 fewer overhead tokens x 50 turns | 250,000 | 35% fresh at $5/MTok + 65% cached at $0.50/MTok: $1.06 |
| Output compression | 15 outputs x avg 10,000 tokens x 73% ratio (git-level) | 109,500 | Response tokens at $25/MTok: $2.74 |
| First-read skeletons | 3 large files x avg 20,000 tokens x 97% ratio | 58,200 | Mostly fresh input at $5/MTok: $0.29 |
| Context eviction | 4 large tool results archived (avg 10,800 tokens) | 43,200 | Removed from future turn input: $0.15 |
| Continuity restore | 1 compaction, 34,000 tokens recovered from checkpoint | 34,000 | Avoided re-read cost: $0.17 |
| Loop prevention | 2 loops caught x 5 turns x 6,100 tokens | 61,000 | Prevented input + output waste: $0.61 |
| **Total** | | **555,900** | **$5.02** |

Across 30 sessions/month at this profile, that is ~$150/month. Most sessions are shorter and simpler, so real monthly savings depend on workload. The per-category production data in Layers 1-5 is the ground truth.

## Quality Grades (1,885 sessions)

Token Optimizer scores every session on 6 signals: stale reads, bloated results, duplicates, compaction depth, decision density, and agent efficiency.

| Grade | Sessions | Meaning |
|---|---|---|
| S | 38 | Exceptional: minimal waste, high decision density |
| A | 436 | Good: clean context, efficient tool use |
| B | 528 | Normal: some bloat, recoverable |
| C | 264 | Degraded: significant waste, coaching recommended |
| D | 619 | Poor: heavy bloat, likely retries or loops |

These grades are tracked over time so users can see whether their habits (and Token Optimizer's interventions) are improving session efficiency.

## Comparison: What Other Benchmarks Measure

| | Token Optimizer | context-mode | RTK | Headroom |
|---|---|---|---|---|
| **Fixtures / scenarios** | 62 | 21 | N/A | N/A |
| **Historical corpus replay** | 1,885 quality-scored + 5,814 backfill | None | None | None |
| **Quality preservation check** | must-preserve + must-not-contain per fixture | None | None | None |
| **Dollar cost analysis** | Per-session, per-model, per-feature | None | Token counts only | None |
| **Session-level measurement** | Full multi-turn session | Individual outputs | Individual outputs | Individual outputs |
| **Structural overhead audit** | Per-component breakdown | None | None | None |
| **Model routing analysis** | 11 detectors, cost simulation | None | None | None |
| **Continuity measurement** | Checkpoint restore tokens | None | None | None |
| **Behavioral waste prevention** | Loop detection, quality scoring | None | None | None |
| **Cache safety verification** | Never modifies existing prefix | Not measured | Not measured | Not measured |

## Running the Benchmarks

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

## Methodology Notes

- All token counts use `bytes / 4` proxy (~15% error vs. actual BPE). Consistent across all measurements.
- Three-tier accounting: **Measured** (active compression with before/after delta from production events), **Projected** (shadow-mode replay over historical data, not yet actively compressing), **Opportunity** (what would be saved if recommendations are applied). These are never summed together, and each table labels which tier its numbers belong to.
- Prompt-cache savings (cache_read tokens) are never claimed as Token Optimizer savings. The Anthropic cache is free infrastructure; claiming it would be dishonest. However, Token Optimizer's structural and compression savings do reduce cache-read volume, so there is a secondary cache-cost benefit that the dollar math in Layer 1 accounts for explicitly.
- Security fixtures verify that credentials (AWS keys, GitHub PATs, Slack tokens) survive compression intact. Compression must never strip sensitive data that the model needs to see.
- First-read skeleton promotion requires proof from the user's own session history before activating. No cohort is promoted without meeting the edit-rate gate across multiple sessions.
