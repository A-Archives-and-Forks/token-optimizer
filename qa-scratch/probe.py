"""Probe the checkpoint relevance scorer for the container-word flaw.

Imports measure.py (production code, READ-ONLY), builds the synthetic pools,
scores a battery of vague container-word-only resume prompts against EACH
checkpoint, and prints the full per-checkpoint score table plus an IDF /
precision / recall / bonus breakdown for the worst false positives.

Does NOT modify measure.py. All scratch output goes to stdout.
"""
from __future__ import annotations

import math
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
SCRIPTS = REPO / "plugins" / "token-optimizer" / "skills" / "token-optimizer" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import measure as M  # noqa: E402

THRESH = M.CHECKPOINT_RELEVANCE_THRESHOLD

PROMPTS = [
    # vague, container-word-only, resume-cued
    "continue the retainer deliverables",
    "continue the clients work",
    "continue the competitor monitor for beta client",
    "continue the reports",
    "continue the references work",
    "continue working on the deliverables",
    "let's resume the retainer",
    "continue the scripts",
    "continue the config",
    "continue the company brain",
    # keyword-stuffed, NO resume cue
    "retainer deliverables competitor monitor reports",
    # control positives (must still hit)
    "continue working on the gambit competitor monitor",
    "continue the gambit company brain",
    # control negative
    "write a limerick about cats",
]


def _pool_paths(dirpath: Path):
    return sorted(str(p) for p in dirpath.glob("*-checkpoint.md"))


def _short(path: str) -> str:
    return Path(path).name.replace("-checkpoint.md", "").replace(".md", "")


# --- instrumentation: recompute the internals for a (prompt, cp) pair --------

def _idf_table(pool_paths):
    df = {}
    pool_docs = []
    for pp in pool_paths:
        pdoc = M._checkpoint_sidecar_doc_tokens(pp)
        pool_docs.append(pdoc)
        for t in pdoc:
            df[t] = df.get(t, 0) + 1
    n = len(pool_docs)

    def _idf(t):
        d = df.get(t, 0)
        return min(math.log((n + 1) / (d + 1)) + 1.0, M._RELEVANCE_IDF_CAP)

    return df, n, _idf, pool_docs


def instrument(prompt, cp_path, pool_paths):
    """Return a dict with the full breakdown of how the score was built."""
    df, n, _idf, pool_docs = _idf_table(pool_paths)
    prompt_tokens = M._topic_tokens(prompt, M._RESUME_TOPIC_STOPWORDS)
    _split_extra = set()
    for _t in prompt_tokens:
        if any(_c in _t for _c in "\\/-_.:"):
            _split_extra |= {
                w for w in M._PATH_WORD_SPLIT_RE.split(_t)
                if w and w not in M._RESUME_TOPIC_STOPWORDS and M._topic_token_kept(w)
            }
    prompt_tokens = prompt_tokens | _split_extra
    doc_tokens = M._checkpoint_sidecar_doc_tokens(cp_path)
    path_tf = M._checkpoint_path_tf(cp_path)

    def _path_weight(t):
        tf = path_tf.get(t, 0)
        if tf <= 0:
            return 1.0
        return 1.0 + M._RELEVANCE_PATH_TF_WEIGHT * min(tf, M._RELEVANCE_PATH_TF_CAP)

    hits = prompt_tokens & doc_tokens
    matched_plain = sum(_idf(t) for t in hits)
    prompt_weight = sum(_idf(t) for t in prompt_tokens) or 1.0
    matched_doc_weight = sum(_idf(t) * _path_weight(t) for t in hits)
    doc_weight = sum(_idf(t) * _path_weight(t) for t in doc_tokens) or 1.0
    precision = matched_plain / prompt_weight if prompt_weight else 0.0
    recall = matched_doc_weight / doc_weight if doc_weight else 0.0
    content = (2 * precision * recall / (precision + recall)
               if (precision + recall) > 0 else 0.0)
    resume = M._resume_intent(prompt)
    bonus = 0.0
    if content > 0 and resume:
        _f = M._RELEVANCE_RESUME_BONUS_PRECISION_FLOOR
        bonus = M._RELEVANCE_RESUME_INTENT_BONUS * (_f + (1.0 - _f) * precision)
    recency = M._RELEVANCE_RECENCY_BONUS if doc_tokens else 0.0
    total = min(content + bonus + recency, 1.0)

    hit_details = sorted(
        ((t, round(_idf(t), 3), df.get(t, 0), n, round(_path_weight(t), 2),
          path_tf.get(t, 0)) for t in hits),
        key=lambda x: -x[1])
    miss_details = sorted(
        ((t, round(_idf(t), 3), df.get(t, 0)) for t in (prompt_tokens - doc_tokens)),
        key=lambda x: -x[1])
    return {
        "prompt_tokens": sorted(prompt_tokens),
        "n_pool": n,
        "hits": hit_details,
        "misses": miss_details,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "content_score": round(content, 4),
        "resume_cue": resume,
        "resume_bonus": round(bonus, 4),
        "recency_bonus": round(recency, 4),
        "total": round(total, 4),
    }


