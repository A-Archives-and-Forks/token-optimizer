# FINDINGS-C: Container-word / single-client-pool false-positive fix

Prototype branch `fix/checkpoint-injection-verbosity`. All work is on COPIES
under `qa-scratch/`; the production `plugins/token-optimizer/skills/token-optimizer/scripts/measure.py`
was NOT edited. Reproduce with:

```
cd qa-scratch && python3 gen_pools.py && python3 harness.py && python3 regression_check.py
```

## 1. Setup

- **Scorer under test**: `checkpoint_relevance_score` (measure.py ~L28969). Doc
  tokens = sanitized sidecar prose UNION separator-split path WORDS (split on
  `/ \ - _ . : ws`). IDF across pool, capped 3.0. content = IDF-weighted F1
  (precision = matched_plain/prompt_weight; recall = matched_doc_weight/doc_weight,
  doc_weight uses `_path_weight(t)=1+0.5*min(path_tf,8)`). resume bonus =
  `0.15*(0.5+0.5*precision)` when content>0 and `_resume_intent`. Threshold 0.25.
- **Pools** (in `qa-scratch/`):
  - `pool-mixed/` — the 5 real-style checkpoints (gambit/competitor-monitor,
    token-optimizer, attention-span, total-recall, miss-chief) with the real file
    paths from `tests/fixtures/history/openings_and_checkpoints.json`.
  - `pool-gambit-only/` — 10 checkpoints ALL under
    `clients/gambit/Retainer-Deliverables/gambit-<sub>/...` so `gambit`,
    `retainer`, `deliverables` are pool-ubiquitous (IDF ~1.0) while sub-project
    words (`competitor`, `monitor`, `company`, `brain`, `market`, `sweep`, ...)
    are distinctive (IDF ~2.3-3.0).
- **Variants prototyped** (each a full copy of measure.py):
  - `baseline` — production measure.py, unmodified.
  - `fixA` — structural-word STOPLIST, **dropped** from path-derived doc tokens
    via the `_path_topic_words` chokepoint.
  - `fixB` — only the checkpoint's TOP path-TF word(s) get the `_path_weight`
    boost; all others get weight 1.0.
  - `fixC` — resume bonus gated on a path-identity hit (matched token with
    `path_tf>=3` AND `idf>=2.0`).
  - `fixAC` — fixA drop + fixC gate combined.
  - `fixAC2` — **recommended**: structural words RETAINED in doc_tokens (so
    doc_weight + IDF stats are unchanged) but EXCLUDED from the matchable hit
    set, + fixC identity-hit gate on the bonus.

## 2. Comparison matrix (max score over the pool; threshold 0.25)

### Gate-core positives (must stay >= 0.25, winner = expected)

| prompt | pool | exp | base | fixA | fixB | fixC | fixAC | fixAC2 |
|---|---|---|---|---|---|---|---|---|
| continue working on the gambit competitor monitor | mixed | gambit | 0.478 | 0.577 | 0.508 | 0.478 | 0.577 | **0.478** |
| continue working on the gambit competitor monitor | gambit-only | gb01 | 0.497 | 0.553 | 0.529 | 0.497 | 0.553 | **0.497** |
| continue the token optimizer checkpoint work | mixed | tok-opt | 0.391 | 0.421 | 0.448 | 0.391 | 0.421 | **0.391** |
| continue the attention span project | mixed | attn | 0.413 | 0.429 | 0.443 | 0.413 | 0.429 | **0.413** |

(token-optimizer / attention-span on pool-gambit-only are N/A — no such
checkpoint exists there; all variants correctly score 0.050, winner gb01 by
recency only, same as baseline.)

### Gate-verbose (real resume_openings; must stay >= 0.25 on pool-mixed)

| prompt | pool | base | fixA | fixB | fixC | fixAC | fixAC2 |
|---|---|---|---|---|---|---|---|
| let's resume the gambit competitor monitor, grab the latest market sweep | mixed | 0.353 | 0.446 | 0.367 | 0.353 | 0.446 | **0.353** |
| continue where we left off on the attention span benchmarks | mixed | 0.495 | 0.516 | 0.522 | 0.495 | 0.516 | **0.495** |
| continue working on token optimizer for full parity of the recent changes | mixed | 0.280 | 0.297 | 0.319 | 0.280 | 0.297 | **0.280** |

