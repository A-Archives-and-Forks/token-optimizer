# Token Optimizer: Benchmark Report

This report shows what Token Optimizer saved, how every number was measured, and how you reproduce each one on your own data. The figures are one user's 30 days (snapshot ending 2026-06-15); yours will differ, and every tool to regenerate them ships in the repo.

---

## What it saved (30 days)

Two numbers, kept separate on purpose. One is **directly metered** (we logged every event). The other is an **estimate** of your full transformation, grounded in your own history.

| | 30-day savings | What it is |
|---|---|---|
| 🟢 **Measured** | **~$313/mo** | Savings we logged event by event. The proven floor. |
| 🔵 **Transformation** | **~$1,877/mo (~18%)** | Your whole workload, priced the old way vs now. Estimated. |

### The ~$313 measured, broken down

This is the number we can prove. Every dollar was logged as it happened.

| Source | 30-day | How it was measured |
|---|---|---|
| Model routing (realized) | **$260** | Every turn that ran on a lighter model than your baseline, priced at the real rate cards. |
| Compression | **$53** | Every tool output we shrank or evicted, logged with before/after token counts. |
| **Total measured** | **~$313** | |

### The ~$1,877 transformation, broken down

This estimates your *entire* workload, not just the slice we metered. We take the exact volume you moved this period, hold it constant, and price it two ways: how you work now vs how you worked before Token Optimizer. The gap is the transformation.

| Lever | 30-day | |
|---|---|---|
| Model routing + caching (your main sessions) | $1,076 | priced on your billed volume |
| Subagent routing | $741 | priced on sidechain volume |
| Compression add-back | $60 | the metered removals, repriced at your baseline mix |
| **Total transformation** | **~$1,877** | |

> The measured routing ($260) is the **proven slice** of the transformation's routing lever, not a separate number. It is about a quarter of that lever ($1,076); the rest is volume the counterfactual prices but no single event meters. The measured compression ($53) is the metered floor of the $60 add-back. The two tables are the floor and the full picture of the same savings, never summed on top of each other.

---

## How we measured everything

Each number above comes from a specific, reproducible method. Here is each one.

<details>
<summary>Measured compression, $53, logged event by event</summary>

Every time Token Optimizer shrinks or evicts an output, it writes a savings event with the real before/after token counts. Over 30 days (snapshot ending 2026-06-15): **990 events, 14.44M tokens removed, $52.77**.

| Mechanism | Events | Tokens removed | Saved |
|---|---|---|---|
| Tool-output archive | 646 | 9.31M | $27.91 |
| Lean session resumes | 7 | 3.22M | $16.11 |
| Structure maps (re-reads) | 307 | 1.19M | $5.37 |
| Checkpoint restores | 24 | 0.71M | $3.35 |
| Delta reads | 6 | 5.9K | $0.03 |
| **Total** | **990** | **14.44M** | **$52.77** |

Reproduce: `python3 scripts/measure.py compression-stats` (your numbers will differ; this is one 30-day snapshot)

</details>

<details>
<summary>Measured routing, $260, priced at real rate cards</summary>

When a turn runs on a lighter model than your pre-optimization baseline, the difference is priced at the actual published rates (Opus $5/$25 per MTok input/output, versus lighter models) and logged. Over 30 days your top-tier share moved from 95% to 60%, banking $260/mo realized. This is the proven slice of the routing lever in the transformation, never added on top of it.

Reproduce: `python3 scripts/measure.py dashboard` (model-routing panel)

</details>

<details>
<summary>The transformation, $1,877, a current-volume counterfactual</summary>

We take the exact token volume you actually moved over 30 days and hold it constant, then price it two ways:

- **Now:** your real model mix and cache pattern.
- **The old way:** your pre-Token-Optimizer baseline (your own frozen early-usage mix, about 95% Opus here, with your old caching pattern).

The difference is pure efficiency. It is never inflated by doing more work, because the volume is identical on both sides. It sums three non-overlapping pools: your main sessions ($1,076), subagents ($741), and the compression add-back ($60, the metered removals repriced at the baseline mix). Combined actual is about $8,708/mo; combined old-way about $10,585/mo.

The baseline is **yours**, frozen from your own first 30 days of real sessions, never a fabricated number. On non-Anthropic platforms it is priced at your own measured mix.

Reproduce: `python3 scripts/measure.py dashboard` (the headline)

</details>

<details>
<summary>Compression safety, the 57-fixture suite</summary>

Before trusting compression on real output, we validate it never removes what the model needs. The suite holds **57 fixtures, all passing**. Each fixture defines raw output, a must-preserve list, a must-not-contain list (catches hallucination), and a minimum ratio. It passes only when all three hold.

