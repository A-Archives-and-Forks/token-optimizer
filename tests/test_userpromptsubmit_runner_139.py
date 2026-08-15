#!/usr/bin/env python3
"""Regression tests for the consolidated UserPromptSubmit dispatcher (issue #139).

The six former UserPromptSubmit hooks.json entries are collapsed into ONE that
runs ``hooks/userpromptsubmit_runner.py``, which imports ``measure.py`` once and
runs all six subcommands in-process with per-subcommand failure isolation.

These four tests pin the three issue #139 deliverables:
  (a) the ``TOKEN_OPTIMIZER_HOOKS_USERPROMPTSUBMIT=0`` pre-import opt-out in
      run.py (Req 3) -- no child process is spawned.
  (b) the single dispatcher runs all six subcommands against one measure.py
      import (Req 2).
  (c) one subcommand throwing never aborts the others; the hook exits 0 and
      logs the failure to stderr (Req 2 failure isolation).
  (d) the per-session marker gate (``measure._ran_once_this_session``) skips the
      three harness-gated subcommands on a latched session while the three
      always-on subcommands still run (Req 2 gating parity).

Run: python3 -m pytest tests/test_userpromptsubmit_runner_139.py -q
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
HOOKS = REPO / "hooks"
RUN_PY = HOOKS / "run.py"
RUNNER = HOOKS / "userpromptsubmit_runner.py"


def _load_run_py():
    """Import hooks/run.py as a fresh module (it has no package-relative imports)."""
    spec = importlib.util.spec_from_file_location("ups_run_py_under_test", RUN_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_runner(monkeypatch, tmp_path):
    """Import hooks/userpromptsubmit_runner.py with CLAUDE_PLUGIN_ROOT=REPO so
    its _resolve_measure_dir() finds skills/token-optimizer/scripts/measure.py."""
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(REPO))
    # Keep the measure import deterministic and isolated from the host's real
    # ~/.claude state by pointing config dirs at a tmp dir.
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    spec = importlib.util.spec_from_file_location("ups_runner_under_test", RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- #
# (a) TOKEN_OPTIMIZER_HOOKS_USERPROMPTSUBMIT=0 pre-import opt-out (Req 3)
# --------------------------------------------------------------------------- #


def test_userpromptsubmit_env_opt_out_returns_before_spawning_child(monkeypatch):
    run = _load_run_py()
    monkeypatch.setattr(sys, "argv", ["run.py", "hooks/userpromptsubmit_runner.py"])
    monkeypatch.setenv("TOKEN_OPTIMIZER_HOOKS_USERPROMPTSUBMIT", "0")
    # Clear CLAUDE_PLUGIN_ROOT so _plugin_disabled_by_host fails open (returns
    # False) and the opt-out check is the thing that actually short-circuits.
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)

    spawned = {"count": 0}

    class _NoSpawn:
        def __init__(self, *a, **k):
            spawned["count"] += 1
            raise AssertionError("run.py spawned a child despite the opt-out env var")

    monkeypatch.setattr(run.subprocess, "Popen", _NoSpawn)
    monkeypatch.setattr(run.signal, "signal", lambda *_a, **_k: None)

    rc = run.main()
    assert rc == 0, "opt-out must exit 0"
    assert spawned["count"] == 0, "run.py must not build the module_runner command"


def test_userpromptsubmit_env_opt_out_is_exact_target(monkeypatch):
    """The opt-out must NOT silence any other hook script (exact-target gate)."""
    run = _load_run_py()
    # A different script path with the env var set must still proceed to Popen
    # (i.e. the gate is scoped to the UserPromptSubmit runner only).
    monkeypatch.setattr(sys, "argv", ["run.py", "skills/token-optimizer/scripts/measure.py", "quality-cache", "--warn", "--quiet"])
    monkeypatch.setenv("TOKEN_OPTIMIZER_HOOKS_USERPROMPTSUBMIT", "0")
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(REPO))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)

    # Stub Popen + wait so main() can complete without really spawning measure.
    class _FakeProc:
        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

        def kill(self):
            pass

    spawned = {"count": 0}

    def _popen(*a, **k):
        spawned["count"] += 1
        return _FakeProc()

    monkeypatch.setattr(run.subprocess, "Popen", _popen)
    monkeypatch.setattr(run.signal, "signal", lambda *_a, **_k: None)
    # Bypass consent (it may read a real ~/.claude/config.json); we only care
    # that the env opt-out did NOT fire for a non-runner script.
    monkeypatch.setattr(run, "_check_consent", lambda: True)
    monkeypatch.setattr(run, "_plugin_disabled_by_host", lambda: False)

    run.main()
    assert spawned["count"] == 1, "non-runner scripts must NOT be silenced by the opt-out"


# --------------------------------------------------------------------------- #
# (b) single dispatcher runs all six subcommands against one measure.py import
# --------------------------------------------------------------------------- #


def _stub_budget(monkeypatch, runner):
    """Replace the wall-clock budget with no-ops so tests don't arm watchdog threads."""
    monkeypatch.setattr(runner.measure, "_install_hook_budget", lambda seconds=8: object())
    monkeypatch.setattr(runner.measure, "_clear_hook_budget", lambda deadline: None)


