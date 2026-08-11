"""U2 — content-based, cwd-free checkpoint relevance scorer (IDF-weighted).

Advances R1 (gate the pointer on relevance) and R4 (score without folder
matching). The scorer tokenizes the opening prompt and the checkpoint sidecar
fields (active_task / topic / decisions / modified_files basenames), weights
overlap by inverse document frequency across the checkpoint pool so generic
words ("the", "run", "fix") don't dominate, sanitizes harness markup out of
sidecar fields before scoring, and treats recency as only a weak prior.

Calibration source for CHECKPOINT_RELEVANCE_THRESHOLD: the U7 replay benchmark
over Alex's real resume/fresh first-prompt mix (tests/baselines/replay-metrics.json).
"""

from __future__ import annotations

import importlib
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "token-optimizer" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


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


def _write_cp(tmp_path, name, active_task=None, topic=None, decisions=None,
              modified_files=None, recent_reads=None, body="", age_seconds=60):
    """Create a checkpoint .md + .json sidecar pair matching the real format."""
    cp = tmp_path / name
    cp.write_text(f"# Session State Checkpoint\n# Generated: test\n{body}\n",
                  encoding="utf-8")
    sidecar = {
        "version": 1,
        "generated": "test",
        "trigger": "stop",
        "session_id": "src-sid",
        "active_task": active_task,
        "topic": topic,
        "decisions": decisions or [],
        "modified_files": [{"path": p, "action": "edit", "range": None}
                           for p in (modified_files or [])],
        "recent_reads": recent_reads or [],
    }
    (tmp_path / cp.name.replace(".md", ".json")).write_text(
        json.dumps(sidecar), encoding="utf-8")
    return cp


def _cp_dict(cp_path, age_seconds=60):
    return {
        "filename": cp_path.name,
        "path": str(cp_path),
        "created": datetime.now() - timedelta(seconds=age_seconds),
        "trigger": "stop",
    }


# --- T1: high topical overlap clears the threshold ---

def test_high_topical_overlap_above_threshold(m, tmp_path):
    cp = _write_cp(tmp_path, "aaaa1111-20260811-120000-checkpoint.md",
                   active_task="fix checkpoint injection targeting in token optimizer",
                   modified_files=["plugins/token-optimizer/scripts/measure.py"])
    score = m.checkpoint_relevance_score(
        "continue the token optimizer checkpoint injection fix", cp, pool=[cp])
    assert score >= m.CHECKPOINT_RELEVANCE_THRESHOLD, (
        f"high topical overlap must clear the threshold; got {score}")


# --- T2: generic-word-only overlap stays low (IDF working) ---

def test_generic_word_only_overlap_stays_low(m, tmp_path):
    # Three checkpoints that all share the generic glue word "work" but each
    # carries a DISTINCTIVE topic that appears in only one of them.
    cp_a = _write_cp(tmp_path, "aaaa1111-20260811-120000-checkpoint.md",
                     active_task="work on token optimizer checkpoint injection",
                     modified_files=["project-a/measure.py"])
    cp_b = _write_cp(tmp_path, "bbbb2222-20260811-120100-checkpoint.md",
                     active_task="work on marketing audit content strategy",
                     modified_files=["project-b/audit.md"])
    cp_c = _write_cp(tmp_path, "cccc3333-20260811-120200-checkpoint.md",
                     active_task="work on billing payment integration",
                     modified_files=["project-c/billing.py"])
    pool = [cp_a, cp_b, cp_c]
    # Generic-only prompt: the only shared word is "work" (low IDF, appears in
    # every checkpoint); "project" misses all. IDF-weighted precision stays low
    # because the matching token is common and the non-matching one is rare.
    generic_score = m.checkpoint_relevance_score("continue work on the project",
                                                 cp_a, pool=pool)
    # Content prompt: names the distinctive topic only cp_a has.
    content_score = m.checkpoint_relevance_score(
        "continue token optimizer checkpoint work", cp_a, pool=pool)
    assert generic_score < m.CHECKPOINT_RELEVANCE_THRESHOLD, (
        f"generic-word-only overlap must stay below threshold; got {generic_score}")
    assert content_score > generic_score, (
        "IDF must rank a content-specific prompt above a generic-only one")
    assert content_score >= m.CHECKPOINT_RELEVANCE_THRESHOLD, (
        f"content-specific overlap must clear the threshold; got {content_score}")


# --- T3: polluted active_task is sanitized before scoring ---

