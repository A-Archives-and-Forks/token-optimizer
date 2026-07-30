#!/usr/bin/env python3
"""GitHub #103 — per-project scoping for prompt-continuity injection.

A two-project session leaks project A's Key Decisions / Modified Files into a
project B hint because the checkpoint's session-wide fields are dumped verbatim
once the checkpoint clears the same-project gate. The fix is a set-overlap
keep/drop rule (``_keep_recovered_item``) applied to each recovered item BEFORE
the ``[:3]``/``[:5]`` slices, with one disclosure line emitted only when
something is dropped.

These tests cover the three Python surfaces that filter:
  * ``_keep_recovered_item`` / ``_continuity_keep_tokens`` (the decision function)
  * ``build_lean_resume_context`` (the cold-resume-lean block)
  * ``_continuity_prompt_hint`` (the lightweight prompt-continuity hint)

The parity fixture (``PARITY_FIXTURE``) is duplicated verbatim in the OpenClaw
and OpenCode TS scoping tests so all three runtimes' keep/drop decisions are
asserted against the SAME token inputs.
"""

from __future__ import annotations

import importlib
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "token-optimizer" / "scripts"


# ---------------------------------------------------------------------------
# Shared parity fixture — MUST stay byte-identical to the TS scoping tests.
# (item_text, keep_tokens, expected_keep)
# ---------------------------------------------------------------------------

PARITY_FIXTURE = [
    # < 3 distinctive tokens -> inconclusive -> keep (even with zero overlap)
    ("gamma delta", {"alpha", "beta"}, True),
    # >= 3 distinctive tokens, zero overlap -> DROP
    ("refactor gamma delta epsilon module", {"alpha", "beta"}, False),
    # >= 3 distinctive tokens, nonempty overlap -> keep
    ("refactor gamma delta alpha module", {"alpha", "beta"}, True),
    # Full paths are SINGLE tokens with regex [a-zA-Z0-9_./:-]+ (slashes are
    # in the class), so they have < 3 distinctive tokens -> ``_keep_recovered_item``
    # always keeps them. Cross-project FILE paths are dropped by a SEPARATE
    # rule at the filter sites (``_cross_project_file_drop``: an absolute-path
    # prefix check against cwd), NOT by this decision function; the per-item
    # token filter targets session-wide TEXT fields (Key Decisions).
    ("/home/u/alpha/src/main.py", {"alpha", "main"}, True),
    ("/home/u/gamma/src/other.py", {"alpha", "main"}, True),
    # empty item -> keep
    ("", {"alpha"}, True),
]


@pytest.fixture
def measure(monkeypatch):
    """Import measure.py fresh under an isolated snapshot/runtime dir."""
    tmp = tempfile.mkdtemp(prefix="to-103-test-")
    monkeypatch.setenv("TOKEN_OPTIMIZER_SNAPSHOT_DIR", tmp)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", tmp)
    sys.path.insert(0, str(SCRIPTS))
    for _m in ("measure",):
        if _m in sys.modules:
            del sys.modules[_m]
    mod = importlib.import_module("measure")
    importlib.reload(mod)
    cp_dir = Path(tmp) / "checkpoints"
    cp_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(mod, "CHECKPOINT_DIR", cp_dir, raising=True)
    monkeypatch.setattr(mod, "TRENDS_DB", Path(tmp) / "trends.db", raising=True)
    yield mod, cp_dir
    if "measure" in sys.modules:
        del sys.modules["measure"]


def _write_checkpoint(cp_dir, sid, sidecar):
    """Write a checkpoint .md + .json sidecar with the session id in the filename."""
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = f"{sid}-{ts}-auto"
    (cp_dir / f"{base}.md").write_text("# checkpoint\n", encoding="utf-8")
    (cp_dir / f"{base}.json").write_text(json.dumps(sidecar), encoding="utf-8")
    return cp_dir / f"{base}.md"


# ---------------------------------------------------------------------------
# Parity: the decision function on a shared token fixture
# ---------------------------------------------------------------------------

def test_keep_recovered_item_parity_fixture(measure):
    """_keep_recovered_item must match the shared parity fixture exactly."""
    mod, _ = measure
    for item_text, keep_tokens, expected in PARITY_FIXTURE:
        got = mod._keep_recovered_item(item_text, set(keep_tokens))
        assert got is expected, (
            f"item={item_text!r} keep={keep_tokens} expected={expected} got={got}")


def test_keep_recovered_item_no_float_threshold():
    """Sanity: the rule is purely set-overlap, no float math involved."""
    mod = importlib.import_module("measure")
    # A huge item with zero overlap still drops; a tiny item with zero overlap
    # still keeps. No score is computed.
    assert mod._keep_recovered_item("alpha beta gamma delta epsilon zeta", set()) is False
    assert mod._keep_recovered_item("alpha beta", set()) is True


# ---------------------------------------------------------------------------
# build_lean_resume_context — mixed A/B checkpoint queried from B
# ---------------------------------------------------------------------------

