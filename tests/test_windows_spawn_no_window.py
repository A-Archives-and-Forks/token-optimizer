"""Windows console-flash elimination + child-tree reap coverage.

On Windows, ``start_new_session=True`` is silently ignored by ``subprocess``,
so a detached child inherits the parent's console and flashes a ~1s window on
every spawn. Every fire-and-forget spawn must now route through
``spawn_utils.detach_spawn_kwargs()`` (POSIX: ``start_new_session``; Windows:
``DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB``).

The one exception is the daemon-regen spawn inside the generated
``dashboard-server.py`` template: that file is standalone (no sibling
``spawn_utils.py``), so the detach logic is inlined as ``CREATE_NO_WINDOW`` on
nt (the regen child is a transient worker, not a survivor).

``hooks/run.py`` is also special: its child MUST inherit run.py's stdio for
hook injection via stdout, so it uses ``CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP``
(NOT ``DETACHED_PROCESS``) on Windows, and reaps via ``taskkill /F /T /PID``
instead of ``os.killpg`` (which does not exist on Windows).

Run: python3 -m pytest tests/test_windows_spawn_no_window.py -v
"""
import importlib
import importlib.util
import os
import posixpath
import subprocess
import sys
import tempfile
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "token-optimizer" / "scripts"
HOOKS = REPO / "hooks"

sys.path.insert(0, str(SCRIPTS))

# ---------------------------------------------------------------------------
# Windows flag constants (the values CPython uses on Windows builds)
# ---------------------------------------------------------------------------
_DETACHED_PROCESS = 0x8
_CREATE_NEW_PROCESS_GROUP = 0x200
_CREATE_BREAKAWAY_FROM_JOB = 0x1000000
_CREATE_NO_WINDOW = 0x08000000

_NT_FLAGS = {
    "DETACHED_PROCESS": _DETACHED_PROCESS,
    "CREATE_NEW_PROCESS_GROUP": _CREATE_NEW_PROCESS_GROUP,
    "CREATE_BREAKAWAY_FROM_JOB": _CREATE_BREAKAWAY_FROM_JOB,
    "CREATE_NO_WINDOW": _CREATE_NO_WINDOW,
}

_DETACH_FLAGS = _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP | _CREATE_BREAKAWAY_FROM_JOB


def _make_fake_os_nt():
    """Create a fake ``os`` module with ``name='nt'`` but real posixpath.

    Monkeypatching ``os.name`` on the real ``os`` module breaks ``pathlib``
    on macOS (Python 3.14's pathlib dispatches path flavour on ``os.name``).
    Instead we build a shallow copy of the real ``os`` module's namespace,
    override only ``name``, and inject it into the module under test. The copy
    keeps ``os.path`` as ``posixpath`` so path resolution still works.
    """
    fake = types.ModuleType("os_nt")
    fake.__dict__.update(os.__dict__)
    fake.name = "nt"
    return fake


def _set_nt_spawn_utils(monkeypatch):
    """Patch ``spawn_utils`` so ``detach_spawn_kwargs()`` returns nt kwargs.

    The helper reads ``os.name`` and flag constants from its OWN namespace
    (``spawn_utils.os``, ``spawn_utils.subprocess``), not the caller's. We
    replace ``spawn_utils.os`` with a fake that has ``name='nt'`` but keeps
    ``posixpath`` so ``pathlib`` in the CALLER (measure, hermes, copilot) is
    unaffected -- ``spawn_utils`` only reads ``os.name``, never touches paths.
    """
    import spawn_utils
    fake_os = _make_fake_os_nt()
    monkeypatch.setattr(spawn_utils, "os", fake_os)
    for _name, _val in _NT_FLAGS.items():
        monkeypatch.setattr(spawn_utils.subprocess, _name, _val, raising=False)


def _set_nt(monkeypatch, mod):
    """Patch *mod* so ``os.name == 'nt'`` and Windows flag constants exist.

    Use ``_set_nt_spawn_utils`` instead for modules that route spawns through
    ``detach_spawn_kwargs()`` and don't have inline ``os.name`` checks (measure,
    hermes, copilot). This heavier helper replaces ``mod.os`` with a fake that
    has ``name='nt'`` but keeps ``posixpath`` -- needed for run.py which has
    inline ``os.name == 'nt'`` branches AND must not break pathlib.
    """
    fake_os = _make_fake_os_nt()
    monkeypatch.setattr(mod, "os", fake_os)
    for _name, _val in _NT_FLAGS.items():
        monkeypatch.setattr(mod.subprocess, _name, _val, raising=False)
    if "spawn_utils" in sys.modules:
        _set_nt_spawn_utils(monkeypatch)


