#!/usr/bin/env python3
"""U7 — replay benchmark over historical first-prompts (anti-overfit gate).

Replays all U6 historical first-prompts (resume + fresh + incidents) through the
relevance scorer and emits a diffable metrics report. Guards against overfitting
to the competitor's fresh-only slice (R7): a pull-only-style silence-on-resume
(recall drop) FAILS; a fresh-direction false-positive rise FAILS.

Metrics:
  - resume_recall: fraction of resume openings that matched the right checkpoint
  - fresh_precision: fraction of fresh openings that correctly yielded no match
  - incident_pass_rate: fraction of incidents with the expected outcome
  - mix_weighted_expected_tokens: expected token cost across the real resume/fresh
    mix (resume hit saves ~1500 tok, resume miss wastes ~1500 tok, fresh FP
    wastes ~300 tok, fresh correct rejection costs 0)

Usage:
  python3 scripts/replay_benchmark.py                    # print metrics
  python3 scripts/replay_benchmark.py --json             # emit JSON
  python3 scripts/replay_benchmark.py --baseline <path>  # compare to baseline
  python3 scripts/replay_benchmark.py --write-baseline <path>  # write baseline

Exit code: 0 if metrics meet the regression bars, 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Bootstrap: measure.py lives in the sibling token-optimizer skill's scripts dir.
_HERE = Path(__file__).resolve().parent
_TO_SCRIPTS = _HERE.parent / "skills" / "token-optimizer" / "scripts"
if str(_TO_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_TO_SCRIPTS))
_RESUME_SCRIPTS = _HERE.parent / "skills" / "resume-checkpoint" / "scripts"
if str(_RESUME_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_RESUME_SCRIPTS))

import measure
import pull_checkpoint

FIXTURES = _HERE.parent / "tests" / "fixtures" / "history" / "openings_and_checkpoints.json"

# Token cost model (conservative, from the plan's incident data):
#  - Resume hit (correct checkpoint matched): model saves ~1500 tokens (efficient
#    resume via pointer instead of re-deriving context from scratch).
#  - Resume miss (no match when one should match): model wastes ~1500 tokens
#    (re-deriving context that a checkpoint already held).
#  - Fresh false positive: model wastes ~300 tokens (an unnecessary pull tool
#    call or pointer injection that yields nothing useful).
#  - Fresh correct rejection: 0 tokens.
_RESUME_HIT_SAVINGS = 1500
_RESUME_MISS_COST = 1500
_FRESH_FP_COST = 300

# Regression bars (the benchmark FAILS if any of these are not met):
_RESUME_RECALL_BAR = 1.0      # every resume opening must match
_FRESH_PRECISION_BAR = 1.0    # every fresh opening must be rejected
_INCIDENT_PASS_BAR = 1.0      # every incident must pass


def _load_fixtures():
    return json.loads(FIXTURES.read_text(encoding="utf-8"))


def _cp_from_spec(tmp_path, spec):
    filename = spec["filename"]
    cp_path = tmp_path / filename
    if spec.get("corrupt_body"):
        cp_path.write_bytes(b"# Session State Checkpoint\n\xff\xfe\x00bad\n")
    else:
        task = spec.get("active_task") or ""
        cp_path.write_text(
            f"# Session State Checkpoint\n# Generated: test\nbody: {task}\n",
            encoding="utf-8")
    if not spec.get("no_sidecar"):
        sidecar = {
            "version": 1, "trigger": spec.get("trigger", "stop"),
            "session_id": "src-sid",
            "active_task": spec.get("active_task"),
            "decisions": spec.get("decisions", []),
            "modified_files": [{"path": p, "action": "edit", "range": None}
                               for p in spec.get("modified_files", [])],
            "recent_reads": [],
        }
        (tmp_path / cp_path.name.replace(".md", ".json")).write_text(
            json.dumps(sidecar), encoding="utf-8")
    return {
        "filename": filename, "path": str(cp_path),
        "created": datetime.now() - timedelta(seconds=spec.get("age_seconds", 60)),
        "trigger": spec.get("trigger", "stop"),
    }


def _winner_filename(prompt, pool, cwd=None, session_id=None):
    """Return the winning checkpoint's filename, or None for no-match."""
    out = pull_checkpoint.pull_checkpoint(
        prompt, session_id=session_id, cwd=cwd, checkpoints=pool)
    if "No relevant checkpoint found" in out:
        return None
    for cp in pool:
        try:
            sc = measure._read_checkpoint_sidecar(cp["path"])
            if sc and sc.get("active_task", "") and sc["active_task"] in out:
                return cp["filename"]
        except Exception:
            continue
    return "unknown"