| Group | # | What's tested |
|---|---|---|
| build | 8 | cargo, make, webpack, tsc, gradle |
| git | 7 | status, log, diff, merge conflicts |
| lint | 7 | eslint, ruff, clippy, pylint |
| logs | 7 | nginx, docker, systemd, application |
| tree / directory listings | 7 | large listings, nested structures |
| test runners | 6 | pytest, jest, go test, extensions |
| 🔄 tee-on-failure | 5 | failed commands keep full output |
| progress / installs | 5 | npm, pip, package downloads |
| 🔒 security | 3 | AWS keys, GitHub PATs, Slack tokens (must NOT be stripped) |
| ⚠️ error passthrough | 2 | non-zero exit, permission denied (must pass through raw) |

The last three groups are the load-bearing guardrails. Compression never costs you a credential, an error message, or the output of a failed command.

Reproduce: `python3 scripts/benchmark.py`

</details>

<details>
<summary>Trust tiers, why we never sum estimates with metered dollars</summary>

- **Measured**: directly metered, realized routing plus logged compression events.
- **Estimated**: the transformation counterfactual, grounded in your own behavior.
- **Opportunity**: realizable if you act on a recommendation, never folded into either number.

The measured routing overlaps the transformation's routing lever (proven slice, never added). The measured compression is the floor of the compression add-back. Prompt-cache reads are never claimed as savings, because the cache is free infrastructure.

</details>

---

## How to measure your own

Every figure above regenerates against your own session history. Results will differ, and that is the point.

```bash
# Your headline plus measured savings (the dashboard)
python3 scripts/measure.py dashboard

# Just the live compression numbers
python3 scripts/measure.py compression-stats
python3 scripts/measure.py compression-stats --days 7 --json

# The 57-fixture compression suite (deterministic)
python3 scripts/benchmark.py
python3 scripts/benchmark.py --json

# First-read skeleton analysis (historical corpus)
python3 scripts/compression_backfill.py
```

The dashboard computes your personal counterfactual from your own data. If you installed recently, it shows a "baseline still building" state until your own early window is complete, then the headline appears.

---

## Corpus (snapshot ending 2026-06-15)

| | |
|---|---|
| Sessions analyzed | **684** (30 days), **2,042** all-time |
| Compression events | **990** (30 days) |
| Tokens removed | **14.44M** (30 days) |
| First-reads analyzed | **30,771** (historical skeleton corpus) |
| Compression fixtures | **57**, all passing |
| Avg prompt-cache hit rate | **74.1%** |

Production figures come from Claude Code CLI sessions (the author's primary platform). Quality scoring and savings tracking run on all supported platforms; signal counts vary by platform (3 to 7).

---

## More detail

<details>
<summary>First-read skeletons (code only — 4 cohorts active)</summary>

Large **code** files read for the first time and unlikely to be edited soon are served as a skeleton, with the full original archived and recoverable via `expand`, a ranged Read, or a direct Edit. A file type is only promoted to active serving after proof from real history: edit-within-5-turns under 15%, across 20 or more reads in 5 or more sessions.

Measured across a historical first-read corpus (5,814 sessions, 30,771 reads):

| Language | Size | Reads | Edit rate | Skeleton ratio | Saved |
|---|---|---|---|---|---|
| python | 16-64KB | 763 | 1.4% | 96.1% | 6.4M |
| python | 64-256KB | 66 | 1.5% | 98.5% | 1.5M |
| typescript | 16-64KB | 220 | 0.9% | 97.4% | 1.5M |
| markdown | 16-64KB | 1,329 | 2.9% | 97.1% | 10.3M (now measure-only) |

As of **v5.11.27** the markdown cohorts are demoted to measure-only: a markdown skeleton is headings-only, so it drops load-bearing prose, and the edit-rate gate proves "rarely edited" rather than "prose not needed." Markdown first-reads now return full content (still measured in shadow). Active serving is code-only — Python and TypeScript. A live tripwire watches every active cohort; if its real edit-after-skeleton rate crosses 15%, it auto-demotes. The full original is always archived first (fail-open: if archiving fails, the full file is served unchanged).

</details>

<details>
<summary>Session quality grades (684 sessions, 30 days)</summary>

Every session is scored on 7 signals (context-fill degradation, stale reads, bloated results, compaction depth, decision density, agent efficiency, absolute waste) and graded S through F.

| Grade | Sessions | |
|---|---|---|
| S | 27 | Exceptional |
| A | 144 | Good |
| B | 225 | Normal |
| C | 79 | Degraded, coaching suggested |
| D | 209 | Poor, heavy bloat or retries |
| F | 0 | none observed |

All 2,042 sessions are graded all-time on the same scale, so grades compare across hosts.

</details>

<details>
<summary>Token counting and known measurement gaps</summary>

Token counts use a `bytes / 4` BPE proxy (about 15% error versus actual Claude tokenization), applied consistently so ratios hold.

- **Opus fast-mode is under-counted by about 50%.** Fast mode (2x rate) is not exposed in session logs, so fast-mode sessions are priced at the standard rate until the transcript exposes it.
- **Older Fable 5 sessions recorded $0.** Trend views compute Fable cost at query time, so trend figures are authoritative even where an older stored per-session value reads $0.
- **Cache-health waste is an opportunity-tier heuristic.** Run `cache-report --verbose` to trace any figure to the sessions behind it.

</details>
