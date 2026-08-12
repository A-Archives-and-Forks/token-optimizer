# FINDINGS-A: Checkpoint relevance scorer misfires on generic container words

**Branch:** `fix/checkpoint-injection-verbosity`
**Scorer:** `plugins/token-optimizer/skills/token-optimizer/scripts/measure.py`, `checkpoint_relevance_score()` (line ~28969)
**Threshold to clear:** `0.25`
**Method:** Two synthetic pools + a 14-prompt battery, scored with the production scorer (READ-ONLY import). Full raw output: `qa-scratch/probe-output.txt`. Harness: `qa-scratch/probe.py`. Pools: `qa-scratch/gen_pools.py`.

---

## TL;DR

A vague resume prompt that names **no project and no subproject**, only structural container words ("retainer", "deliverables", "reports", "scripts", "config", "company brain"), routinely clears the 0.25 relevance threshold. The worst single-client false positive reaches **0.588**, more than 2x the threshold (margin +0.338). The precision-scaling fix (commit 2fa0a89) provides **zero protection** here: every false positive has **precision = 1.0** because the prompt is made entirely of container words that all match. There are no unmatched high-IDF tokens to drag precision down. The flaw is **not** unique to single-client pools, it also fires in the mixed multi-project pool (9/11 container prompts clear there too).

---

## Pool construction

### pool-gambit-only (single-client, 10 checkpoints)
All `modified_files` paths live under `clients/gambit/...`. 8 of 10 sit under `clients/gambit/Retainer-Deliverables/gambit-<subproject>/...`; 2 (onboarding, intake) sit directly under `clients/gambit/gambit-<x>/...` so `retainer`/`deliverables` do **not** reach df=n. `active_task` prose never contains the word "gambit", identity lives only in the paths. Resulting document-frequency structure (n=10):

| token | df | IDF |
|---|---|---|
| gambit, clients | 10/10 | 1.000 (in every path, correctly weak) |
| retainer, deliverables | 8/10 | 1.201 |
| reports | 3/10 | 2.012 |
| competitor, monitor | 2/10 | 2.299 |
| company, brain | 1/10 | 2.705 |

### pool-mixed (multi-project, 6 checkpoints)
gambit (2) + token-optimizer + attention-span + personal-OS distractor + marketing-site distractor. Mimics the real pool shape.

---

## Results table: prompt -> best checkpoint -> score -> verdict

`FP` = container-word-only prompt (no project/subproject named) that cleared 0.25. `OK+` = control positive cleared. `OK-` = correctly below bar.

| Prompt | Pool | Best checkpoint | Score | Verdict |
|---|---|---|---|---|
| continue the retainer deliverables | gambit | aa10 (kpi-tracker) | 0.4236 | **FP** |
| continue the retainer deliverables | mixed | bb02 (company-brain) | 0.4746 | **FP** |
| continue the clients work | gambit | aa06 (onboarding) | 0.3210 | **FP** |
| continue the clients work | mixed | bb02 (company-brain) | 0.3474 | **FP** |
| continue the competitor monitor for beta client | gambit | aa02 (competitor-monitor) | 0.4230 | **FP** |
| continue the competitor monitor for beta client | mixed | bb01 (competitor-monitor) | 0.4052 | **FP** |
| continue the reports | gambit | aa09 (brand-audit) | 0.3161 | **FP** |
| continue the reports | mixed | bb01 (competitor-monitor) | 0.3221 | **FP** |
| continue the references work | gambit | aa10 (kpi-tracker) | 0.2796 | **FP** |
| continue the references work | mixed | bb01 (competitor-monitor) | 0.3221 | **FP** |
| continue working on the deliverables | gambit | aa10 (kpi-tracker) | 0.3184 | **FP** |
| continue working on the deliverables | mixed | bb02 (company-brain) | 0.3474 | **FP** |
| let's resume the retainer | gambit | aa10 (kpi-tracker) | 0.1684 | OK- |
| let's resume the retainer | mixed | bb02 (company-brain) | 0.1974 | OK- |
| continue the scripts | gambit | aa10 (kpi-tracker) | 0.3065 | **FP** |
| continue the scripts | mixed | bb03 (token-optimizer) | 0.3546 | **FP** |
| continue the config | gambit | aa10 (kpi-tracker) | 0.3065 | **FP** |
| continue the config | mixed | bb01 | 0.0500 | OK- |
| continue the company brain | gambit | aa03 (company-brain) | 0.5880 | **FP** |
| continue the company brain | mixed | bb02 (company-brain) | 0.5251 | **FP** |
| retainer deliverables competitor monitor reports (no cue) | gambit | aa02 (competitor-monitor) | 0.5589 | **FP** |
| retainer deliverables competitor monitor reports (no cue) | mixed | bb01 (competitor-monitor) | 0.6015 | **FP** |
| continue working on the gambit competitor monitor | gambit | aa02 | 0.5777 | OK+ |
| continue working on the gambit competitor monitor | mixed | bb01 | 0.5930 | OK+ |
| continue the gambit company brain | gambit | aa03 | 0.6438 | OK+ |
| continue the gambit company brain | mixed | bb02 | 0.6297 | OK+ |
| write a limerick about cats | gambit | aa01 | 0.0500 | OK- |
| write a limerick about cats | mixed | bb01 | 0.0500 | OK- |