def _ab_sidecar(proj_a, proj_b):
    """A checkpoint that touched BOTH project A and project B in one session."""
    return {
        "session_id": "abcdefgh1234",
        "active_task": "work on the beta feature",
        "decisions": [
            # B-overlapping: names beta (cwd basename) -> keep
            "Ship the beta feature behind a feature flag",
            # A-only: names only gamma (project A) -> drop
            "Refactor the gamma delta epsilon module for project alpha",
            # B-overlapping: names a B file stem -> keep
            "Wire beta_router into the request pipeline",
        ],
        "modified_files": [
            {"path": f"{proj_b}/src/beta_router.py"},
            {"path": f"{proj_a}/src/gamma_engine.py"},
        ],
        "recent_reads": [
            f"{proj_a}/docs/gamma_design.md",
            f"{proj_b}/README.md",
        ],
        "git": {"branch": "main", "sha": "abc123"},
    }


def test_lean_resume_keeps_only_b_overlap_and_emits_disclosure(measure):
    """Mixed A/B checkpoint queried from project B drops A-only DECISIONS
    (multi-word, zero overlap with keep_tokens) AND A-only FILE paths
    (cross-project absolute paths not under cwd) and emits exactly ONE
    disclosure line reporting both categories."""
    mod, cp_dir = measure
    proj_b = "/home/u/beta"
    proj_a = "/home/u/gamma"
    _write_checkpoint(cp_dir, "abcdefgh1234", _ab_sidecar(proj_a, proj_b))

    block = mod.build_lean_resume_context(
        "abcdefgh1234", prompt_text="continue the beta work", cwd=proj_b)

    # B-overlapping decisions kept:
    assert "beta feature behind a feature flag" in block
    assert "beta_router" in block
    # A-only DECISION dropped (names only gamma/alpha, zero overlap with
    # keep_tokens={beta, beta_router, ...}):
    assert "gamma delta epsilon" not in block
    # A-only FILE paths dropped (cross-project absolute paths not under cwd):
    assert "gamma_engine" not in block
    assert "gamma_design" not in block
    # B files/reads kept:
    assert "README.md" in block
    # Exactly one disclosure line. Both the A-only decision AND the A-only
    # file paths (modified_files + recent_reads) are dropped, so the
    # disclosure reports both categories:
    assert block.count("- Omitted (scoped to current project):") == 1
    assert "- Omitted (scoped to current project): 1 decision(s), 2 file(s)" in block


def test_lean_resume_single_project_emits_no_disclosure(measure):
    """A single-project checkpoint (everything under cwd) drops nothing and
    emits NO disclosure line. Includes a decision that names NO project token
    so the test genuinely exercises the mixture gate (without the gate the
    token-overlap rule would drop it and mislabel it "different project")."""
    mod, cp_dir = measure
    proj = "/home/u/beta"
    sidecar = {
        "session_id": "abcdefgh1234",
        "active_task": "work on the beta feature",
        "decisions": [
            "Ship the beta feature behind a feature flag",
            "Wire beta_router into the request pipeline",
            # Names NO project token (no beta/beta_router/...): would be
            # dropped by the token-overlap rule alone, but the mixture gate
            # keeps it because the checkpoint is single-project.
            "Switched from REST polling to websocket push",
        ],
        "modified_files": [
            {"path": f"{proj}/src/beta_router.py"},
            {"path": f"{proj}/src/beta_core.py"},
        ],
        "recent_reads": [f"{proj}/README.md"],
        "git": {"branch": "main", "sha": "abc123"},
    }
    _write_checkpoint(cp_dir, "abcdefgh1234", sidecar)

    block = mod.build_lean_resume_context(
        "abcdefgh1234", prompt_text="continue the beta work", cwd=proj)

    assert "beta feature" in block
    assert "beta_router" in block
    assert "beta_core" in block
    # The non-basename decision is kept (single-project -> no filtering):
    assert "Switched from REST polling to websocket push" in block
    assert "- Omitted" not in block


def test_lean_resume_no_filter_when_cwd_absent(measure):
    """Legacy callers (no prompt_text/cwd) get the unfiltered block: A-only
    items survive and NO disclosure line is emitted. Backward compat."""
    mod, cp_dir = measure
    proj_b = "/home/u/beta"
    proj_a = "/home/u/gamma"
    _write_checkpoint(cp_dir, "abcdefgh1234", _ab_sidecar(proj_a, proj_b))

    block = mod.build_lean_resume_context("abcdefgh1234")

    # Everything survives, no disclosure.
    assert "gamma delta epsilon" in block
    assert "gamma_engine" in block
    assert "- Omitted" not in block


# ---------------------------------------------------------------------------
# _continuity_prompt_hint — lightweight hint filters + disclosure
# ---------------------------------------------------------------------------

