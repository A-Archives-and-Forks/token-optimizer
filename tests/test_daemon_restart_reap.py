"""Version-skew restart must reap the orphaned port-holder and verify it landed.

Origin: dashboard daemon silent version-skew drift. After a plugin upgrade the
service-manager restart (launchctl kickstart -k / systemctl restart / schtasks)
only restarts the job's OWN child; an orphaned dashboard-server.py -- from a
prior label, a manual launch, or ANOTHER runtime on the same port -- survives
and keeps serving the old hardcoded measure.py path. _restart_dashboard_daemon
now reaps the port holder BEFORE restarting and verifies the restart landed by
the served /api/health version.

These tests mock all subprocess / network / sleep surfaces: no real processes,
no real network, no real sleeps.

Run: python3 -m pytest tests/test_daemon_restart_reap.py -v
"""

import sys
import urllib.request
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "token-optimizer" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import measure  # noqa: E402


class _Completed:
    """Minimal stand-in for subprocess.CompletedProcess."""

    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def _make_recording_run(call_log, ps_contains_script=True):
    """Fake subprocess.run that records argv and answers id/lsof/ps sensibly."""

    def fake_run(cmd, *a, **k):
        argv = list(cmd) if isinstance(cmd, (list, tuple)) else [cmd]
        call_log.append(argv)
        head = argv[0] if argv else ""
        if head == "id":
            return _Completed(stdout="501\n")
        if head == "lsof":
            return _Completed(stdout="4242\n")
        if head == "ps":
            cmdline = "/usr/bin/python dashboard-server.py" if ps_contains_script else "/usr/bin/python some-other-app.py"
            return _Completed(stdout=cmdline)
        return _Completed(stdout="")

    return fake_run


def _fake_urlopen_factory(body):
    """Return a fake urlopen whose context manager yields a .read() of `body`."""

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n=-1):
            return body

    def _fake(url, timeout=None):
        return _Resp()

    return _fake


# --------------------------------------------------------------------------
# _restart_dashboard_daemon
# --------------------------------------------------------------------------

def test_reaper_runs_before_launchctl_on_darwin(monkeypatch):
    calls = []
    monkeypatch.setattr(measure, "_reclaim_posix_daemon_port", lambda *a, **k: calls.append(["REAP"]))
    monkeypatch.setattr(measure.subprocess, "run", _make_recording_run(calls))
    monkeypatch.setattr(measure, "_daemon_served_version", lambda *a, **k: measure.TOKEN_OPTIMIZER_VERSION)
    monkeypatch.setattr(measure.time, "sleep", lambda *a, **k: None)

    result = measure._restart_dashboard_daemon("Darwin")

    assert result == "restarted"
    reap_idx = calls.index(["REAP"])
    launch_idx = next(i for i, c in enumerate(calls) if c and c[0] == "launchctl")
    assert reap_idx < launch_idx, f"reaper must run before launchctl: {calls}"


def test_service_manager_argv_per_platform(monkeypatch):
    cases = {
        "Darwin": "launchctl",
        "Linux": "systemctl",
        "Windows": "schtasks",
    }
    for system, head in cases.items():
        calls = []
        monkeypatch.setattr(measure, "_reclaim_posix_daemon_port", lambda *a, **k: calls.append(["REAP"]))
        monkeypatch.setattr(measure.subprocess, "run", _make_recording_run(calls))
        monkeypatch.setattr(measure, "_daemon_served_version", lambda *a, **k: None)
        monkeypatch.setattr(measure.time, "sleep", lambda *a, **k: None)

        result = measure._restart_dashboard_daemon(system)

        assert result == "restarted"
        heads = [c[0] for c in calls if c]
        assert head in heads, f"{system}: expected {head} in {heads}"
        if system == "Windows":
            # End then Run
            end_idx = next(i for i, c in enumerate(calls) if c[:2] == ["schtasks", "/End"])
            run_idx = next(i for i, c in enumerate(calls) if c[:2] == ["schtasks", "/Run"])
            assert end_idx < run_idx


def test_unsupported_platform_is_restart_failed(monkeypatch):
    calls = []
    monkeypatch.setattr(measure, "_reclaim_posix_daemon_port", lambda *a, **k: calls.append(["REAP"]))
    monkeypatch.setattr(measure.subprocess, "run", _make_recording_run(calls))
    monkeypatch.setattr(measure, "_daemon_served_version", lambda *a, **k: None)

    result = measure._restart_dashboard_daemon("Plan9")

    assert result == "restart-failed"
    # No service-manager command issued.
    assert not any(c and c[0] in ("launchctl", "systemctl", "schtasks") for c in calls)