def _set_posix_spawn_utils(monkeypatch):
    """Patch ``spawn_utils`` so ``detach_spawn_kwargs()`` returns posix kwargs."""
    import spawn_utils
    fake_os = types.ModuleType("os_posix")
    fake_os.__dict__.update(os.__dict__)
    fake_os.name = "posix"
    monkeypatch.setattr(spawn_utils, "os", fake_os)
    for _name in _NT_FLAGS:
        monkeypatch.delattr(spawn_utils.subprocess, _name, raising=False)


def _set_posix(monkeypatch, mod):
    """Patch *mod* so ``os.name == 'posix'`` and no Windows flags exist."""
    monkeypatch.setattr(mod.os, "name", "posix")
    for _name in _NT_FLAGS:
        monkeypatch.delattr(mod.subprocess, _name, raising=False)


# ---------------------------------------------------------------------------
# measure.py fixture (mirrors test_daemon_midsession_pulse.py bootstrap)
# ---------------------------------------------------------------------------
@pytest.fixture()
def m(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="to-spawn-nt-")
    monkeypatch.setenv("TOKEN_OPTIMIZER_SNAPSHOT_DIR", tmp)
    if "measure" in sys.modules:
        del sys.modules["measure"]
    mod = importlib.import_module("measure")
    importlib.reload(mod)
    monkeypatch.setattr(mod, "_is_foreign_runtime", lambda: False)
    monkeypatch.setattr(mod, "detect_runtime", lambda: "claude")
    yield mod
    if "measure" in sys.modules:
        del sys.modules["measure"]


def _capture_popen(mod, monkeypatch):
    """Replace ``subprocess.Popen`` on *mod* with a kwargs-capturing fake."""
    captured = {}

    def fake_popen(argv, *a, **k):
        captured.update(k)
        captured["argv"] = argv

        class _P:
            pid = 99999
            def poll(self):
                return 0
            def wait(self, timeout=None):
                return 0
            def kill(self):
                pass
        return _P()
    monkeypatch.setattr(mod.subprocess, "Popen", fake_popen)
    return captured


# ---------------------------------------------------------------------------
# spawn_utils.detach_spawn_kwargs unit tests
# ---------------------------------------------------------------------------
def test_detach_spawn_kwargs_nt_returns_creationflags(monkeypatch):
    import spawn_utils
    fake_os = _make_fake_os_nt()
    monkeypatch.setattr(spawn_utils, "os", fake_os)
    for _name, _val in _NT_FLAGS.items():
        monkeypatch.setattr(spawn_utils.subprocess, _name, _val, raising=False)
    kw = spawn_utils.detach_spawn_kwargs()
    assert "creationflags" in kw
    assert "start_new_session" not in kw
    assert kw["creationflags"] == _DETACH_FLAGS


def test_detach_spawn_kwargs_posix_returns_start_new_session(monkeypatch):
    import spawn_utils
    fake_os = types.ModuleType("os_posix")
    fake_os.__dict__.update(os.__dict__)
    fake_os.name = "posix"
    monkeypatch.setattr(spawn_utils, "os", fake_os)
    kw = spawn_utils.detach_spawn_kwargs()
    assert kw == {"start_new_session": True}
    assert "creationflags" not in kw


# ---------------------------------------------------------------------------
# measure.py: _defer_session_end_flush (line ~6059)
# ---------------------------------------------------------------------------
def test_session_end_flush_nt_uses_creationflags(m, monkeypatch):
    _set_nt_spawn_utils(monkeypatch)
    cap = _capture_popen(m, monkeypatch)
    m._defer_session_end_flush(["measure.py", "session-end", "--session", "t"])
    assert "creationflags" in cap, "nt spawn must pass creationflags"
    assert cap["creationflags"] == _DETACH_FLAGS
    assert "start_new_session" not in cap


def test_session_end_flush_posix_uses_start_new_session(m, monkeypatch):
    _set_posix(monkeypatch, m)
    cap = _capture_popen(m, monkeypatch)
    m._defer_session_end_flush(["measure.py", "session-end", "--session", "t"])
    assert cap.get("start_new_session") is True
    assert "creationflags" not in cap