def test_continuity_prompt_hint_filters_and_discloses(measure, monkeypatch):
    """The lightweight hint applies the same keep/drop filter and emits the
    disclosure line. We force a candidate through the scoring gate by
    stubbing the topic score and same-project check so the rendering path is
    reached deterministically."""
    mod, cp_dir = measure
    proj_b = "/home/u/beta"
    proj_a = "/home/u/gamma"
    _write_checkpoint(cp_dir, "abcdefgh1234", _ab_sidecar(proj_a, proj_b))

    # Force the single checkpoint through: high score, in-project, no external
    # memory (so the full-block threshold stays at 0.5 and 0.9 clears it).
    checkpoints = mod.list_checkpoints()
    assert len(checkpoints) == 1
    cp = checkpoints[0]
    sidecar = mod._read_checkpoint_sidecar(cp["path"])

    monkeypatch.setattr(mod, "_checkpoint_topic_score", lambda text, c, cwd=None: (0.9, sidecar))
    monkeypatch.setattr(mod, "_checkpoint_in_project", lambda sc, c: True)
    monkeypatch.setattr(mod, "_external_memory_present", lambda: False)

    # Non-resume-intent prompt so we exercise the LIGHTWEIGHT hint rendering
    # path (the hint file-filter site), not the resume-intent lean-block path
    # that "continue the beta work" would short-circuit into.
    hint = mod._continuity_prompt_hint(
        prompt_text="beta feature",
        session_id="zzzzzzzzzzzz",  # a different session so the checkpoint isn't skipped
        cwd=proj_b)

    assert "beta feature behind a feature flag" in hint
    assert "beta_router" in hint
    # A-only DECISION dropped from the hint:
    assert "gamma delta epsilon" not in hint
    # A-only FILE path dropped (cross-project absolute path not under cwd):
    assert "gamma_engine" not in hint
    # Exactly one disclosure line. The hint surface filters modified_files
    # only (no recent_reads), so both the A-only decision AND the A-only
    # file path are dropped -> disclosure reports both categories:
    assert hint.count("- Omitted (scoped to current project):") == 1
    assert "- Omitted (scoped to current project): 1 decision(s), 1 file(s)" in hint


def test_continuity_prompt_hint_single_project_no_disclosure(measure, monkeypatch):
    """Lightweight hint on a single-project checkpoint emits NO disclosure.
    Uses a non-resume prompt so the lightweight hint rendering path runs
    (not the resume-intent lean block), and includes a decision that names no
    project token so the mixture gate is genuinely exercised."""
    mod, cp_dir = measure
    proj = "/home/u/beta"
    sidecar = {
        "session_id": "abcdefgh1234",
        "active_task": "work on the beta feature",
        "decisions": [
            "Ship the beta feature behind a feature flag",
            # Names NO project token: would be dropped by the token-overlap
            # rule alone, but the mixture gate keeps it (single-project).
            "Switched from REST polling to websocket push",
        ],
        "modified_files": [{"path": f"{proj}/src/beta_router.py"}],
        "recent_reads": [f"{proj}/README.md"],
        "git": {"branch": "main", "sha": "abc123"},
    }
    _write_checkpoint(cp_dir, "abcdefgh1234", sidecar)
    checkpoints = mod.list_checkpoints()
    cp = checkpoints[0]
    sc = mod._read_checkpoint_sidecar(cp["path"])
    monkeypatch.setattr(mod, "_checkpoint_topic_score", lambda text, c, cwd=None: (0.9, sc))
    monkeypatch.setattr(mod, "_checkpoint_in_project", lambda s, c: True)
    monkeypatch.setattr(mod, "_external_memory_present", lambda: False)

    hint = mod._continuity_prompt_hint(
        prompt_text="beta feature",
        session_id="zzzzzzzzzzzz",
        cwd=proj)

    assert "beta feature" in hint
    assert "beta_router" in hint
    # The non-basename decision is kept (single-project -> no filtering):
    assert "Switched from REST polling to websocket push" in hint
    assert "- Omitted" not in hint


def test_continuity_prompt_hint_no_filter_when_cwd_absent(measure, monkeypatch):
    """Legacy hint caller with cwd=None gets the UNFILTERED hint: A-only
    decisions and file paths survive and NO disclosure line is emitted
    (GitHub #103 #6). The hint surface previously computed keep_tokens
    unconditionally, so cwd=None callers were token-filtered on prompt text
    alone and got a fabricated "scoped to current project" disclosure."""
    mod, cp_dir = measure
    proj_b = "/home/u/beta"
    proj_a = "/home/u/gamma"
    _write_checkpoint(cp_dir, "abcdefgh1234", _ab_sidecar(proj_a, proj_b))
    checkpoints = mod.list_checkpoints()
    cp = checkpoints[0]
    sidecar = mod._read_checkpoint_sidecar(cp["path"])
    monkeypatch.setattr(mod, "_checkpoint_topic_score", lambda text, c, cwd=None: (0.9, sidecar))
    monkeypatch.setattr(mod, "_checkpoint_in_project", lambda s, c: True)
    monkeypatch.setattr(mod, "_external_memory_present", lambda: False)

    hint = mod._continuity_prompt_hint(
        prompt_text="beta feature",
        session_id="zzzzzzzzzzzz",
        cwd=None)

    # A-only decision survives (no token filtering without cwd):
    assert "gamma delta epsilon" in hint
    # A-only file path survives (no path drop without cwd):
    assert "gamma_engine" in hint
    # No fabricated disclosure:
    assert "- Omitted" not in hint
