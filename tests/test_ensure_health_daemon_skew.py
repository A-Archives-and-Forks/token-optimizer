"""Escalation contract for a daemon that stays stale after a version-skew restart.

Origin: dashboard daemon silent version-skew drift. When _restart_dashboard_daemon
reports 'restart-stale' (the service restart ran but the OLD version still serves
the port), run_ensure_health must escalate: reap the port holder DIRECTLY, THEN
force a clean reinstall. Reaping first is essential -- an alive-but-wrong-version
orphan makes the port read as healthy, so _ensure_dashboard_daemon(force=True)
would no-op ('noop-healthy') without the reap.

This targets the escalation CONTRACT via the extracted _apply_daemon_restart_outcome
helper, not the full run_ensure_health (which does heavy environment I/O). All
process / reinstall surfaces are mocked.

Run: python3 -m pytest tests/test_ensure_health_daemon_skew.py -v
"""

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "token-optimizer" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import measure  # noqa: E402


def _claude_runtime(monkeypatch):
    """Force the escalation's do-no-harm gate to allow reinstall (Claude, enabled)."""
    monkeypatch.setattr(measure, "_is_foreign_runtime", lambda: False)
    monkeypatch.setattr(measure, "detect_runtime", lambda: "claude")
    monkeypatch.setattr(measure, "_read_config_flag", lambda k, d=None: False)
    monkeypatch.setattr(measure, "_normalized_platform", lambda: "Darwin")


def test_restarted_reports_ok_without_reinstall(monkeypatch):
    calls = []
    monkeypatch.setattr(measure, "_reclaim_posix_daemon_port", lambda *a, **k: calls.append("REAP"))
    monkeypatch.setattr(measure, "_ensure_dashboard_daemon", lambda *a, **k: calls.append("FORCE") or "installed")

    level, msg = measure._apply_daemon_restart_outcome("restarted")

    assert level == "ok"
    assert "Auto-updated" in msg
    assert calls == [], "a clean restart must not escalate (no reap, no force reinstall)"


def test_stale_reaps_before_forcing_reinstall(monkeypatch):
    _claude_runtime(monkeypatch)
    calls = []
    monkeypatch.setattr(measure, "_reclaim_posix_daemon_port", lambda *a, **k: calls.append("REAP"))
    monkeypatch.setattr(measure, "_ensure_dashboard_daemon", lambda *a, **k: (calls.append("FORCE"), "installed")[1])

    level, msg = measure._apply_daemon_restart_outcome("restart-stale")

    assert level == "ok-reinstall"
    assert "reinstall" in msg
    # The trap: reap MUST precede the forced reinstall, else the alive orphan
    # reads as noop-healthy and the force does nothing.
    assert calls == ["REAP", "FORCE"], f"reap must come before force: {calls}"


def test_stale_reinstall_restarted_also_ok(monkeypatch):
    _claude_runtime(monkeypatch)
    monkeypatch.setattr(measure, "_reclaim_posix_daemon_port", lambda *a, **k: None)
    monkeypatch.setattr(measure, "_ensure_dashboard_daemon", lambda *a, **k: "restarted")

    level, msg = measure._apply_daemon_restart_outcome("restart-stale")

    assert level == "ok-reinstall"


def test_stale_reinstall_failure_is_no_false_success(monkeypatch):
    _claude_runtime(monkeypatch)
    monkeypatch.setattr(measure, "_reclaim_posix_daemon_port", lambda *a, **k: None)
    monkeypatch.setattr(measure, "_ensure_dashboard_daemon", lambda *a, **k: "noop-throttled")

    level, msg = measure._apply_daemon_restart_outcome("restart-stale")

    assert level == "stale"
    assert "Auto-updated" not in msg, "must never claim success when the daemon is still stale"
    assert "setup-daemon" in msg  # actionable remediation


def test_stale_escalation_survives_reaper_exception(monkeypatch):
    _claude_runtime(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("reap failed")

    monkeypatch.setattr(measure, "_reclaim_posix_daemon_port", boom)
    monkeypatch.setattr(measure, "_ensure_dashboard_daemon", lambda *a, **k: "installed")

    # Must not raise; escalation continues to the forced reinstall.
    level, msg = measure._apply_daemon_restart_outcome("restart-stale")

    assert level == "ok-reinstall"


def test_stale_does_not_reap_on_non_claude_runtime(monkeypatch):
    """Do-no-harm gate: never reap a daemon we can't replace (Codex etc)."""
    _claude_runtime(monkeypatch)
    monkeypatch.setattr(measure, "detect_runtime", lambda: "codex")
    calls = []
    monkeypatch.setattr(measure, "_reclaim_posix_daemon_port", lambda *a, **k: calls.append("REAP"))
    monkeypatch.setattr(measure, "_ensure_dashboard_daemon", lambda *a, **k: calls.append("FORCE") or "installed")

    level, msg = measure._apply_daemon_restart_outcome("restart-stale")

    assert level == "stale"
    assert calls == [], "must NOT reap when the reinstall would no-op (would leave user daemon-less)"


def test_stale_does_not_reap_when_daemon_disabled(monkeypatch):
    _claude_runtime(monkeypatch)
    monkeypatch.setattr(measure, "_read_config_flag",
                        lambda k, d=None: True if k == "daemon_disabled" else d)
    calls = []
    monkeypatch.setattr(measure, "_reclaim_posix_daemon_port", lambda *a, **k: calls.append("REAP"))
    monkeypatch.setattr(measure, "_ensure_dashboard_daemon", lambda *a, **k: calls.append("FORCE") or "installed")

    level, msg = measure._apply_daemon_restart_outcome("restart-stale")

    assert level == "stale"
    assert calls == [], "daemon_disabled: must not reap-and-abandon"


def test_restart_failed_reports_failed(monkeypatch):
    calls = []
    monkeypatch.setattr(measure, "_reclaim_posix_daemon_port", lambda *a, **k: calls.append("REAP"))
    monkeypatch.setattr(measure, "_ensure_dashboard_daemon", lambda *a, **k: calls.append("FORCE") or "installed")

    level, msg = measure._apply_daemon_restart_outcome("restart-failed")

    assert level == "failed"
    assert calls == [], "restart-failed does not escalate"