# ---------------------------------------------------------------------------
# measure.py: daemon-revive spawn (line ~21658) -- unchanged, still uses
# the inline pattern. Verify it stays byte-safe (not refactored onto helper).
# ---------------------------------------------------------------------------
def test_daemon_revive_nt_uses_creationflags(m, monkeypatch):
    """CXP-1 reference pattern at ~21658 must remain inline and correct on nt."""
    monkeypatch.setattr(m, "_verify_daemon_port", lambda **k: False)  # dead
    monkeypatch.setattr(m, "_normalized_platform", lambda: "Windows")
    _set_nt(monkeypatch, m)
    cap = {}
    def fake_popen(argv, *a, **k):
        cap.update(k)
        class _P: pass
        return _P()
    monkeypatch.setattr(m.subprocess, "Popen", fake_popen)
    assert m._daemon_midsession_pulse() == "revive-spawned"
    assert "creationflags" in cap
    assert cap["creationflags"] == _DETACH_FLAGS
    assert "start_new_session" not in cap


def test_daemon_revive_posix_uses_start_new_session(m, monkeypatch):
    monkeypatch.setattr(m, "_verify_daemon_port", lambda **k: False)
    _set_posix(monkeypatch, m)
    cap = {}
    def fake_popen(argv, *a, **k):
        cap.update(k)
        class _P: pass
        return _P()
    monkeypatch.setattr(m.subprocess, "Popen", fake_popen)
    assert m._daemon_midsession_pulse() == "revive-spawned"
    assert cap.get("start_new_session") is True
    assert "creationflags" not in cap


# ---------------------------------------------------------------------------
# measure.py: generated daemon regen spawn (line ~19290, inside f-string
# template). The generated daemon is standalone -- no spawn_utils import.
# Verify the generated source inlines CREATE_NO_WINDOW on nt.
# ---------------------------------------------------------------------------
def test_daemon_regen_source_inlines_create_no_window():
    """The generated dashboard-server.py must inline CREATE_NO_WINDOW, not
    reference the shared helper (it has no sibling spawn_utils.py)."""
    if "measure" in sys.modules:
        del sys.modules["measure"]
    import measure
    src = measure._generate_daemon_script()
    assert "CREATE_NO_WINDOW" in src, "generated daemon must inline CREATE_NO_WINDOW"
    assert "detach_spawn_kwargs" not in src, (
        "generated daemon must NOT reference the shared helper (no sibling on sys.path)"
    )
    assert "start_new_session" in src, "posix branch must still use start_new_session"
    if "measure" in sys.modules:
        del sys.modules["measure"]


