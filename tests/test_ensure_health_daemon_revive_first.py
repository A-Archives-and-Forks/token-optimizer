"""FIX C: the dashboard-daemon ensure/revive must run FIRST, under its own
short independent guard, BEFORE the 8s SessionStart health budget is armed.

Before the fix, ``run_ensure_health`` ran under ``_install_hook_budget(8)`` and
the daemon self-heal lived partway down the function. A health scan that
exceeded 8s raised ``_HookTimeout`` (or tripped the watchdog) BEFORE the daemon
revive, so a missing launchd plist / dead daemon on port 24842 was never
restored. The fix extracts ``_ensure_health_daemon_revive_first()`` and calls
it BEFORE the 8s budget in the ensure-health dispatch, so a missing plist is
reliably reinstalled even when the later health scan times out.

These tests prove:
  * ``_ensure_health_daemon_revive_first`` runs ``_ensure_dashboard_daemon`` and
    reports an install/restart, never raising (fail-open).
  * The daemon revive completes BEFORE the health scan that later times out --
    i.e. a missing plist is reinstalled even when ``run_ensure_health`` raises
    ``_HookTimeout``.
  * The ensure-health dispatch source calls ``_ensure_health_daemon_revive_first``
    BEFORE ``_install_hook_budget(8)`` / ``run_ensure_health`` (reorder guard).

Run: python3 -m pytest tests/test_ensure_health_daemon_revive_first.py -v
"""

from __future__ import annotations

import importlib
import re
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "token-optimizer" / "scripts"
sys.path.insert(0, str(SCRIPTS))


@pytest.fixture()
def m(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="to-daemon-revive-first-")
    monkeypatch.setenv("TOKEN_OPTIMIZER_SNAPSHOT_DIR", tmp)
    if "measure" in sys.modules:
        del sys.modules["measure"]
    mod = importlib.import_module("measure")
    monkeypatch.setattr(mod, "_is_foreign_runtime", lambda: False)
    monkeypatch.setattr(mod, "detect_runtime", lambda: "claude")
    monkeypatch.setattr(mod, "_normalized_platform", lambda: "Darwin")
    # No real watchdog threads: stub the budget primitives.
    monkeypatch.setattr(mod, "_install_hook_budget", lambda s: object())
    monkeypatch.setattr(mod, "_clear_hook_budget", lambda d: None)
    yield mod
    if "measure" in sys.modules:
        del sys.modules["measure"]


def test_revive_first_installs_missing_daemon(m, monkeypatch, capsys):
    """A missing daemon (-> 'installed') is reinstalled by the first-step helper."""
    monkeypatch.setattr(m, "_ensure_dashboard_daemon", lambda *a, **k: "installed")
    m._ensure_health_daemon_revive_first()
    out = capsys.readouterr().out
    assert "Installed the dashboard daemon" in out
    assert "localhost:" in out


def test_revive_first_restarts_dead_daemon(m, monkeypatch, capsys):
    monkeypatch.setattr(m, "_ensure_dashboard_daemon", lambda *a, **k: "restarted")
    m._ensure_health_daemon_revive_first()
    assert "Restarted the dashboard daemon" in capsys.readouterr().out


def test_revive_first_never_raises_on_inner_error(m, monkeypatch):
    """Fail-open: an exception inside _ensure_dashboard_daemon must not escape."""

    def boom(*a, **k):
        raise RuntimeError("daemon explode")

    monkeypatch.setattr(m, "_ensure_dashboard_daemon", boom)
    # Must not raise into the hook.
    m._ensure_health_daemon_revive_first()


def test_revive_first_swallows_injected_hook_timeout(m, monkeypatch):
    """A test-injected _HookTimeout inside the revive is swallowed so the rest
    of ensure-health still gets its own 8s guard."""

    def boom(*a, **k):
        raise m._HookTimeout()

    monkeypatch.setattr(m, "_ensure_dashboard_daemon", boom)
    m._ensure_health_daemon_revive_first()  # must not raise


def test_daemon_revive_runs_before_health_budget_timeout(m, monkeypatch):
    """THE FIX C contract: the daemon revive completes (installs the missing
    plist) BEFORE the health scan that later trips the 8s budget. Reproduces the
    ensure-health dispatch ordering: revive-first, then run_ensure_health under
    its 8s guard. A timeout in the health scan must not skip the revive."""
    log: list[str] = []

    monkeypatch.setattr(
        m, "_ensure_dashboard_daemon", lambda *a, **k: log.append("daemon") or "installed"
    )

    def slow_health():
        log.append("health")
        raise m._HookTimeout()

    monkeypatch.setattr(m, "run_ensure_health", slow_health)

    # Mirror the ensure-health dispatch exactly (FIX C ordering):
    #   _ensure_health_daemon_revive_first()
    #   _install_hook_budget(8); try: run_ensure_health() except _HookTimeout: ...
    m._ensure_health_daemon_revive_first()
    try:
        m.run_ensure_health()
    except m._HookTimeout:
        pass

    # Daemon revive ran first and completed; the health scan timed out AFTER.
    assert log == ["daemon", "health"], (
        f"daemon revive must precede the (timing-out) health scan; got {log}"
    )


def test_dispatch_source_calls_revive_first_before_budget():
    """Reorder guard: in the ensure-health dispatch block, the call to
    ``_ensure_health_daemon_revive_first()`` must appear BEFORE
    ``_install_hook_budget(8)`` and ``run_ensure_health()`` so a future edit
    cannot silently revert FIX C."""
    src = (SCRIPTS / "measure.py").read_text(encoding="utf-8")
    # Isolate the ensure-health dispatch arm.
    m = re.search(
        r'elif args\[0\] == "ensure-health":(?P<body>.*?)(?=\n    elif args\[0\] ==)',
        src,
        re.S,
    )
    assert m, "could not locate the ensure-health dispatch block"
    body = m.group("body")
    i_revive = body.find("_ensure_health_daemon_revive_first()")
    i_budget = body.find("_install_hook_budget(8)")
    i_health = body.find("run_ensure_health()")
    assert i_revive != -1, "dispatch no longer calls _ensure_health_daemon_revive_first()"
    assert i_budget != -1, "dispatch no longer arms the 8s budget"
    assert i_health != -1, "dispatch no longer calls run_ensure_health"
    assert i_revive < i_budget < i_health, (
        "FIX C ordering broken: revive-first must precede the 8s budget and "
        "run_ensure_health"
    )