def _install_call_recorder(monkeypatch, runner):
    """Monkeypatch the six subcommand entrypoints + side-effect helpers to record
    calls. Returns a dict of call logs keyed by subcommand."""
    calls = {
        "quality_cache_warn": [],
        "prompt_continuity": [],
        "verbosity_steer": [],
        "ensure_health": [],
        "quality_cache_force": [],
        "compact_restore": [],
    }

    def _quality_cache(**kw):
        if kw.get("warn") and not kw.get("force"):
            calls["quality_cache_warn"].append(kw)
        elif kw.get("force"):
            calls["quality_cache_force"].append(kw)

    def _continuity(**kw):
        calls["prompt_continuity"].append(kw)
        return ""

    def _verbosity(**kw):
        calls["verbosity_steer"].append(kw)
        return None

    def _ensure_health():
        calls["ensure_health"].append({})

    def _compact_restore(**kw):
        calls["compact_restore"].append(kw)

    monkeypatch.setattr(runner.measure, "quality_cache", _quality_cache)
    monkeypatch.setattr(runner.measure, "_continuity_prompt_hint", _continuity)
    monkeypatch.setattr(runner.measure, "run_verbosity_steer", _verbosity)
    monkeypatch.setattr(runner.measure, "run_ensure_health", _ensure_health)
    monkeypatch.setattr(runner.measure, "compact_restore", _compact_restore)
    # Side-effect helpers: must not raise, must not touch the real filesystem.
    monkeypatch.setattr(runner.measure, "_daemon_midsession_pulse", lambda: None)
    monkeypatch.setattr(runner.measure, "_ensure_health_daemon_revive_first", lambda: None)
    monkeypatch.setattr(runner.measure, "_is_running_from_plugin_cache", lambda: True)
    monkeypatch.setattr(runner.measure, "_is_plugin_installed", lambda: True)
    # Marker guard: return False so the gated subcommands DO their work.
    monkeypatch.setattr(runner.measure, "_ran_once_this_session", lambda tag, sid: False)
    # Cowork/codex detection: stay on the raw-stdout path (no envelope wrapping).
    monkeypatch.setattr(runner.measure, "is_cowork", lambda: False)
    monkeypatch.setattr(runner.measure, "detect_runtime", lambda: "claude")
    return calls


def test_userpromptsubmit_runner_all_subcommands_one_import(monkeypatch, tmp_path):
    runner = _load_runner(monkeypatch, tmp_path)
    _stub_budget(monkeypatch, runner)
    calls = _install_call_recorder(monkeypatch, runner)

    # The runner reads stdin once via _read_hook_input; bypass it with a fixed
    # payload so no real stdin read happens.
    payload = {"session_id": "sess-abc-139", "transcript_path": "/tmp/t.jsonl",
               "cwd": "/tmp", "prompt": "hello"}
    monkeypatch.setattr(runner, "_read_hook_input", lambda: payload)
    # Harness guard must pass so the three gated subcommands run.
    monkeypatch.setattr(runner, "_harness_only_context", lambda: True)

    # measure.py is imported exactly once: the runner module holds a single
    # `measure` attribute bound to the cached sys.modules entry.
    assert runner.measure is sys.modules.get("measure")

    rc = runner.main()
    assert rc == 0

    # All six subcommands ran exactly once, in one process, against one import.
    assert len(calls["quality_cache_warn"]) == 1, "quality-cache --warn must run"
    assert len(calls["prompt_continuity"]) == 1, "prompt-continuity must run"
    assert len(calls["verbosity_steer"]) == 1, "verbosity-steer must run"
    assert len(calls["ensure_health"]) == 1, "ensure-health must run"
    assert len(calls["quality_cache_force"]) == 1, "quality-cache --force must run"
    assert len(calls["compact_restore"]) == 1, "compact-restore must run"

    # Verify the REAL call shapes (the plan assumed wrong kwargs; pin the truth).
    warn_kw = calls["quality_cache_warn"][0]
    assert warn_kw == {
        "throttle_seconds": 120, "warn_threshold": 70, "quiet": True,
        "session_jsonl": "/tmp/t.jsonl", "force": False,
        "pure_time_throttle": False, "session_id": "sess-abc-139", "warn": True,
    }
    force_kw = calls["quality_cache_force"][0]
    assert force_kw["force"] is True and force_kw["warn"] is False
    assert force_kw["session_jsonl"] == "/tmp/t.jsonl"
    # verbosity-steer dispatch hardcodes quiet=False (NOT the --quiet flag).
    assert calls["verbosity_steer"][0]["quiet"] is False
    # compact-restore uses new_session_only=True.
    assert calls["compact_restore"][0]["new_session_only"] is True
    assert calls["compact_restore"][0]["session_id"] == "sess-abc-139"