### Container-word false positives (must be < 0.25 on BOTH pools)

| prompt | pool | base | fixA | fixB | fixC | fixAC | fixAC2 |
|---|---|---|---|---|---|---|---|
| continue the retainer deliverables | mixed | 0.395 | 0.050 | 0.417 | 0.395 | 0.050 | **0.050** |
| continue the retainer deliverables | gambit-only | 0.355 | 0.227 | 0.371 | 0.205 | 0.117 | **0.050** |
| continue the clients work | mixed | 0.302 | 0.050 | 0.314 | 0.302 | 0.050 | **0.050** |
| continue the clients work | gambit-only | 0.280 | 0.050 | 0.289 | 0.130 | 0.050 | **0.050** |
| continue the competitor monitor for beta client | mixed | 0.329 | 0.385 | 0.346 | 0.329 | 0.385 | 0.329 |
| continue the competitor monitor for beta client | gambit-only | 0.385 | 0.423 | 0.407 | 0.385 | 0.423 | 0.385 |
| continue the reports | mixed | 0.235 | 0.050 | 0.227 | 0.085 | 0.050 | **0.050** |
| continue the reports | gambit-only | 0.248 | 0.050 | 0.236 | 0.098 | 0.050 | **0.050** |
| continue the references work | mixed | 0.275 | 0.050 | 0.257 | 0.125 | 0.050 | **0.050** |
| continue the references work | gambit-only | 0.244 | 0.050 | 0.226 | 0.094 | 0.050 | **0.050** |
| let's resume the retainer | mixed | 0.152 | 0.050 | 0.164 | 0.152 | 0.050 | **0.050** |
| let's resume the retainer | gambit-only | 0.130 | 0.119 | 0.139 | 0.130 | 0.119 | **0.050** |
| continue the scripts | mixed | 0.230 | 0.050 | 0.228 | 0.080 | 0.050 | **0.050** |
| continue the scripts | gambit-only | 0.248 | 0.050 | 0.237 | 0.098 | 0.050 | **0.050** |
| continue the config | mixed | 0.235 | 0.050 | 0.227 | 0.085 | 0.050 | **0.050** |
| continue the config | gambit-only | 0.294 | 0.275 | 0.307 | 0.144 | 0.125 | **0.050** |
| continue the company brain | mixed | 0.334 | 0.206 | 0.338 | 0.334 | 0.099 | **0.085** |
| continue the company brain | gambit-only | 0.408 | 0.437 | 0.432 | 0.408 | 0.437 | 0.322 |
| retainer deliverables competitor monitor reports | mixed | 0.429 | 0.262 | 0.458 | 0.429 | 0.262 | **0.220** |
| retainer deliverables competitor monitor reports | gambit-only | 0.418 | 0.296 | 0.445 | 0.418 | 0.296 | 0.281 |

### Negatives (must be < 0.25)

| label | pool | base | fixA | fixB | fixC | fixAC | fixAC2 |
|---|---|---|---|---|---|---|---|
| #129 bare continue | mixed | 0.050 | 0.050 | 0.050 | 0.050 | 0.050 | **0.050** |
| #129 bare continue | gambit-only | 0.050 | 0.050 | 0.050 | 0.050 | 0.050 | **0.050** |
| cross-client acme | mixed | 0.225 | 0.252 | 0.233 | 0.225 | 0.252 | **0.225** |
| cross-client acme | gambit-only | 0.254 | 0.272 | 0.265 | 0.254 | 0.272 | 0.254 |
| fresh limerick | mixed | 0.050 | 0.050 | 0.050 | 0.050 | 0.050 | **0.050** |
| fresh limerick | gambit-only | 0.050 | 0.050 | 0.050 | 0.050 | 0.050 | **0.050** |

## 3. Pass / fail and why