**Tally (container-word-only prompts, excluding the two that fail on a regex technicality):**
- gambit-only pool: **10 of 11** clear the threshold.
- mixed pool: **9 of 11** clear the threshold.

The two that fail ("let's resume the retainer" in both pools, "continue the config" in mixed) fail only incidentally, not because the flaw is checked: "resume the retainer" does not trip `_RESUME_INTENT_RE` (the regex requires `resume the (work|session|project|task|...)`, not `resume the <noun>`), so it gets no resume bonus and its content alone (0.168-0.197) stays under. "continue the config" in the mixed pool fails only because no mixed checkpoint has `config` in its paths, so there are zero hits. These are not evidence of a working guard.

---

## IDF / precision / recall breakdown for the 3 worst false positives

### Worst FP #1 (gambit-only): "continue the company brain" -> aa03, total = 0.5880

| Component | Value |
|---|---|
| prompt_tokens | `['brain', 'company']` |
| pool size n | 10 |
| HIT: brain | idf=2.705, df=1/10, path_weight=3.0, path_tf=4 |
| HIT: company | idf=2.705, df=1/10, path_weight=3.0, path_tf=4 |
| MISS tokens | **none** |
| precision | **1.0000** |
| recall | 0.2407 |
| content_score (F1) | 0.3880 |
| resume_cue | True |
| resume_bonus | 0.1500 (full: floor 0.5 + 0.5*1.0 = 1.0) |
| recency_bonus | 0.0500 |
| **total** | **0.5880** |
| threshold | 0.25 |
| **margin** | **+0.3380** |

Both tokens are subproject-identity words that appear in only 1 of 10 checkpoints, so IDF is near the 3.0 cap (2.705). The prompt names no project ("gambit" is absent), yet scores 2.35x the threshold. Precision is a perfect 1.0 because there is nothing to miss.

### Worst FP #2 (gambit-only): "retainer deliverables competitor monitor reports" (NO resume cue) -> aa02, total = 0.5589

| Component | Value |
|---|---|
| prompt_tokens | `['competitor','deliverables','monitor','reports','retainer']` |
| HIT: monitor | idf=2.299, df=2/10, path_weight=2.5, path_tf=3 |
| HIT: competitor | idf=2.299, df=2/10, path_weight=2.5, path_tf=3 |
| HIT: reports | idf=2.012, df=3/10, path_weight=1.5, path_tf=1 |
| HIT: retainer | idf=1.201, df=8/10, path_weight=2.5, path_tf=3 |
| HIT: deliverables | idf=1.201, df=8/10, path_weight=2.5, path_tf=3 |
| MISS tokens | **none** |
| precision | **1.0000** |
| recall | 0.3413 |
| content_score (F1) | 0.5089 |
| resume_cue | False |
| resume_bonus | 0.0000 |
| recency_bonus | 0.0500 |
| **total** | **0.5589** |
| **margin** | **+0.3089** |

This clears the bar with **no resume cue at all**, on content alone. The keyword-stuffed prompt is 100% container/path words, none of which is a project name. The D3 "stuffing defense" (F1 + IDF cap) was meant to stop this, but it only bites via recall, and recall here is 0.34 because the matched tokens carry enough weighted mass relative to the doc. The IDF cap (3.0) does not help because the offending tokens are at 1.2-2.3, well under the cap.

### Worst FP #3 (gambit-only): "continue the retainer deliverables" -> aa10, total = 0.4236

| Component | Value |
|---|---|
| prompt_tokens | `['deliverables','retainer']` |
| HIT: retainer | idf=1.201, df=8/10, path_weight=2.5, path_tf=3 |
| HIT: deliverables | idf=1.201, df=8/10, path_weight=2.5, path_tf=3 |
| MISS tokens | **none** |
| precision | **1.0000** |
| recall | 0.1259 |
| content_score (F1) | 0.2236 |
| resume_cue | True |
| resume_bonus | 0.1500 (full) |
| recency_bonus | 0.0500 |
| **total** | **0.4236** |
| **margin** | **+0.1736** |