def run_benchmark(tmp_path=None):
    """Run the replay benchmark and return a metrics dict."""
    import tempfile
    if tmp_path is None:
        tmp_path = Path(tempfile.mkdtemp(prefix="to-replay-"))
    else:
        tmp_path = Path(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)

    fixture = _load_fixtures()
    pool = [_cp_from_spec(tmp_path, c) for c in fixture["checkpoints"]]

    # Resume direction
    resume_hits = 0
    resume_total = 0
    for o in fixture["resume_openings"]:
        resume_total += 1
        winner = _winner_filename(o["prompt"], pool)
        expected_cp = next(c for c in fixture["checkpoints"]
                           if c["id"] == o["expected_checkpoint_id"])
        if winner == expected_cp["filename"]:
            resume_hits += 1

    # Fresh direction
    fresh_correct_rejections = 0
    fresh_total = 0
    fresh_fps = []
    for o in fixture["fresh_openings"]:
        fresh_total += 1
        winner = _winner_filename(o["prompt"], pool)
        if winner is None:
            fresh_correct_rejections += 1
        else:
            fresh_fps.append(o["id"])

    # Incidents
    incident_passes = 0
    incident_total = 0
    for inc in fixture["incidents"]:
        incident_total += 1
        inc_pool = [_cp_from_spec(tmp_path, c) for c in inc["checkpoints"]]
        winner = _winner_filename(
            inc["prompt"], inc_pool,
            cwd=inc.get("cwd"), session_id=inc.get("session_id"))
        expected = inc["expected"]
        if expected == "no_match":
            if winner is None:
                incident_passes += 1
        elif expected == "match":
            if winner is not None:
                incident_passes += 1
        elif expected == "no_match_or_handled":
            if winner is None or winner == "unknown":
                incident_passes += 1

    resume_recall = resume_hits / resume_total if resume_total else 0.0
    fresh_precision = fresh_correct_rejections / fresh_total if fresh_total else 0.0
    incident_pass_rate = incident_passes / incident_total if incident_total else 0.0

    # Mix-weighted expected tokens: assume a 60/40 resume/fresh mix (from the
    # real history sample). Negative = net savings (good).
    resume_ratio = 0.6
    fresh_ratio = 0.4
    resume_miss_rate = 1.0 - resume_recall
    fresh_fp_rate = 1.0 - fresh_precision
    mix_weighted_expected_tokens = int(
        resume_ratio * (resume_miss_rate * _RESUME_MISS_COST - resume_recall * _RESUME_HIT_SAVINGS)
        + fresh_ratio * (fresh_fp_rate * _FRESH_FP_COST)
    )

    return {
        "resume_recall": round(resume_recall, 4),
        "fresh_precision": round(fresh_precision, 4),
        "incident_pass_rate": round(incident_pass_rate, 4),
        "mix_weighted_expected_tokens": mix_weighted_expected_tokens,
        "resume_hits": resume_hits,
        "resume_total": resume_total,
        "fresh_correct_rejections": fresh_correct_rejections,
        "fresh_total": fresh_total,
        "fresh_false_positives": fresh_fps,
        "incident_passes": incident_passes,
        "incident_total": incident_total,
        "threshold": measure.CHECKPOINT_RELEVANCE_THRESHOLD,
        "resume_intent_bonus": measure._RELEVANCE_RESUME_INTENT_BONUS,
    }


def check_regression(metrics):
    """Return (passed, failures) for the regression bars."""
    failures = []
    if metrics["resume_recall"] < _RESUME_RECALL_BAR:
        failures.append(
            f"resume_recall {metrics['resume_recall']:.2f} < {_RESUME_RECALL_BAR:.2f} "
            f"(silence-on-resume regression)")
    if metrics["fresh_precision"] < _FRESH_PRECISION_BAR:
        failures.append(
            f"fresh_precision {metrics['fresh_precision']:.2f} < {_FRESH_PRECISION_BAR:.2f} "
            f"(fresh false-positive rise)")
    if metrics["incident_pass_rate"] < _INCIDENT_PASS_BAR:
        failures.append(
            f"incident_pass_rate {metrics['incident_pass_rate']:.2f} < {_INCIDENT_PASS_BAR:.2f}")
    return (len(failures) == 0, failures)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Replay benchmark over historical first-prompts (U7, R7).")
    parser.add_argument("--json", action="store_true",
                        help="Emit metrics as JSON instead of human-readable.")
    parser.add_argument("--baseline", default=None,
                        help="Compare metrics to a baseline JSON file.")
    parser.add_argument("--write-baseline", default=None,
                        help="Write the current metrics as a baseline JSON file.")
    args = parser.parse_args(argv)

    metrics = run_benchmark()

    if args.write_baseline:
        p = Path(args.write_baseline)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
        print(f"Baseline written to {p}")
        return 0

    passed, failures = check_regression(metrics)

    if args.baseline:
        baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        if metrics != baseline:
            print("METRICS DRIFTED from baseline:")
            for k in sorted(set(list(metrics.keys()) + list(baseline.keys()))):
                if metrics.get(k) != baseline.get(k):
                    print(f"  {k}: baseline={baseline.get(k)!r} current={metrics.get(k)!r}")
            return 1
        else:
            print("Metrics match baseline.")
            return 0

    if args.json:
        print(json.dumps(metrics, indent=2))
    else:
        print(f"Replay Benchmark (threshold={metrics['threshold']}, bonus={metrics['resume_intent_bonus']})")
        print(f"  resume_recall:       {metrics['resume_recall']:.2f} ({metrics['resume_hits']}/{metrics['resume_total']})")
        print(f"  fresh_precision:     {metrics['fresh_precision']:.2f} ({metrics['fresh_correct_rejections']}/{metrics['fresh_total']})")
        if metrics["fresh_false_positives"]:
            print(f"  fresh_false_positives: {metrics['fresh_false_positives']}")
        print(f"  incident_pass_rate:  {metrics['incident_pass_rate']:.2f} ({metrics['incident_passes']}/{metrics['incident_total']})")
        print(f"  mix_weighted_expected_tokens: {metrics['mix_weighted_expected_tokens']}")
        if not passed:
            print("  REGRESSION:")
            for f in failures:
                print(f"    - {f}")

    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