| variant | verdict | reason |
|---|---|---|
| baseline | FAIL | 12 container FPs clear on both pools (retainer 0.395, clients 0.302, references 0.275, config 0.294, company-brain 0.334/0.408, keyword-stuffed 0.429/0.418, competitor-monitor-beta 0.329/0.385). Confirms the flaw. |
| fixA (drop) | FAIL | Kills the pure-container FPs but (a) **regresses cross-client-acme** 0.225->0.252 (dropping structural words shrinks doc_weight -> inflates recall for the single-word graze on "competitor"), (b) leaves `config` 0.275 on gambit-only (config path_tf 1 but still matched + bonus), (c) leaves company-brain 0.437 and keyword-stuffed 0.262/0.296. |
| fixB (top-tf boost) | FAIL | Almost no effect. `retainer`/`deliverables`/`gambit` are TIED at max path_tf (they recur in every path), so they still get the boost. Container FPs barely move. |
| fixC (identity-hit gate) | FAIL | Kills low-path_tf containers (reports/scripts/config via path_tf<3) and pool-ubiquitous containers in gambit-only (retainer idf 1.0 < 2.0), but `retainer` in pool-mixed has idf 2.1 AND path_tf 7 -> passes the gate -> bonus fires -> 0.395. Statistically identical to a real identity word in a diverse pool. |
| fixAC (drop + gate) | FAIL | Better than fixA alone, but still regresses cross-client-acme (0.252/0.272) for the same doc_weight-shrink reason, and leaves competitor-monitor-beta + company-brain + keyword-stuffed. |
| **fixAC2** | **BEST** | Kills ALL pure-container FPs on BOTH pools, preserves every gate positive (core + verbose) at baseline values, and does NOT regress cross-client-acme (pool-mixed 0.225 == baseline). Residual: 4 cases (see §5). |

**Why fixB is useless here**: in a single-client pool the container words
`retainer`/`deliverables` recur in EVERY path (path_tf == max, tied with the
client name), so "only boost the top path-TF word(s)" boosts them right along
with the real identity. Top-path-TF cannot separate a boilerplate dir that
appears in every path from the project name that appears in every path.

**Why fixC alone is insufficient**: in a DIVERSE pool a boilerplate container
(`retainer`) is unique to the one client that uses it, so it has high IDF and
high path_tf — statistically indistinguishable from the real project identity
word (`competitor`). No frequency signal (path_tf, IDF, or both) separates them.
Only a lexical stoplist can.

**Why the drop variant (fixA/fixAC) regresses cross-client**: removing structural
words from `doc_tokens` shrinks `doc_weight` (the F1 denominator). For a
single-word graze ("competitor analysis for acme corp" hitting gambit on
`competitor` only), recall = matched_doc_weight / doc_weight goes UP because the
denominator shrank, so the cross-client score rises above threshold. fixAC2
avoids this by keeping structural words IN `doc_tokens`/`doc_weight` and only
removing them from the matchable `hits` set.

## 4. Recommended fix: fixAC2

**Algorithm (pseudocode):**

```
STRUCTURAL = {retainer, deliverables, clients, client, projects, project,
              sessions, session, reports, report, references, reference,
              scripts, script, config, configs, src, lib, libs, utils, core,
              build, plans, plan, docs, doc, tests, test, output, outputs,
              data, system, automation, company}   # boilerplate path segments

# inside checkpoint_relevance_score, after pool IDF + path_tf are computed:
hits = prompt_tokens & doc_tokens
hits = {t for t in hits if t not in STRUCTURAL}      # FIX A2: never match on a container word
# doc_weight still sums over the FULL doc_tokens (structural words retained) -> IDF + recall-for-grazes unchanged

# resume bonus gated on a genuine path-identity hit (FIX C):
identity_hit = any(path_tf[t] >= 3 and idf[t] >= 2.0 for t in hits)
if content_score > 0 and resume_intent(text) and identity_hit:
    score += 0.15 * (0.5 + 0.5 * precision)
```

Two env-tunable bars: `_FIXC_PATH_TF_BAR=3`, `_FIXC_IDF_BAR=2.0`.

**Concrete diff to production `measure.py`** (3 edits, ~30 lines):

Edit 1 — after `_RELEVANCE_PATH_TF_CAP` (L28852), add the FIX C bars:
```python
_FIXC_PATH_TF_BAR = _int_env("TOKEN_OPTIMIZER_FIXC_PATH_TF_BAR", 3)
_FIXC_IDF_BAR = _float_env("TOKEN_OPTIMIZER_FIXC_IDF_BAR", 2.0)
```