def test_reaper_exception_does_not_abort_restart(monkeypatch):
    calls = []

    def boom(*a, **k):
        raise RuntimeError("reaper blew up")

    monkeypatch.setattr(measure, "_reclaim_posix_daemon_port", boom)
    monkeypatch.setattr(measure.subprocess, "run", _make_recording_run(calls))
    monkeypatch.setattr(measure, "_daemon_served_version", lambda *a, **k: measure.TOKEN_OPTIMIZER_VERSION)
    monkeypatch.setattr(measure.time, "sleep", lambda *a, **k: None)

    result = measure._restart_dashboard_daemon("Darwin")

    assert result == "restarted"  # restart still proceeds despite reaper failure


def test_served_version_mismatch_is_restart_stale(monkeypatch):
    monkeypatch.setattr(measure, "_reclaim_posix_daemon_port", lambda *a, **k: None)
    monkeypatch.setattr(measure.subprocess, "run", _make_recording_run([]))
    monkeypatch.setattr(measure, "_daemon_served_version", lambda *a, **k: "0.0.1-old")
    monkeypatch.setattr(measure.time, "sleep", lambda *a, **k: None)

    result = measure._restart_dashboard_daemon("Darwin")

    assert result == "restart-stale"


def test_served_version_none_is_safe_degrade_to_restarted(monkeypatch):
    monkeypatch.setattr(measure, "_reclaim_posix_daemon_port", lambda *a, **k: None)
    monkeypatch.setattr(measure.subprocess, "run", _make_recording_run([]))
    monkeypatch.setattr(measure, "_daemon_served_version", lambda *a, **k: None)
    monkeypatch.setattr(measure.time, "sleep", lambda *a, **k: None)

    result = measure._restart_dashboard_daemon("Darwin")

    assert result == "restarted"  # unknown must NOT be a false stale


def test_subprocess_exception_is_restart_failed(monkeypatch):
    monkeypatch.setattr(measure, "_reclaim_posix_daemon_port", lambda *a, **k: None)

    def boom(*a, **k):
        raise OSError("launchctl missing")

    monkeypatch.setattr(measure.subprocess, "run", boom)
    monkeypatch.setattr(measure, "_daemon_served_version", lambda *a, **k: None)

    result = measure._restart_dashboard_daemon("Darwin")

    assert result == "restart-failed"


# --------------------------------------------------------------------------
# _daemon_served_version
# --------------------------------------------------------------------------

def test_served_version_reads_version_field(monkeypatch):
    body = b'{"ok": true, "server": "token-optimizer-daemon", "version": "9.9.9"}'
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen_factory(body))

    assert measure._daemon_served_version() == "9.9.9"


def test_served_version_none_when_no_version_field(monkeypatch):
    body = b'{"ok": true, "server": "token-optimizer-daemon"}'
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen_factory(body))

    assert measure._daemon_served_version() is None


def test_served_version_none_when_unreachable(monkeypatch):
    def boom(url, timeout=None):
        raise ConnectionError("refused")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    monkeypatch.setattr(measure.time, "sleep", lambda *a, **k: None)

    assert measure._daemon_served_version(total_timeout=0.5) is None


# --------------------------------------------------------------------------
# _reclaim_posix_daemon_port foreign-process guard
# --------------------------------------------------------------------------

def test_reaper_never_kills_foreign_process(monkeypatch):
    killed = []
    monkeypatch.setattr(measure.subprocess, "run", _make_recording_run([], ps_contains_script=False))
    monkeypatch.setattr(measure.os, "kill", lambda pid, sig: killed.append(pid))
    monkeypatch.setattr(measure.time, "sleep", lambda *a, **k: None)

    measure._reclaim_posix_daemon_port()

    assert killed == [], "must never SIGTERM a process whose command lacks dashboard-server.py"


def test_reaper_kills_our_own_daemon(monkeypatch):
    killed = []
    monkeypatch.setattr(measure.subprocess, "run", _make_recording_run([], ps_contains_script=True))
    monkeypatch.setattr(measure.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    monkeypatch.setattr(measure.time, "sleep", lambda *a, **k: None)

    measure._reclaim_posix_daemon_port()

    assert killed and killed[0][0] == 4242
    assert killed[0][1] == measure.signal.SIGTERM
