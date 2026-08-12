#!/usr/bin/env python3
"""Comparison harness: baseline vs Fix A/B/C/AC across both pools.

Scores the full prompt battery against every checkpoint in each pool, reports
the MAX score the prompt achieves against the pool (the score that decides
whether a pointer fires) and which checkpoint won. A fix PASSES when:
  - all 3 core gate positives >= 0.25 on pool-mixed (winner = expected)
  - the gambit gate positive >= 0.25 on pool-gambit-only (winner = gb01)
  - verbose resume_openings stay >= 0.25 on pool-mixed (no regression)
  - ALL container-word FPs < 0.25 on BOTH pools
  - #129 bare "continue" < 0.25, cross-client < 0.25, fresh negative < 0.25
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
SCRIPTS = REPO / "plugins" / "token-optimizer" / "skills" / "token-optimizer" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

THRESH = 0.25

VARIANTS = [
    ("baseline", SCRIPTS / "measure.py"),
    ("fixA", ROOT / "measure_fixA.py"),
    ("fixB", ROOT / "measure_fixB.py"),
    ("fixC", ROOT / "measure_fixC.py"),
    ("fixAC", ROOT / "measure_fixAC.py"),
    ("fixAC2", ROOT / "measure_fixAC2.py"),
]
VNAMES = [v[0] for v in VARIANTS]

POOLS = [
    ("pool-mixed", ROOT / "pool-mixed"),
    ("pool-gambit-only", ROOT / "pool-gambit-only"),
]
POOLDIR = dict(POOLS)

# (prompt, mixed_stem, gambit_stem) ; gambit_stem None = N/A for pool-gambit-only
GATE_CORE = [
    ("continue working on the gambit competitor monitor", "aa11bb22", "gb01"),
    ("continue the token optimizer checkpoint work", "aa22cc33", None),
    ("continue the attention span project", "aa33dd44", None),
]
GATE_VERBOSE = [
    ("let's resume the gambit competitor monitor, grab the latest market sweep", "aa11bb22", "gb01"),
    ("continue where we left off on the attention span benchmarks", "aa33dd44", None),
    ("continue working on token optimizer for full parity of the recent changes", "aa22cc33", None),
]

CONTAINER_FPS = [
    "continue the retainer deliverables",
    "continue the clients work",
    "continue the competitor monitor for beta client",
    "continue the reports",
    "continue the references work",
    "let's resume the retainer",
    "continue the scripts",
    "continue the config",
    "continue the company brain",
    "retainer deliverables competitor monitor reports",
]

NEGATIVES = [
    ("#129-bare-continue", "continue"),
    ("cross-client-acme", "continue the competitor analysis for acme corp"),
    ("fresh-limerick", "write a limerick about cats"),
]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def pool_checkpoints(pool_dir):
    return sorted(pool_dir.glob("*.md"))


def score_pool(mod, prompt, pool_dir):
    cps = pool_checkpoints(pool_dir)
    pool_paths = list(cps)
    best = (0.0, None)
    for cp in cps:
        s = mod.checkpoint_relevance_score(prompt, cp, pool=pool_paths)
        if s > best[0]:
            best = (s, cp.stem)
    return best


def main():
    mods = {vname: load_module(f"measure_{vname}", vpath) for vname, vpath in VARIANTS}

    # build row list: (category, prompt, pool, expected_stem_or_None)
    rows = []
    for prompt, ms, gs in GATE_CORE:
        rows.append(("GATE-CORE", prompt, "pool-mixed", ms))
        rows.append(("GATE-CORE", prompt, "pool-gambit-only", gs))
    for prompt, ms, gs in GATE_VERBOSE:
        rows.append(("GATE-VERBOSE", prompt, "pool-mixed", ms))
        rows.append(("GATE-VERBOSE", prompt, "pool-gambit-only", gs))
    for prompt in CONTAINER_FPS:
        for pname, _ in POOLS:
            rows.append(("CONTAINER-FP", prompt, pname, None))
    for label, prompt in NEGATIVES:
        for pname, _ in POOLS:
            rows.append((label, prompt, pname, None))

    results = {}
    for cat, prompt, pname, exp in rows:
        results[(cat, prompt, pname)] = {
            vname: score_pool(mods[vname], prompt, POOLDIR[pname]) for vname in VNAMES
        }

    def fmt(s):
        return f"{s:.3f}"

    def verdict_cell(scores, want_high):
        flags = []
        for vname, s in zip(VNAMES, scores):
            ok = (s >= THRESH) if want_high else (s < THRESH)
            flags.append(f"{vname[:4]}={'OK' if ok else 'NO'}")
        return " ".join(flags)

    print("=" * 120)
    print("CHECKPOINT RELEVANCE SCORER - CONTAINER-WORD FP FIX COMPARISON")
    print(f"threshold = {THRESH}   variants: {', '.join(VNAMES)}")
    print("=" * 120)

    # GATE-CORE
    print("\n## GATE-CORE POSITIVES (must stay >= 0.25; winner = expected checkpoint)")
    hdr = f"{'prompt':50.50} {'pool':16.16} {'exp':9.9} | " + " ".join(f"{v:>7}" for v in VNAMES) + " | winner(baseline..fixAC)"
    print(hdr)
    print("-" * len(hdr))
    for cat, prompt, pname, exp in rows:
        if cat != "GATE-CORE":
            continue
        r = results[(cat, prompt, pname)]
        scores = [r[v][0] for v in VNAMES]
        winners = "/".join((r[v][1][:6] if r[v][1] else "-") for v in VNAMES)
        flag = "" if r["baseline"][0] >= THRESH else "  <-- BASELINE FAIL"
        print(f"{prompt:50.50} {pname:16.16} {exp or 'N/A':9.9} | " + " ".join(f"{fmt(s):>7}" for s in scores) + f" | {winners}{flag}")

    # GATE-VERBOSE
    print("\n## GATE-VERBOSE (real resume_openings; must stay >= 0.25 on pool-mixed, no regression)")
    print(hdr.replace("GATE-CORE", "GATE-VERBOSE"))
    print("-" * len(hdr))
    for cat, prompt, pname, exp in rows:
        if cat != "GATE-VERBOSE":
            continue
        r = results[(cat, prompt, pname)]
        scores = [r[v][0] for v in VNAMES]
        winners = "/".join((r[v][1][:6] if r[v][1] else "-") for v in VNAMES)
        flag = "" if r["baseline"][0] >= THRESH else "  <-- BASELINE FAIL"
        print(f"{prompt:50.50} {pname:16.16} {exp or 'N/A':9.9} | " + " ".join(f"{fmt(s):>7}" for s in scores) + f" | {winners}{flag}")

    # CONTAINER-FP
    print("\n## CONTAINER-WORD FALSE POSITIVES (must be < 0.25 on BOTH pools)")
    hdr2 = f"{'prompt':50.50} {'pool':16.16} | " + " ".join(f"{v:>7}" for v in VNAMES) + " | verdict"
    print(hdr2)
    print("-" * len(hdr2))
    for cat, prompt, pname, exp in rows:
        if cat != "CONTAINER-FP":
            continue
        r = results[(cat, prompt, pname)]
        scores = [r[v][0] for v in VNAMES]
        print(f"{prompt:50.50} {pname:16.16} | " + " ".join(f"{fmt(s):>7}" for s in scores) + f" | {verdict_cell(scores, want_high=False)}")

    # NEGATIVES
    print("\n## NEGATIVES (must be < 0.25)")
    hdr3 = f"{'label':20.20} {'prompt':38.38} {'pool':16.16} | " + " ".join(f"{v:>7}" for v in VNAMES) + " | verdict"
    print(hdr3)
    print("-" * len(hdr3))
    for cat, prompt, pname, exp in rows:
        if cat in ("GATE-CORE", "GATE-VERBOSE", "CONTAINER-FP"):
            continue
        r = results[(cat, prompt, pname)]
        scores = [r[v][0] for v in VNAMES]
        print(f"{cat:20.20} {prompt:38.38} {pname:16.16} | " + " ".join(f"{fmt(s):>7}" for s in scores) + f" | {verdict_cell(scores, want_high=False)}")

    # DETAIL per-checkpoint for gate-core pool-mixed
    print("\n## DETAIL: gate-core winner per-checkpoint scores (pool-mixed)")
    pdir = POOLDIR["pool-mixed"]
    cps = pool_checkpoints(pdir)
    pool_paths = list(cps)
    for prompt, ms, gs in GATE_CORE:
        print(f"\n  prompt: {prompt!r}  (expected {ms})")
        for cp in cps:
            sc = {vname: mods[vname].checkpoint_relevance_score(prompt, cp, pool=pool_paths) for vname in VNAMES}
            mark = "  <-- expected" if cp.stem.startswith(ms) else ""
            print(f"    {cp.stem[:38]:38.38} " + " ".join(f"{v[:4]}={fmt(sc[v]):>6}" for v in VNAMES) + mark)

    # SUMMARY
    print("\n## SUMMARY: does each variant pass all criteria?")

    def evaluate(vname):
        ok = True
        fails = []
        for prompt, ms, gs in GATE_CORE:
            r = results[("GATE-CORE", prompt, "pool-mixed")][vname]
            if r[0] < THRESH:
                ok = False; fails.append(f"gate-core-mixed<{prompt[:24]}={r[0]:.3f}")
            elif not r[1].startswith(ms):
                ok = False; fails.append(f"gate-core-mixed winner {r[1][:6]}!={ms}")
            if gs:
                rg = results[("GATE-CORE", prompt, "pool-gambit-only")][vname]
                if rg[0] < THRESH:
                    ok = False; fails.append(f"gate-core-gambit<{prompt[:24]}={rg[0]:.3f}")
                elif not rg[1].startswith(gs):
                    ok = False; fails.append(f"gate-core-gambit winner {rg[1][:6]}!={gs}")
        for prompt, ms, gs in GATE_VERBOSE:
            r = results[("GATE-VERBOSE", prompt, "pool-mixed")][vname]
            if r[0] < THRESH:
                ok = False; fails.append(f"gate-verbose-mixed<{prompt[:24]}={r[0]:.3f}")
        for prompt in CONTAINER_FPS:
            for pname, _ in POOLS:
                r = results[("CONTAINER-FP", prompt, pname)][vname]
                if r[0] >= THRESH:
                    ok = False; fails.append(f"FP<{prompt[:22]}@{pname[:10]}={r[0]:.3f}")
        for label, prompt in NEGATIVES:
            for pname, _ in POOLS:
                r = results[(label, prompt, pname)][vname]
                if r[0] >= THRESH:
                    ok = False; fails.append(f"NEG<{label[:14]}@{pname[:10]}={r[0]:.3f}")
        return ok, fails

    for vname in VNAMES:
        ok, fails = evaluate(vname)
        print(f"  {vname:9.9}: {'PASS' if ok else 'FAIL'}")
        for f in fails:
            print(f"      - {f}")


if __name__ == "__main__":
    main()