# --------------------------------------------------------------------------- #
# (c) failure isolation: one subcommand throwing never aborts the others
# --------------------------------------------------------------------------- #


def test_userpromptsubmit_runner_failure_isolation(monkeypatch, tmp_path, capsys):
    runner = _load_runner(monkeypatch, tmp_path)
    _stub_budget(monkeypatch, runner)
    calls = _install_call_recorder(monkeypatch, runner)

    # Make quality-cache --warn explode. The other five must still run.
    def _boom(**kw):
        if kw.get("warn") and not kw.get("force"):
            raise RuntimeError("simulated quality-cache --warn failure")
        if kw.get("force"):
            calls["quality_cache_force"].append(kw)

    monkeypatch.setattr(runner.measure, "quality_cache", _boom)
    monkeypatch.setattr(runner, "_read_hook_input",
                        lambda: {"session_id": "sess-iso-139", "prompt": "x"})
    monkeypatch.setattr(runner, "_harness_only_context", lambda: True)

    rc = runner.main()
    assert rc == 0, "a subcommand failure must never abort the hook (exit 0)"

    # The other five subcommands still ran.
    assert len(calls["prompt_continuity"]) == 1
    assert len(calls["verbosity_steer"]) == 1
    assert len(calls["ensure_health"]) == 1
    assert len(calls["quality_cache_force"]) == 1
    assert len(calls["compact_restore"]) == 1

    err = capsys.readouterr().err
    assert "quality-cache --warn failed, continuing" in err, (
        "failure must be logged to stderr, not swallowed silently"
    )


# --------------------------------------------------------------------------- #
# (d) per-session marker gate: gated subcommands skip on a latched session
# --------------------------------------------------------------------------- #


def test_userpromptsubmit_runner_session_marker_gate(monkeypatch, tmp_path):
    runner = _load_runner(monkeypatch, tmp_path)
    _stub_budget(monkeypatch, runner)
    calls = _install_call_recorder(monkeypatch, runner)

    # Simulate "already ran this session" for EVERY gated tag. The runner calls
    # _ran_once_this_session(tag, sid) for ensure-health, quality-cache-force,
    # and compact-restore-new-session.
    monkeypatch.setattr(runner.measure, "_ran_once_this_session", lambda tag, sid: True)
    monkeypatch.setattr(runner, "_read_hook_input",
                        lambda: {"session_id": "sess-latched-139", "prompt": "x"})
    monkeypatch.setattr(runner, "_harness_only_context", lambda: True)

    rc = runner.main()
    assert rc == 0

    # The three always-on subcommands still run.
    assert len(calls["quality_cache_warn"]) == 1, "ungated quality-cache --warn must run"
    assert len(calls["prompt_continuity"]) == 1, "ungated prompt-continuity must run"
    assert len(calls["verbosity_steer"]) == 1, "ungated verbosity-steer must run"

    # The three gated subcommands skip their domain work.
    assert calls["ensure_health"] == [], "ensure-health must skip when marker exists"
    assert calls["quality_cache_force"] == [], "quality-cache --force must skip when marker exists"
    assert calls["compact_restore"] == [], "compact-restore must skip when marker exists"


def test_userpromptsubmit_runner_harness_guard_skips_gated(monkeypatch, tmp_path):
    """When the harness guard fails, the three gated subcommands are skipped
    entirely (replicating the shell `exit 0` that used to prefix entries 4/5/6)
    while the three always-on subcommands still run."""
    runner = _load_runner(monkeypatch, tmp_path)
    _stub_budget(monkeypatch, runner)
    calls = _install_call_recorder(monkeypatch, runner)
    monkeypatch.setattr(runner, "_read_hook_input",
                        lambda: {"session_id": "sess-noguard-139", "prompt": "x"})
    monkeypatch.setattr(runner, "_harness_only_context", lambda: False)

    rc = runner.main()
    assert rc == 0

    assert len(calls["quality_cache_warn"]) == 1
    assert len(calls["prompt_continuity"]) == 1
    assert len(calls["verbosity_steer"]) == 1
    assert calls["ensure_health"] == []
    assert calls["quality_cache_force"] == []
    assert calls["compact_restore"] == []
