"""U7 — replay benchmark over historical first-prompts (anti-overfit gate).

Every future scorer tweak is measured, not vibed. Guards against overfitting to
the competitor's fresh-only slice (R7): a pull-only-style silence-on-resume
(recall drop) FAILS; a fresh-direction false-positive rise FAILS.

Three test scenarios:
  1. Baseline run produces a stable metrics file matching the committed baseline.
  2. A deliberately over-tightened threshold (drops resume recall) -> FAILS.
  3. A deliberately loosened threshold (fresh false positives) -> FAILS.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
TO_SCRIPTS = REPO / "skills" / "token-optimizer" / "scripts"
RESUME_SCRIPTS = REPO / "skills" / "resume-checkpoint" / "scripts"
BASELINE = REPO / "tests" / "baselines" / "replay-metrics.json"
for p in (str(SCRIPTS), str(TO_SCRIPTS), str(RESUME_SCRIPTS)):
    if p not in sys.path:
        sys.path.insert(0, str(p))


@pytest.fixture
def m(monkeypatch, tmp_path):
    monkeypatch.setenv("TOKEN_OPTIMIZER_SNAPSHOT_DIR", str(tmp_path))
    if "measure" in sys.modules:
        del sys.modules["measure"]
    mod = importlib.import_module("measure")
    importlib.reload(mod)
    yield mod
    if "measure" in sys.modules:
        del sys.modules["measure"]


@pytest.fixture
def replay(m, monkeypatch, tmp_path):
    cp_dir = tmp_path / "checkpoints"
    cp_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(m, "CHECKPOINT_DIR", cp_dir, raising=True)
    if "pull_checkpoint" in sys.modules:
        del sys.modules["pull_checkpoint"]
    import pull_checkpoint
    importlib.reload(pull_checkpoint)
    if "replay_benchmark" in sys.modules:
        del sys.modules["replay_benchmark"]
    mod = importlib.import_module("replay_benchmark")
    importlib.reload(mod)
    yield mod
    for name in ("replay_benchmark", "pull_checkpoint"):
        if name in sys.modules:
            del sys.modules[name]


# --- T1: baseline run produces a stable metrics file ---

def test_baseline_matches_committed(replay, tmp_path):
    metrics = replay.run_benchmark(tmp_path=str(tmp_path / "replay"))
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    # The key metrics must match the committed baseline. The threshold and
    # bonus are also checked so a calibration change is caught.
    for key in ("resume_recall", "fresh_precision", "incident_pass_rate",
                "mix_weighted_expected_tokens", "threshold", "resume_intent_bonus"):
        assert metrics[key] == baseline[key], (
            f"metric {key!r} drifted: baseline={baseline[key]!r} "
            f"current={metrics[key]!r}")


# --- T2: over-tightened threshold drops resume recall -> FAILS ---

def test_over_tightened_threshold_fails(replay, m, monkeypatch, tmp_path):
    """A threshold so high that resume prompts can't clear it must FAIL the
    regression bars (silence-on-resume is a failing regression, R7)."""
    monkeypatch.setattr(m, "CHECKPOINT_RELEVANCE_THRESHOLD", 0.99, raising=True)
    metrics = replay.run_benchmark(tmp_path=str(tmp_path / "replay"))
    passed, failures = replay.check_regression(metrics)
    assert not passed, (
        f"over-tightened threshold must fail the regression bars; "
        f"resume_recall={metrics['resume_recall']:.2f}")
    assert any("resume_recall" in f for f in failures), (
        f"the failure must cite resume_recall, got: {failures}")


# --- T3: loosened threshold causes fresh false positives -> FAILS ---

def test_loosened_threshold_fails(replay, m, monkeypatch, tmp_path):
    """A threshold so low that fresh prompts match must FAIL the regression
    bars (fresh false-positive rise is a failing regression, R7)."""
    monkeypatch.setattr(m, "CHECKPOINT_RELEVANCE_THRESHOLD", 0.001, raising=True)
    metrics = replay.run_benchmark(tmp_path=str(tmp_path / "replay"))
    passed, failures = replay.check_regression(metrics)
    assert not passed, (
        f"loosened threshold must fail the regression bars; "
        f"fresh_precision={metrics['fresh_precision']:.2f}")
    assert any("fresh_precision" in f for f in failures), (
        f"the failure must cite fresh_precision, got: {failures}")


# --- T4: mix-weighted expected tokens is negative (net savings) ---

def test_mix_weighted_expected_tokens_is_net_savings(replay, tmp_path):
    """With perfect resume recall and fresh precision, the mix-weighted
    expected tokens must be negative (net savings, not net cost)."""
    metrics = replay.run_benchmark(tmp_path=str(tmp_path / "replay"))
    assert metrics["mix_weighted_expected_tokens"] < 0, (
        f"the mix-weighted expected tokens must be net savings (negative); "
        f"got {metrics['mix_weighted_expected_tokens']}")