def test_polluted_active_task_sanitized(m, tmp_path):
    polluted = ("<task-notification>system: scheduled task #7 fired</task-notification> "
                "fix checkpoint injection in token optimizer")
    cp = _write_cp(tmp_path, "aaaa1111-20260811-120000-checkpoint.md",
                   active_task=polluted,
                   modified_files=["plugins/token-optimizer/scripts/measure.py"])
    # The harness markup tokens ("task-notification", "scheduled", "fired")
    # must NOT be the reason the score is high. Score the prompt on the REAL
    # content only and confirm it clears; then confirm a prompt that names ONLY
    # the markup noise stays low.
    real = m.checkpoint_relevance_score(
        "continue the token optimizer checkpoint injection fix", cp, pool=[cp])
    noise = m.checkpoint_relevance_score(
        "scheduled task notification fired system", cp, pool=[cp])
    assert real >= m.CHECKPOINT_RELEVANCE_THRESHOLD, (
        f"real content must still score high after sanitization; got {real}")
    assert noise < m.CHECKPOINT_RELEVANCE_THRESHOLD, (
        f"markup noise must not score high after sanitization; got {noise}")


# --- T4: bare "continue" + stale unrelated pool stays below threshold (#129) ---

def test_bare_continue_stale_unrelated_pool_below_threshold(m, tmp_path):
    stale_age = 60 * 60 * 12  # 12h, well past the recency prior window
    cp = _write_cp(tmp_path, "aaaa1111-20260811-000000-checkpoint.md",
                   active_task="unrelated marketing audit work",
                   modified_files=["clients/acme/audit.md"],
                   age_seconds=stale_age)
    score = m.checkpoint_relevance_score("continue", cp, pool=[cp])
    assert score < m.CHECKPOINT_RELEVANCE_THRESHOLD, (
        f"bare continue + stale unrelated pool must stay below threshold (#129); got {score}")


# --- T5: non-UTF-8 checkpoint content scores 0.0 without aborting ---

def test_non_utf8_checkpoint_scores_zero_without_raising(m, tmp_path):
    cp = tmp_path / "aaaa1111-20260811-120000-checkpoint.md"
    # Sidecar is valid UTF-8 JSON; the .md body is a stray-byte (cp1252) blob.
    cp.write_bytes(b"# Session State Checkpoint\n# Generated: test\n\xff\xfe\x80\n")
    sidecar = {"version": 1, "active_task": "token optimizer checkpoint fix",
               "decisions": [], "modified_files": [], "recent_reads": []}
    (tmp_path / cp.name.replace(".md", ".json")).write_text(
        json.dumps(sidecar), encoding="utf-8")
    # Must not raise; the sidecar still carries real content so this scores on
    # the sidecar, but a checkpoint with NO sidecar + non-UTF-8 body scores 0.0.
    no_sidecar = tmp_path / "bbbb2222-20260811-120100-checkpoint.md"
    no_sidecar.write_bytes(b"\xff\xfe\x80\x81")
    # Loop over both: the non-UTF-8-only one must score 0.0 and not abort the loop.
    scores = []
    for path in (cp, no_sidecar):
        try:
            scores.append(m.checkpoint_relevance_score(
                "token optimizer checkpoint fix", path, pool=[cp, no_sidecar]))
        except Exception as exc:  # pragma: no cover - the test is that this never fires
            pytest.fail(f"scorer raised on non-UTF-8 content: {exc!r}")
    assert scores[1] == 0.0, (
        f"checkpoint with no sidecar + non-UTF-8 body must score 0.0; got {scores[1]}")


# --- T6: CJK opening prompt tokenizes without crashing and scores sensibly (#127) ---

def test_cjk_opening_prompt_tokenizes_and_scores(m, tmp_path):
    cp = _write_cp(tmp_path, "aaaa1111-20260811-120000-checkpoint.md",
                   active_task="결제 모듈 리팩터링 및 테스트",
                   modified_files=["src/payment/module.py"])
    try:
        score = m.checkpoint_relevance_score("결제 모듈 리팩터링 다시 시작", cp, pool=[cp])
    except Exception as exc:  # pragma: no cover
        pytest.fail(f"scorer raised on CJK prompt: {exc!r}")
    assert score >= m.CHECKPOINT_RELEVANCE_THRESHOLD, (
        f"CJK topical overlap must clear the threshold; got {score}")


# --- T7: threshold constant is exposed and documented as calibrated ---

def test_threshold_constant_exposed(m):
    assert isinstance(m.CHECKPOINT_RELEVANCE_THRESHOLD, float)
    assert 0.0 < m.CHECKPOINT_RELEVANCE_THRESHOLD < 1.0, (
        "threshold must be a defensible (0,1) constant calibrated via U7 replay")