# ---------------------------------------------------------------------------
# measure.py: run_ensure_health auto-update spawn (line ~34363)
# ---------------------------------------------------------------------------
def _stub_ensure_health(m, monkeypatch, tmp_path):
    """Stub heavy helpers so run_ensure_health reaches the auto-update Popen."""
    install_dir = tmp_path / "token-optimizer"
    install_dir.mkdir()
    (install_dir / ".git").mkdir()
    (install_dir / "install.sh").write_text("#!/bin/bash\nexit 0\n")
    monkeypatch.setattr(m, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(m, "_read_settings_json", lambda: ({}, None))
    monkeypatch.setattr(m, "_write_settings_atomic", lambda d: None)
    monkeypatch.setattr(m, "_auto_remove_bad_env_vars", lambda: None)
    monkeypatch.setattr(m, "_ensure_dashboard_daemon", lambda *a, **k: "noop-healthy")
    monkeypatch.setattr(m, "setup_quality_bar", lambda *a, **k: None)
    monkeypatch.setattr(m, "_read_config_flag", lambda k, d=None: False)


def test_ensure_health_auto_update_nt_uses_creationflags(m, monkeypatch, tmp_path):
    _set_nt_spawn_utils(monkeypatch)
    _stub_ensure_health(m, monkeypatch, tmp_path)
    cap = _capture_popen(m, monkeypatch)
    m.run_ensure_health()
    assert "creationflags" in cap, "nt auto-update spawn must pass creationflags"
    assert cap["creationflags"] == _DETACH_FLAGS
    assert "start_new_session" not in cap


def test_ensure_health_auto_update_posix_uses_start_new_session(m, monkeypatch, tmp_path):
    _set_posix(monkeypatch, m)
    _stub_ensure_health(m, monkeypatch, tmp_path)
    cap = _capture_popen(m, monkeypatch)
    m.run_ensure_health()
    assert cap.get("start_new_session") is True
    assert "creationflags" not in cap


# ---------------------------------------------------------------------------
# hermes_hook_bridge.py: run_rollup (~185) and run_dashboard (~226)
# ---------------------------------------------------------------------------
@pytest.fixture()
def hermes(monkeypatch):
    for _mod in ("hermes_hook_bridge", "spawn_utils", "runtime_env"):
        if _mod in sys.modules:
            del sys.modules[_mod]
    mod = importlib.import_module("hermes_hook_bridge")
    mod._locate_measure_py_cache = mod._SENTINEL
    yield mod
    for _mod in ("hermes_hook_bridge", "spawn_utils", "runtime_env"):
        if _mod in sys.modules:
            del sys.modules[_mod]


def test_hermes_rollup_nt_uses_creationflags(hermes, monkeypatch):
    _set_nt_spawn_utils(monkeypatch)
    cap = {}
    def fake_popen(argv, *a, **k):
        cap.update(k)
        class _P: pass
        return _P()
    monkeypatch.setattr(hermes.subprocess, "Popen", fake_popen)
    hermes.run_rollup("sid", "hermes", "stop")
    assert "creationflags" in cap
    assert cap["creationflags"] == _DETACH_FLAGS
    assert "start_new_session" not in cap


def test_hermes_rollup_posix_uses_start_new_session(hermes, monkeypatch):
    _set_posix(monkeypatch, hermes)
    cap = {}
    def fake_popen(argv, *a, **k):
        cap.update(k)
        class _P: pass
        return _P()
    monkeypatch.setattr(hermes.subprocess, "Popen", fake_popen)
    hermes.run_rollup("sid", "hermes", "stop")
    assert cap.get("start_new_session") is True
    assert "creationflags" not in cap


def test_hermes_dashboard_nt_uses_creationflags(hermes, monkeypatch):
    _set_nt_spawn_utils(monkeypatch)
    cap = {}
    def fake_popen(argv, *a, **k):
        cap.update(k)
        class _P: pass
        return _P()
    monkeypatch.setattr(hermes.subprocess, "Popen", fake_popen)
    hermes.run_dashboard("sid", 24844)
    assert "creationflags" in cap
    assert "start_new_session" not in cap


def test_hermes_dashboard_posix_uses_start_new_session(hermes, monkeypatch):
    _set_posix(monkeypatch, hermes)
    cap = {}
    def fake_popen(argv, *a, **k):
        cap.update(k)
        class _P: pass
        return _P()
    monkeypatch.setattr(hermes.subprocess, "Popen", fake_popen)
    hermes.run_dashboard("sid", 24844)
    assert cap.get("start_new_session") is True
    assert "creationflags" not in cap


# ---------------------------------------------------------------------------
# copilot_hook_bridge.py: handle_stop (~589)
# ---------------------------------------------------------------------------
@pytest.fixture()
def copilot(monkeypatch):
    for _mod in ("copilot_hook_bridge", "spawn_utils", "runtime_env"):
        if _mod in sys.modules:
            del sys.modules[_mod]
    mod = importlib.import_module("copilot_hook_bridge")
    yield mod
    for _mod in ("copilot_hook_bridge", "spawn_utils", "runtime_env"):
        if _mod in sys.modules:
            del sys.modules[_mod]


def test_copilot_stop_nt_uses_creationflags(copilot, monkeypatch):
    _set_nt_spawn_utils(monkeypatch)
    cap = {}
    def fake_popen(argv, *a, **k):
        cap.update(k)
        class _P: pass
        return _P()
    monkeypatch.setattr(copilot.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(copilot, "_to_dir", lambda: None)
    copilot.handle_stop({"sessionId": "sid", "toolName": "stop"})
    assert "creationflags" in cap
    assert cap["creationflags"] == _DETACH_FLAGS
    assert "start_new_session" not in cap


def test_copilot_stop_posix_uses_start_new_session(copilot, monkeypatch):
    _set_posix(monkeypatch, copilot)
    cap = {}
    def fake_popen(argv, *a, **k):
        cap.update(k)
        class _P: pass
        return _P()
    monkeypatch.setattr(copilot.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(copilot, "_to_dir", lambda: None)
    copilot.handle_stop({"sessionId": "sid", "toolName": "stop"})
    assert cap.get("start_new_session") is True
    assert "creationflags" not in cap


# ---------------------------------------------------------------------------
# hooks/run.py: SPECIAL spawn (inherits stdio) + tree reap
# ---------------------------------------------------------------------------
def _load_run_py():
    """Load hooks/run.py as an isolated module (it is a script, not a package)."""
    spec = importlib.util.spec_from_file_location("run_py_under_test", HOOKS / "run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_plugin_root(tmp_path):
    """Build a minimal plugin root so run.py's path resolver finds the target."""
    root = tmp_path / "plugin"
    (root / "hooks").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "hooks" / "module_runner.py").write_text(
        "import sys; sys.exit(0)\n"
    )
    (root / "scripts" / "dummy_hook.py").write_text("print('ok')\n")
    return root


def test_run_py_spawn_nt_uses_create_no_window(monkeypatch, tmp_path):
    """run.py:213 SPECIAL: on nt use CREATE_NO_WINDOW|CREATE_NEW_PROCESS_GROUP,
    NOT DETACHED_PROCESS. The child MUST inherit run.py's stdio (no DEVNULL)."""
    mod = _load_run_py()
    _set_nt(monkeypatch, mod)
    root = _make_plugin_root(tmp_path)
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(root / "_data"))
    monkeypatch.setattr(mod, "_check_consent", lambda: True)
    cap = {}
    def fake_popen(cmd, **k):
        cap.update(k)
        class _P:
            pid = 12345
            def poll(self):
                return 0
            def wait(self, timeout=None):
                return 0
            def kill(self):
                pass
        return _P()
    monkeypatch.setattr(mod.subprocess, "Popen", fake_popen)
    mod.sys.argv = ["run.py", "scripts/dummy_hook.py", "--quiet"]
    mod.main()
    assert "creationflags" in cap, "nt spawn must pass creationflags"
    assert cap["creationflags"] == (_CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP)
    assert "start_new_session" not in cap
    # CRITICAL: no DEVNULL on stdio -- the child inherits run.py's pipes.
    assert cap.get("stdin") != subprocess.DEVNULL
    assert cap.get("stdout") != subprocess.DEVNULL
    assert cap.get("stderr") != subprocess.DEVNULL


def test_run_py_spawn_posix_uses_start_new_session(monkeypatch, tmp_path):
    mod = _load_run_py()
    _set_posix(monkeypatch, mod)
    root = _make_plugin_root(tmp_path)
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(root / "_data"))
    monkeypatch.setattr(mod, "_check_consent", lambda: True)
    cap = {}
    def fake_popen(cmd, **k):
        cap.update(k)
        class _P:
            pid = 12345
            def poll(self):
                return 0
            def wait(self, timeout=None):
                return 0
            def kill(self):
                pass
        return _P()
    monkeypatch.setattr(mod.subprocess, "Popen", fake_popen)
    mod.sys.argv = ["run.py", "scripts/dummy_hook.py", "--quiet"]
    mod.main()
    assert cap.get("start_new_session") is True
    assert "creationflags" not in cap


def test_run_py_timeout_reap_nt_uses_taskkill(monkeypatch, tmp_path):
    """On nt, TimeoutExpired must reap via taskkill /F /T /PID (windowless),
    not os.killpg (which does not exist on Windows)."""
    mod = _load_run_py()
    _set_nt(monkeypatch, mod)
    root = _make_plugin_root(tmp_path)
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(root / "_data"))
    monkeypatch.setattr(mod, "_check_consent", lambda: True)
    class _FakeProc:
        pid = 54321
        def poll(self):
            return None  # still running
        def wait(self, timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired(cmd="x", timeout=timeout)
            return 0
        def kill(self):
            pass
    def fake_popen(cmd, **k):
        return _FakeProc()
    monkeypatch.setattr(mod.subprocess, "Popen", fake_popen)
    taskkill_calls = []
    def fake_run(cmd, **k):
        taskkill_calls.append(cmd)
        class _R:
            returncode = 0
        return _R()
    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    mod.sys.argv = ["run.py", "scripts/dummy_hook.py", "--quiet"]
    mod.main()
    assert len(taskkill_calls) == 1, "nt timeout must reap via taskkill"
    assert taskkill_calls[0] == ["taskkill", "/F", "/T", "/PID", "54321"]


def test_run_py_timeout_reap_posix_uses_killpg(monkeypatch, tmp_path):
    """On posix, TimeoutExpired reaps via os.killpg (unchanged behavior)."""
    mod = _load_run_py()
    _set_posix(monkeypatch, mod)
    root = _make_plugin_root(tmp_path)
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(root / "_data"))
    monkeypatch.setattr(mod, "_check_consent", lambda: True)
    killpg_calls = []
    class _FakeProc:
        pid = 54321
        def poll(self):
            return None
        def wait(self, timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired(cmd="x", timeout=timeout)
            return 0
        def kill(self):
            killpg_calls.append("kill")
    def fake_popen(cmd, **k):
        return _FakeProc()
    monkeypatch.setattr(mod.subprocess, "Popen", fake_popen)
    def fake_killpg(pgid, sig):
        killpg_calls.append(("killpg", pgid, sig))
    monkeypatch.setattr(mod.os, "killpg", fake_killpg)
    monkeypatch.setattr(mod.os, "getpgid", lambda pid: pid)
    mod.sys.argv = ["run.py", "scripts/dummy_hook.py", "--quiet"]
    mod.main()
    assert any(isinstance(c, tuple) and c[0] == "killpg" for c in killpg_calls), (
        "posix timeout must reap via os.killpg"
    )