def run_pool(label, dirpath):
    pool = _pool_paths(dirpath)
    print("=" * 88)
    print(f"POOL: {label}  ({len(pool)} checkpoints)   threshold={THRESH}")
    print("=" * 88)
    results = []
    for prompt in PROMPTS:
        scores = [(cp, M.checkpoint_relevance_score(prompt, cp, pool=pool))
                  for cp in pool]
        scores.sort(key=lambda x: -x[1])
        best_cp, best_score = scores[0]
        verdict = "FP" if (best_score >= THRESH and "cats" not in prompt
                           and "gambit" not in prompt.lower()) else (
                   "OK+" if best_score >= THRESH else "OK-")
        # override verdict for control positives
        if "gambit" in prompt.lower() and best_score >= THRESH:
            verdict = "OK+"
        if "cats" in prompt and best_score < THRESH:
            verdict = "OK-"
        if "cats" in prompt and best_score >= THRESH:
            verdict = "FP!!"
        results.append((prompt, best_cp, best_score, verdict, scores))
        print(f"\nPROMPT: {prompt!r}")
        print(f"  cue={M._resume_intent(prompt)}  best={_short(best_cp)} "
              f"score={best_score:.4f}  verdict={verdict}")
        print("  per-checkpoint:")
        for cp, sc in scores:
            flag = " <== BEST" if cp == best_cp else ""
            mark = " *** CLEARS" if sc >= THRESH else ""
            print(f"    {sc:.4f}  {_short(cp):<10}{mark}{flag}")

    # worst FPs = container-word prompts that cleared, sorted by score desc
    fps = [(p, cp, s, v, sc) for (p, cp, s, v, sc) in results
           if v == "FP" and s >= THRESH]
    fps.sort(key=lambda x: -x[2])
    print("\n" + "-" * 88)
    print(f"FALSE POSITIVES (container-word-only prompts that cleared {THRESH}): "
          f"{len(fps)}")
    print("-" * 88)
    for i, (p, cp, s, v, sc) in enumerate(fps[:3], 1):
        info = instrument(p, cp, pool)
        print(f"\n  WORST FP #{i}: {p!r}")
        print(f"    matched checkpoint: {_short(cp)}   total={info['total']}")
        print(f"    prompt_tokens: {info['prompt_tokens']}")
        print(f"    pool size n={info['n_pool']}")
        print(f"    HIT tokens (token, idf, df, n, path_weight, path_tf):")
        for t, idf, dfv, nv, pw, tfv in info["hits"]:
            print(f"        {t:<14} idf={idf:<6} df={dfv}/{nv}  "
                  f"path_weight={pw:<5} path_tf={tfv}")
        print(f"    MISS tokens (token, idf, df):")
        for t, idf, dfv in info["misses"]:
            print(f"        {t:<14} idf={idf:<6} df={dfv}/{info['n_pool']}")
        print(f"    precision={info['precision']}  recall={info['recall']}  "
              f"content={info['content_score']}")
        print(f"    resume_cue={info['resume_cue']}  "
              f"resume_bonus={info['resume_bonus']}  "
              f"recency_bonus={info['recency_bonus']}")
        print(f"    total={info['total']}  threshold={THRESH}  "
              f"margin=+{info['total']-THRESH:.4f}")
    return results


if __name__ == "__main__":
    g_res = run_pool("GAMBIT-ONLY (single-client)", ROOT / "pool-gambit-only")
    m_res = run_pool("MIXED (multi-project)", ROOT / "pool-mixed")

    # summary table
    print("\n" + "#" * 88)
    print("SUMMARY: prompt -> best checkpoint -> score -> verdict")
    print("#" * 88)
    print(f"\n{'PROMPT':<52} {'POOL':<12} {'BEST':<10} {'SCORE':<8} {'VERDICT'}")
    print("-" * 100)
    for (p, cp, s, v, _), (pm, cpm, sm, vm, _) in zip(g_res, m_res):
        print(f"{p:<52} {'gambit':<12} {_short(cp):<10} {s:<8.4f} {v}")
        print(f"{'':<52} {'mixed':<12} {_short(cpm):<10} {sm:<8.4f} {vm}")
