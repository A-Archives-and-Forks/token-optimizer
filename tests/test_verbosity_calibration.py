"""U5 — verbosity-steer calibration (measure, don't assume).

The old code booked a hardcoded 10-15% x 800-token assumed "saving" the moment a
nudge fired, without measuring the counterfactual. In the competitor's data
output actually ROSE after the nudge. Now: the nudge event books 0 (no
counterfactual yet), and a measured saving is computed later from real
post-nudge output: ``max(0, baseline_avg - actual_post_nudge_avg) x turns``.
Never an estimate, never a negative saving. Advances R5.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "token-optimizer" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

LIVE_SID = "0e37aafe-6625-457c-9d94-68e7ea73e45c"


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


# --- Pure function: measured savings from real post-nudge output ---

def test_output_dropped_books_measured_delta(m):
    # baseline 800; post-nudge turns averaged 450 -> delta 350 x 2 turns = 700.
    saved = m._verbosity_measured_savings(800, [400, 500])
    assert saved == 700, f"must book the measured delta x turns; got {saved}"


def test_output_unchanged_books_zero(m):
    saved = m._verbosity_measured_savings(800, [800, 800])
    assert saved == 0, f"unchanged output must book 0; got {saved}"


def test_output_rose_books_zero_never_negative(m):
    saved = m._verbosity_measured_savings(800, [1000, 1200])
    assert saved == 0, f"output rose must book 0, never negative; got {saved}"


def test_no_post_nudge_data_books_zero(m):
    saved = m._verbosity_measured_savings(800, [])
    assert saved == 0, "no post-nudge data yet must book 0, never an estimate"


# --- Integration: the nudge event itself books 0 (no assumed figure) ---

def _write_transcript(path: Path, outputs):
    """Write a JSONL transcript with assistant entries carrying output_tokens."""
    lines = []
    for o in outputs:
        lines.append(json.dumps({"type": "assistant",
                                 "message": {"usage": {"output_tokens": o}}}))
    path.write_text("\n".join(lines), encoding="utf-8")


def _stub_nudge(m, monkeypatch, tmp_path, transcript_outputs):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"quality-cache-{LIVE_SID}.json"
    cache_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(m, "_quality_cache_path_for", lambda fp=None: cache_path)
    transcript = tmp_path / "transcript.jsonl"
    _write_transcript(transcript, transcript_outputs)
    cache = {"fill_pct": 47, "score": 73, "session_efficiency": 60,
             "nudge_count": 0, "last_nudge_time": 0}
    monkeypatch.setattr(m, "_read_quality_cache", lambda cp: cache)
    monkeypatch.setattr(m, "_find_current_session_jsonl", lambda: transcript)
    logged = []
    monkeypatch.setattr(m, "_log_savings_event",
                        lambda et, ts, **kw: logged.append((et, ts, kw)))
    return transcript, logged


def test_nudge_books_zero_not_assumed(m, monkeypatch, tmp_path):
    transcript, logged = _stub_nudge(m, monkeypatch, tmp_path, [800, 800, 800])
    out = m.run_verbosity_steer(transcript_path=str(transcript),
                                quiet=True, session_id=LIVE_SID)
    assert out, "fixture sanity: the nudge should fire"
    vs_events = [(et, ts) for (et, ts, _) in logged if et == "verbosity_steer"]
    assert vs_events, "a verbosity_steer event must be logged when the nudge fires"
    for et, ts in vs_events:
        assert ts == 0, (
            f"nudge event must book 0 (no counterfactual yet), not an assumed figure; got {ts}")


def test_real_nudge_path_settles_prior_nudge_with_measured_delta(m, monkeypatch, tmp_path):
    """D1: the REAL UserPromptSubmit path must invoke the measure-not-assume
    logic. A prior nudge recorded a pre-nudge baseline; a later run_verbosity_steer
    turn must call _verbosity_measured_savings on the observed post-nudge output
    and book a measured verbosity_steer_measured event (not dead code)."""
    # A prior nudge fired at turn 2 with pre-nudge avg output 800.
    m._log_savings_event(
        "verbosity_steer", 0, session_id=LIVE_SID,
        detail="fill=80% score=70 tier=strong pre_avg_out=800 pre_turns=2")

    # Transcript: 2 pre-nudge turns (800,800) then 2 post-nudge turns (400,500).
    # post avg = 450 -> measured = (800-450) x 2 = 700.
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"quality-cache-{LIVE_SID}.json"
    cache_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(m, "_quality_cache_path_for", lambda fp=None: cache_path)
    monkeypatch.setattr(m, "_read_quality_cache", lambda cp: {
        "fill_pct": 47, "score": 73, "session_efficiency": 60,
        "nudge_count": 0, "last_nudge_time": 0})
    transcript = tmp_path / "transcript.jsonl"
    _write_transcript(transcript, [800, 800, 400, 500])

    # Spy on the measurement to prove the real path invokes it.
    calls = []
    real = m._verbosity_measured_savings
    def _spy(baseline, outs):
        result = real(baseline, outs)
        calls.append((baseline, list(outs), result))
        return result
    monkeypatch.setattr(m, "_verbosity_measured_savings", _spy)

    m.run_verbosity_steer(transcript_path=str(transcript), quiet=True,
                          session_id=LIVE_SID)

    assert calls, "real nudge path must invoke _verbosity_measured_savings (D1: not dead code)"
    assert calls[0] == (800, [400, 500], 700), f"measured delta wrong: {calls[0]}"

    # The measured saving must be booked to the DB.
    conn = m._init_trends_db()
    try:
        row = conn.execute(
            "SELECT tokens_saved FROM savings_events "
            "WHERE event_type='verbosity_steer_measured' AND session_id=?",
            (LIVE_SID,)).fetchone()
    finally:
        conn.close()
    assert row is not None, "a verbosity_steer_measured event must be booked"
    assert row[0] == 700, f"must book the measured 700, got {row[0]}"

    # Idempotent: a second turn must not double-credit the same nudge.
    calls.clear()
    m.run_verbosity_steer(transcript_path=str(transcript), quiet=True,
                          session_id=LIVE_SID)
    conn = m._init_trends_db()
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM savings_events "
            "WHERE event_type='verbosity_steer_measured' AND session_id=?",
            (LIVE_SID,)).fetchone()[0]
    finally:
        conn.close()
    assert n == 1, f"prior nudge must be settled once, not re-credited; got {n} rows"


def test_no_assumed_constant_remains_in_source():
    """The hardcoded 0.10/0.15 x 800 assumed figure must be gone from the
    verbosity nudge path."""
    src = (REPO / "skills" / "token-optimizer" / "scripts" / "measure.py").read_text(
        encoding="utf-8")
    # Pinpoint the verbosity nudge block, not any unrelated 0.10/0.15 literal.
    # The old code: _est_output_reduction = 0.10 if fill_pct < 75 else 0.15
    assert "_est_output_reduction" not in src, (
        "the assumed output-reduction constant must be removed from the nudge path")
    assert "_avg_response_tokens = 800" not in src, (
        "the assumed 800-token response figure must be removed from the nudge path")