Edit 2 — after `_PATH_WORD_SPLIT_RE` (L28894), add the structural set:
```python
_STRUCTURAL_PATH_WORDS = frozenset({
    "retainer", "deliverables", "clients", "client", "projects", "project",
    "sessions", "session", "reports", "report", "references", "reference",
    "scripts", "script", "config", "configs", "src", "lib", "libs", "utils",
    "core", "build", "plans", "plan", "docs", "doc", "tests", "test", "output",
    "outputs", "data", "system", "automation", "company",
})
```

Edit 3 — at L29045, change:
```python
        hits = prompt_tokens & doc_tokens
```
to:
```python
        hits = prompt_tokens & doc_tokens
        hits = {t for t in hits if t not in _STRUCTURAL_PATH_WORDS}
```

Edit 4 — in the resume-bonus block (L29093-29111), gate the bonus on an
identity hit. Replace the body inside `if _resume_intent(...):` so that the
`score += ...` line only runs when an identity hit exists:
```python
                _identity_hit = False
                if pool_docs and path_tf:
                    for _t in hits:
                        if path_tf.get(_t, 0) >= _FIXC_PATH_TF_BAR and \
                                _idf(_t) >= _FIXC_IDF_BAR:
                            _identity_hit = True
                            break
                if _identity_hit:
                    _f = _RELEVANCE_RESUME_BONUS_PRECISION_FLOOR
                    score += _RELEVANCE_RESUME_INTENT_BONUS * (_f + (1.0 - _f) * precision)
```

(The full edited functions are in `qa-scratch/measure_fixAC2.py` for reference.)

**Why this is the cleanest option available:**
- It is the only variant that suppresses the container FPs on BOTH pools without
  regressing the existing cross-client guard (pool-mixed acme stays 0.225).
- The hit-exclusion is surgical: doc_weight, pool IDF, and the path-weight boost
  are all unchanged, so every score that does NOT involve a structural word is
  byte-for-byte the baseline (gate positives identical: 0.478 / 0.391 / 0.413).
- The identity-hit gate is principled (frequency-based, no stoplist): it removes
  the bonus from low-path_tf dir grazes and pool-ubiquitous containers that the
  stoplist happens to miss.
- The stoplist is the irreducible lexical piece. §3 shows no frequency signal
  separates `retainer` from `competitor` in a diverse pool, so a small,
  domain-grounded stoplist of boilerplate path segments is unavoidable for the
  pure-container FPs. It is scoped to PATH-derived tokens only (prose is
  untouched) and the words are structural directory names, not project names.

## 5. Why the 3 gate positives do NOT regress (token-level proof)

For each gate positive, the carrying tokens are NOT in `_STRUCTURAL_PATH_WORDS`
and clear the identity-hit gate (`path_tf>=3` AND `idf>=2.0`), so the bonus
fires and the score is identical to baseline:

1. **"continue working on the gambit competitor monitor" -> gambit (0.478)**
   - Carrying tokens: `competitor` (path_tf 7, idf 2.10 in pool-mixed) and
     `monitor` (path_tf 7, idf 2.10). Both >= 3 and >= 2.0 -> identity hit.
   - `gambit` is also matched (not structural) and adds to precision.
   - fixAC2 score == baseline == 0.478 (winner aa11bb22). On pool-gambit-only,
     competitor/monitor idf 2.30 (df 2) -> still identity -> 0.497 (winner gb01).

2. **"continue the token optimizer checkpoint work" -> token-optimizer (0.391)**
   - Carrying tokens: `token` (path_tf 3, idf 2.10) and `optimizer` (path_tf 3,
     idf 2.10). path_tf 3 >= 3 and idf 2.10 >= 2.0 -> identity hit.
   - `checkpoint` (path_tf 1) matches but is not the identity carrier; the gate
     is satisfied by `token`/`optimizer`.
   - fixAC2 == baseline == 0.391 (winner aa22cc33).

3. **"continue the attention span project" -> attention-span (0.413)**
   - Carrying tokens: `attention` (path_tf 5, idf 2.10) and `span` (path_tf 5,
     idf 2.10) -> identity hit.
   - `project` (prompt) is structural -> excluded from hits, but it was never in
     the doc anyway (doc has `projects`, dropped... retained but `project` !=
     `projects`), so excluding it changes nothing. precision is carried by
     attention/span.
   - fixAC2 == baseline == 0.413 (winner aa33dd44).

