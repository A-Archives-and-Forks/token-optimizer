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
    # Directory names are deliberately NON-topical (alpha/beta/gamma) so the
    # prompt word "project" genuinely misses every doc. The scorer now splits
    # path DIRECTORY segments into topic words (a real checkpoint's identity
    # lives in its dirs, e.g. clients/acme/...), so a dir literally named
    # "project-*" would inject "project" into the docs and defeat the very point
    # of this test -- that a generic word carries no signal.
    cp_a = _write_cp(tmp_path, "aaaa1111-20260811-120000-checkpoint.md",
                     active_task="work on token optimizer checkpoint injection",
                     modified_files=["alpha/measure.py"])
    cp_b = _write_cp(tmp_path, "bbbb2222-20260811-120100-checkpoint.md",
                     active_task="work on marketing audit content strategy",
                     modified_files=["beta/audit.md"])
    cp_c = _write_cp(tmp_path, "cccc3333-20260811-120200-checkpoint.md",
                     active_task="work on billing payment integration",
                     modified_files=["gamma/billing.py"])
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


# --- T8: adversarial keyword-stuffing cannot game the score above threshold (D3) ---

def _big_doc_cp(tmp_path):
    """A checkpoint whose sidecar carries MANY distinctive topic tokens."""
    return _write_cp(
        tmp_path, "aaaa1111-20260811-120000-checkpoint.md",
        active_task=("Refactor payment gateway reconcile stripe webhook retries migrate "
                     "ledger schema backfill invoices harden idempotency reconciliation "
                     "dashboard currency rounding refunds"),
        decisions=["adopt double-entry bookkeeping model",
                   "shard ledger by tenant identifier",
                   "encrypt cardholder tokens at rest",
                   "replay webhooks through durable queue",
                   "expose settlement metrics prometheus exporter"],
        modified_files=["src/payments/gateway.py", "src/payments/ledger.py",
                        "src/payments/webhooks.py", "src/payments/settlement.py",
                        "src/billing/invoices.py", "src/billing/refunds.py"])


def test_keyword_stuffing_cannot_exceed_threshold(m, tmp_path):
    """D3: the OLD scorer was pure precision (hits_weight / prompt_weight), which
    hits 1.0 whenever every prompt token appears in the doc. An adversarial fresh
    opening keyword-stuffed from a large checkpoint's own vocabulary therefore
    scored a perfect 1.0 with NO resume cue. The length-normalized (F1) scorer
    folds in recall, so covering only a sliver of a big checkpoint cannot clear
    the bar."""
    cp = _big_doc_cp(tmp_path)

    # A fresh opening (no resume cue) stuffed with a few of the checkpoint's own
    # distinctive tokens. Every token is present in the doc -> OLD precision = 1.0.
    stuffed = "stripe webhook ledger"
    prompt_tokens = m._topic_tokens(stuffed, m._RESUME_TOPIC_STOPWORDS)
    doc_tokens = m._checkpoint_sidecar_doc_tokens(cp)
    assert prompt_tokens and prompt_tokens.issubset(doc_tokens), (
        "fixture sanity: every stuffed token must be in the doc so OLD precision "
        "would have been a perfect 1.0")

    score = m.checkpoint_relevance_score(stuffed, cp, pool=[cp])
    assert score < m.CHECKPOINT_RELEVANCE_THRESHOLD, (
        f"keyword-stuffed fresh opening must NOT clear the threshold; got {score}")

    # An unrelated opening padded with buzzwords (its own topic + a token that
    # grazes the doc) must also stay well below.
    padded = ("kubernetes helm chart rollout canary istio sidecar mesh "
              "observability grafana stripe")
    padded_score = m.checkpoint_relevance_score(padded, cp, pool=[cp])
    assert padded_score < m.CHECKPOINT_RELEVANCE_THRESHOLD, (
        f"unrelated keyword-padded opening must stay below threshold; got {padded_score}")


def test_genuine_broad_resume_still_clears(m, tmp_path):
    """No over-correction: a genuine resume that covers the checkpoint's real
    topic (not padding) still clears the threshold."""
    cp = _big_doc_cp(tmp_path)
    genuine = ("continue the payment gateway work: the stripe webhook retries, the "
               "ledger schema migration, the invoices backfill and the refunds "
               "reconciliation")
    score = m.checkpoint_relevance_score(genuine, cp, pool=[cp])
    assert score >= m.CHECKPOINT_RELEVANCE_THRESHOLD, (
        f"a genuine broad-coverage resume must still clear the threshold; got {score}")


# --- T7: threshold constant is exposed and documented as calibrated ---

def test_threshold_constant_exposed(m):
    assert isinstance(m.CHECKPOINT_RELEVANCE_THRESHOLD, float)
    assert 0.0 < m.CHECKPOINT_RELEVANCE_THRESHOLD < 1.0, (
        "threshold must be a defensible (0,1) constant calibrated via U7 replay")