This is the cleanest demonstration of the priority flaw. `retainer` and `deliverables` are the two most common container words in the pool (df=8/10, the weakest IDF of any matched token at 1.201). The content_score alone is **0.2236, below the threshold**. The resume bonus (0.15, unscaled because precision=1.0) plus recency (0.05) lifts it to 0.4236, comfortably over. The precision-scaling fix is supposed to prevent exactly this, a vague cue riding container words over the bar, but precision is a perfect 1.0 so the bonus is paid in full.

### Worst FP (mixed pool): "retainer deliverables competitor monitor reports" -> bb01, total = 0.6015

For completeness, the mixed pool's worst FP is the same keyword-stuffed prompt, scoring even higher (0.6015) because the smaller pool (n=6) gives the matched tokens higher IDF (competitor/monitor/reports all at 2.253, df=1/6) and the doc is smaller so recall is higher (0.3807). content_score = 0.5515 with no resume cue at all.

---

## Why the precision-scaling fix (commit 2fa0a89) does not help

The fix scales the resume bonus by `FLOOR + (1-FLOOR)*precision` with FLOOR=0.5, so a prompt with low precision gets a reduced bonus. The intent: a prompt that names a *different* project and only grazes a shared container word has unmatched high-IDF mass, which lowers precision, which lowers the bonus.

The failure mode here defeats that logic completely:

1. The prompt is composed **entirely** of container words that exist in the matched checkpoint's doc (`retainer`, `deliverables`, `reports`, `scripts`, `config`, `company`, `brain`).
2. Every prompt token matches. There are **zero miss tokens**.
3. Therefore `precision = matched_plain / prompt_weight = 1.0` in every single false positive measured (see the breakdown tables above).
4. With precision = 1.0, the scaling factor is `0.5 + 0.5*1.0 = 1.0`, the **full** bonus is paid.
5. The bonus (0.15) plus recency (0.05) plus a sub-threshold content_score (as low as 0.2236) clears 0.25.

The fix only fires when there is unmatched distinctive mass. A prompt built from nothing but container words has none, so the fix is inert. The IDF cap (3.0) is also inert: the offending container tokens sit at IDF 1.2-2.7, under the cap.

---

## How high can it go on a prompt that names no project and no subproject?

- **Single-client pool:** up to **0.588** ("continue the company brain"), 2.35x the threshold, margin +0.338. Even the most generic two-word container prompt ("continue the retainer deliverables") reaches **0.424**.
- **Mixed pool:** up to **0.601** (keyword-stuffed, no cue), and **0.525** for a cued two-word container prompt ("continue the company brain").
- **Floor of the failure:** any cued container-word prompt whose tokens all match gets at minimum `content + 0.15 + 0.05`. Because content for an all-matching prompt is `2*1.0*recall/(1.0+recall)`, even a recall as low as 0.13 yields content 0.224, and `0.224 + 0.20 = 0.424`. The scorer effectively treats "every prompt token matched" as strong evidence of relevance regardless of whether those tokens are project identity or generic scaffolding.

---

## Mixed-pool vs single-client

The flaw is **not** confined to single-client pools. In the mixed pool, 9 of 11 container-word prompts still clear, and the worst reaches a higher absolute score (0.6015) than the single-client worst (0.5589 for the same prompt). The multi-project IDF structure does down-weight `gambit` (df=2/6 -> IDF 1.847, vs 1.0 in the single-client pool), which helps the *control positives* rank more cleanly, but it does nothing to stop a prompt that avoids the project name entirely and rides subproject/container words. The single-client pool makes the flaw *worse in breadth* (more checkpoints share the container words, so more clear simultaneously, 8 of 10 at once for "continue the retainer deliverables"), but the mixed pool already exhibits the same root failure.

---

## Artifacts

- `qa-scratch/gen_pools.py` - pool generator (writes the .md/.json pairs, touches mtime to now)
- `qa-scratch/pool-gambit-only/` - 10 single-client checkpoints
- `qa-scratch/pool-mixed/` - 6 multi-project checkpoints
- `qa-scratch/probe.py` - scoring harness + instrumentation (imports measure.py read-only)
- `qa-scratch/probe-output.txt` - full raw run output (463 lines)

No production files were modified. `measure.py` was imported, never written.