Verbose resume_openings also hold (all >= 0.25 on pool-mixed, identical to
baseline): market-sweep 0.353, attention-benchmarks 0.495, token-optimizer-parity
0.280. The parity one (lowest, 0.280) survives because `token`/`optimizer` still
provide the identity hit even though `parity`/`recent`/`changes` are unmatched.

**Existing test suite**: `tests/test_relevance_scorer.py` (9 tests) run against
fixAC2 via a temp scripts dir -> **9 passed**. Replicated assertions
(T1/T2/T3/T4/T8 + genuine_broad_resume) also all PASS (`regression_check.py`).
T8's `_big_doc_cp` uses `src/payments/...` paths; `src` is structural but the
stuffed prompt (`stripe webhook ledger`) never names `src`, and `src` stays in
doc_weight, so T8 is unaffected.

## 6. Residual edge cases fixAC2 still misses

Four prompts still clear 0.25 with fixAC2. None is a regression (all are <=
baseline), and two are pre-existing baseline failures, but they are not fully
fixed:

1. **"continue the competitor monitor for beta client" (0.329 / 0.385).**
   This names the REAL sub-project identity (`competitor`+`monitor`, both
   path_tf 7, high IDF) of the gambit competitor-monitor checkpoint, plus an
   unmatched different-client marker (`beta`, `client`). The content_score
   (F1) alone is ~0.28 because the two identity words have huge path_weight and
   dominate recall; precision is only ~0.41 (2 of 4 tokens match) but F1 with
   high recall still clears. This is a cross-client-SUB-PROJECT-NAME case: the
   project name is shared across clients. Killing it requires treating
   unmatched high-IDF prompt tokens as stronger negative evidence, i.e. moving
   content_score from F1 toward a precision-preferential blend (Fix D). That
   reform risks the verbose gate positives (e.g. token-optimizer-parity, which
   also has 2 strong matches + 3 unmatched padding words, currently 0.280), so
   it is out of scope for the clean fix and left as a documented residual.

2. **"continue the company brain" on pool-gambit-only (0.322).**
   `company` is structural (excluded), but `brain` is a genuine identity word
   for the `gambit-company-brain` checkpoint (gb02, path_tf 3, idf 2.30) -> it
   clears the identity gate and matches. In pool-mixed, where `company-brain`
   is only an incidental single-path reference in the gambit checkpoint
   (brain path_tf 1), it is correctly killed (0.085). On pool-gambit-only the
   company-brain PROJECT actually exists, so matching it is arguably a TRUE
   positive, not a false positive. Forcing it under 0.25 would require
   `brain` (path_tf 3) to fail the gate, which means raising `_FIXC_PATH_TF_BAR`
   to 4, which in turn breaks the token-optimizer gate positive (`token`
   path_tf 3). Not worth the regression.

3. **"retainer deliverables competitor monitor reports" on pool-gambit-only
   (0.281).** Keyword-stuffed FRESH prompt (no resume cue -> no bonus). The
   structural words are excluded from hits, leaving `competitor`+`monitor`
   (identity, path_tf 7). content_score (F1) ~0.23 + recency 0.05 = 0.28. This
   is a pre-existing D3 stuffing-defense limitation (baseline was 0.418;
   fixAC2 improved it to 0.281 but not under 0.25): when the stuffed words
   include the project's ultra-high-path-weight identity words, F1 recall stays
   high enough that the 0.05 recency prior tips it over. On pool-mixed it IS
   killed (0.220). A higher recency floor or a no-resume-cue recency cap would
   fix it but changes global calibration.

4. **"continue the competitor analysis for acme corp" on pool-gambit-only
   (0.254).** Pre-existing baseline failure (baseline 0.254, fixAC2 0.254 —
   NOT a regression). In pool-gambit-only `competitor` has df 2 (gb01 +
   gb02's competitor-monitor subdir) so its IDF is lower (2.30) and the
   single-word graze + recency barely clears. On pool-mixed (the real pool)
   it is 0.225 < 0.25, preserved by fixAC2.

**Brittleness note (Fix A stoplist)**: a project literally named one of the
stoplist words (e.g. a "reports" or "config" project) would have its identity
starved. Mitigation: the stoplist is scoped to path-derived tokens only, the
words are generic structural directory names (not plausible project names in
this repo's `clients/<x>/Retainer-Deliverables/<project>/` and
`PROJECTS/<project>/` layout), and the list is small and auditable. If a real
project ever collides, remove that one word from the set.
