"""Windows console-flash elimination + child-tree reap coverage.

On Windows, ``start_new_session=True`` is silently ignored by ``subprocess``,
so a detached child inherits the parent's console and flashes a ~1s window on
every spawn. Every fire-and-forget spawn must now route through
``spawn_utils.spawn_detached()`` (POSIX: ``start_new_session``; Windows:
``DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB``,
with a retry that drops ``CREATE_BREAKAWAY_FROM_JOB`` if ``CreateProcess``
raises ``OSError`` inside a restrictive Job Object).

The one exception is the daemon-regen spawn inside the generated
``dashboard-server.py`` template: that file is standalone (no sibling
``spawn_utils.py``), so the detach logic is inlined as ``CREATE_NO_WINDOW`` on
nt (the regen child is a transient worker, not a survivor).

The daemon-revive spawn (~line 21660 in measure.py) uses
``detach_spawn_kwargs()`` directly (single source of truth) because the caller
already swallows ``Exception`` and the child must survive the hook.

``hooks/run.py`` is also special: its child MUST inherit run.py's stdio for
hook injection via stdout, so it uses ``CREATE_NO_WINDOW`` only (NOT
``DETACHED_PROCESS``, NOT ``CREATE_NEW_PROCESS_GROUP``) on Windows. Because
module_runner.py runs measure.py in-process, the child proc IS the lock
holder, so Windows reaps with plain ``proc.kill()`` (TerminateProcess of
proc.pid only) -- NOT ``taskkill /F /T`` which would walk the PPID tree and
kill the detached session-end-flush worker. POSIX keeps ``os.killpg``.

Run: python3 -m pytest tests/test_windows_spawn_no_window.py -v
"""
import importlib
import importlib.util
import os
import re
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


def _make_fake_os(name):
    """Create a fake ``os`` module with the given ``name`` but real posixpath.

    Monkeypatching ``os.name`` on the real ``os`` module breaks ``pathlib``
    on macOS (Python 3.14's pathlib dispatches path flavour on ``os.name``).
    Instead we build a shallow copy of the real ``os`` module's namespace,
    override only ``name``, and inject it into the module under test. The copy
    keeps ``os.path`` as ``posixpath`` so path resolution still works.
    """
    fake = types.ModuleType("os_" + name)
    fake.__dict__.update(os.__dict__)
    fake.name = name
    return fake


def _set_nt_spawn_utils(monkeypatch):
    """Patch ``spawn_utils`` so ``detach_spawn_kwargs()``/``spawn_detached()``
    see ``os.name == 'nt'`` and Windows flag constants.

    The helpers read ``os.name`` and flag constants from their OWN namespace
    (``spawn_utils.os``, ``spawn_utils.subprocess``), not the caller's. We
    replace ``spawn_utils.os`` with a fake that has ``name='nt'`` but keeps
    ``posixpath`` so ``pathlib`` in the CALLER (measure, hermes, copilot) is
    unaffected -- ``spawn_utils`` only reads ``os.name``, never touches paths.
    """
    import spawn_utils
    monkeypatch.setattr(spawn_utils, "os", _make_fake_os("nt"))
    for _name, _val in _NT_FLAGS.items():
        monkeypatch.setattr(spawn_utils.subprocess, _name, _val, raising=False)


def _set_posix_spawn_utils(monkeypatch):
    """Patch ``spawn_utils`` so ``detach_spawn_kwargs()`` returns posix kwargs."""
    import spawn_utils
    monkeypatch.setattr(spawn_utils, "os", _make_fake_os("posix"))
    for _name in _NT_FLAGS:
        monkeypatch.delattr(spawn_utils.subprocess, _name, raising=False)


def _set_nt(monkeypatch, mod):
    """Patch *mod* so ``os.name == 'nt'`` and Windows flag constants exist.

    Use ``_set_nt_spawn_utils`` instead for modules that route spawns through
    ``spawn_detached()`` and don't have inline ``os.name`` checks (measure,
    hermes, copilot). This heavier helper replaces ``mod.os`` with a fake that
    has ``name='nt'`` but keeps ``posixpath`` -- needed for run.py which has
    inline ``os.name == 'nt'`` branches AND must not break pathlib.
    """
    monkeypatch.setattr(mod, "os", _make_fake_os("nt"))
    for _name, _val in _NT_FLAGS.items():
        monkeypatch.setattr(mod.subprocess, _name, _val, raising=False)
    if "spawn_utils" in sys.modules:
        _set_nt_spawn_utils(monkeypatch)


def _set_posix(monkeypatch, mod):
    """Patch *mod* so ``os.name == 'posix'`` and no Windows flags exist.

    Use ``_set_posix_spawn_utils`` instead for modules that route spawns
    through ``spawn_detached()``.
    """
    monkeypatch.setattr(mod, "os", _make_fake_os("posix"))
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


def _capture_spawn_detached_popen(monkeypatch):
    """Patch ``spawn_utils.subprocess.Popen`` (what ``spawn_detached`` calls)
    with a kwargs-capturing fake. Returns the capture dict."""
    import spawn_utils
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
    monkeypatch.setattr(spawn_utils.subprocess, "Popen", fake_popen)
    return captured


# ---------------------------------------------------------------------------
# spawn_utils unit tests
# ---------------------------------------------------------------------------
def test_detach_spawn_kwargs_nt_returns_creationflags(monkeypatch):
    import spawn_utils
    monkeypatch.setattr(spawn_utils, "os", _make_fake_os("nt"))
    for _name, _val in _NT_FLAGS.items():
        monkeypatch.setattr(spawn_utils.subprocess, _name, _val, raising=False)
    kw = spawn_utils.detach_spawn_kwargs()
    assert "creationflags" in kw
    assert "start_new_session" not in kw
    assert kw["creationflags"] == _DETACH_FLAGS


def test_detach_spawn_kwargs_posix_returns_start_new_session(monkeypatch):
    import spawn_utils
    monkeypatch.setattr(spawn_utils, "os", _make_fake_os("posix"))
    kw = spawn_utils.detach_spawn_kwargs()
    assert kw == {"start_new_session": True}
    assert "creationflags" not in kw


def test_spawn_detached_nt_passes_creationflags(monkeypatch):
    import spawn_utils
    monkeypatch.setattr(spawn_utils, "os", _make_fake_os("nt"))
    for _name, _val in _NT_FLAGS.items():
        monkeypatch.setattr(spawn_utils.subprocess, _name, _val, raising=False)
    cap = {}
    def fake_popen(argv, **k):
        cap.update(k)
        class _P: pass
        return _P()
    monkeypatch.setattr(spawn_utils.subprocess, "Popen", fake_popen)
    spawn_utils.spawn_detached(["x"], stdout=subprocess.DEVNULL)
    assert cap["creationflags"] == _DETACH_FLAGS
    assert "start_new_session" not in cap


def test_spawn_detached_posix_passes_start_new_session(monkeypatch):
    import spawn_utils
    monkeypatch.setattr(spawn_utils, "os", _make_fake_os("posix"))
    cap = {}
    def fake_popen(argv, **k):
        cap.update(k)
        class _P: pass
        return _P()
    monkeypatch.setattr(spawn_utils.subprocess, "Popen", fake_popen)
    spawn_utils.spawn_detached(["x"], stdout=subprocess.DEVNULL)
    assert cap.get("start_new_session") is True
    assert "creationflags" not in cap


def test_spawn_detached_nt_retries_without_breakaway_on_oserror(monkeypatch):
    """If CreateProcess fails with OSError (ACCESS_DENIED inside a restrictive
    Job Object), spawn_detached retries once without CREATE_BREAKAWAY_FROM_JOB."""
    import spawn_utils
    monkeypatch.setattr(spawn_utils, "os", _make_fake_os("nt"))
    for _name, _val in _NT_FLAGS.items():
        monkeypatch.setattr(spawn_utils.subprocess, _name, _val, raising=False)
    attempts = []
    def fake_popen(argv, **k):
        attempts.append(k.get("creationflags", 0))
        if len(attempts) == 1:
            raise OSError("Access is denied")
        class _P: pass
        return _P()
    monkeypatch.setattr(spawn_utils.subprocess, "Popen", fake_popen)
    result = spawn_utils.spawn_detached(["x"])
    assert result is not None, "retry must succeed"
    assert len(attempts) == 2
    assert attempts[0] == _DETACH_FLAGS
    assert attempts[1] == (_DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP)
    assert not (attempts[1] & _CREATE_BREAKAWAY_FROM_JOB)


def test_spawn_detached_nt_returns_none_on_double_failure(monkeypatch):
    import spawn_utils
    monkeypatch.setattr(spawn_utils, "os", _make_fake_os("nt"))
    for _name, _val in _NT_FLAGS.items():
        monkeypatch.setattr(spawn_utils.subprocess, _name, _val, raising=False)
    def fake_popen(argv, **k):
        raise OSError("nope")
    monkeypatch.setattr(spawn_utils.subprocess, "Popen", fake_popen)
    assert spawn_utils.spawn_detached(["x"]) is None


def test_spawn_detached_posix_returns_none_on_oserror(monkeypatch):
    import spawn_utils
    monkeypatch.setattr(spawn_utils, "os", _make_fake_os("posix"))
    def fake_popen(argv, **k):
        raise OSError("nope")
    monkeypatch.setattr(spawn_utils.subprocess, "Popen", fake_popen)
    assert spawn_utils.spawn_detached(["x"]) is None


# ---------------------------------------------------------------------------
# measure.py: _defer_session_end_flush (line ~6059) -- uses spawn_detached
# ---------------------------------------------------------------------------
def test_session_end_flush_nt_uses_creationflags(m, monkeypatch):
    _set_nt_spawn_utils(monkeypatch)
    cap = _capture_spawn_detached_popen(monkeypatch)
    m._defer_session_end_flush(["measure.py", "session-end", "--session", "t"])
    assert "creationflags" in cap, "nt spawn must pass creationflags"
    assert cap["creationflags"] == _DETACH_FLAGS
    assert "start_new_session" not in cap


def test_session_end_flush_posix_uses_start_new_session(m, monkeypatch):
    _set_posix_spawn_utils(monkeypatch)
    cap = _capture_spawn_detached_popen(monkeypatch)
    m._defer_session_end_flush(["measure.py", "session-end", "--session", "t"])
    assert cap.get("start_new_session") is True
    assert "creationflags" not in cap


# ---------------------------------------------------------------------------
# measure.py: daemon-revive spawn (line ~21660) -- uses detach_spawn_kwargs()
# directly (single source of truth), not spawn_detached.
# ---------------------------------------------------------------------------
def test_daemon_revive_nt_uses_creationflags(m, monkeypatch):
    """The daemon-revive spawn must route through detach_spawn_kwargs() on nt."""
    monkeypatch.setattr(m, "_verify_daemon_port", lambda **k: False)  # dead
    monkeypatch.setattr(m, "_normalized_platform", lambda: "Windows")
    _set_nt_spawn_utils(monkeypatch)
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
    _set_posix_spawn_utils(monkeypatch)
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
# Exec the generated template's _maybe_refresh_dashboard with a fake
# subprocess.Popen to assert the inlined nt branch produces CREATE_NO_WINDOW.
# ---------------------------------------------------------------------------
def test_daemon_regen_nt_uses_create_no_window(monkeypatch, tmp_path):
    """Exec the generated daemon source and assert that on nt the regen spawn
    passes CREATE_NO_WINDOW (not DETACHED_PROCESS, not the shared helper)."""
    if "measure" in sys.modules:
        del sys.modules["measure"]
    import measure
    src = measure._generate_daemon_script()
    if "measure" in sys.modules:
        del sys.modules["measure"]

    # The generated daemon must NOT reference the shared helper.
    assert "detach_spawn_kwargs" not in src, (
        "generated daemon must NOT reference the shared helper (no sibling on sys.path)"
    )
    assert "spawn_detached" not in src, (
        "generated daemon must NOT reference the shared helper (no sibling on sys.path)"
    )

    dashboard = tmp_path / "dashboard.html"
    dashboard.write_text("<html></html>")
    regen_log = tmp_path / "regen.log"
    measure_py = tmp_path / "measure.py"
    measure_py.write_text("# stub\n")

    fake_os = _make_fake_os("nt")
    fake_sub = types.ModuleType("subprocess")
    for _name, _val in _NT_FLAGS.items():
        setattr(fake_sub, _name, _val)
    fake_sub.DEVNULL = subprocess.DEVNULL
    fake_sub.STDOUT = subprocess.STDOUT

    cap = {}
    def fake_popen(argv, **k):
        cap.update(k)
        class _P: pass
        return _P()
    fake_sub.Popen = fake_popen

    # The generated method does `import subprocess` locally, so we must
    # inject our fake into sys.modules so the import gets it.
    monkeypatch.setitem(sys.modules, "subprocess", fake_sub)

    ns = {
        "os": fake_os,
        "sys": types.ModuleType("sys"),
        "time": __import__("time"),
        "Path": Path,
        "DASHBOARD": str(dashboard),
        "REGEN_LOG": str(regen_log),
        "MEASURE_PY_FALLBACK": str(measure_py),
        "_MEASURE_PY_CACHE": ("", 0.0),
        "MEASURE_PY_RESOLVE_TTL": 300,
        "DASHBOARD_FRESH_SECONDS": 120,
        "_last_regen": 0.0,
        "_resolve_measure_py": lambda: str(measure_py),
        "_log_regen": lambda msg: None,
    }
    ns["sys"].executable = sys.executable
    ns["__name__"] = "dashboard_server_test"

    import textwrap
    m = re.search(r"^    def _maybe_refresh_dashboard\(self\):.*?\n(?=^    def |^class |\Z)",
                  src, re.M | re.S)
    assert m, "_maybe_refresh_dashboard missing from generated daemon"
    dedented = textwrap.dedent(m.group(0))
    dedented = dedented.replace("(self):", "():")
    exec(dedented, ns)

    import time as _time
    old_mtime = _time.time() - 9999
    os.utime(str(dashboard), (old_mtime, old_mtime))

    ns["_maybe_refresh_dashboard"]()

    assert "creationflags" in cap, "nt regen spawn must pass CREATE_NO_WINDOW"
    assert cap["creationflags"] == _CREATE_NO_WINDOW
    assert "start_new_session" not in cap
    # The regen child is transient, NOT detached.
    assert not (cap["creationflags"] & _DETACHED_PROCESS)


def test_daemon_regen_posix_uses_start_new_session(monkeypatch, tmp_path):
    """On posix the generated daemon's regen spawn uses start_new_session."""
    if "measure" in sys.modules:
        del sys.modules["measure"]
    import measure
    src = measure._generate_daemon_script()
    if "measure" in sys.modules:
        del sys.modules["measure"]

    dashboard = tmp_path / "dashboard.html"
    dashboard.write_text("<html></html>")
    regen_log = tmp_path / "regen.log"
    measure_py = tmp_path / "measure.py"
    measure_py.write_text("# stub\n")

    fake_os = _make_fake_os("posix")
    fake_sub = types.ModuleType("subprocess")
    fake_sub.DEVNULL = subprocess.DEVNULL
    fake_sub.STDOUT = subprocess.STDOUT

    cap = {}
    def fake_popen(argv, **k):
        cap.update(k)
        class _P: pass
        return _P()
    fake_sub.Popen = fake_popen

    monkeypatch.setitem(sys.modules, "subprocess", fake_sub)

    ns = {
        "os": fake_os,
        "sys": types.ModuleType("sys"),
        "time": __import__("time"),
        "Path": Path,
        "DASHBOARD": str(dashboard),
        "REGEN_LOG": str(regen_log),
        "MEASURE_PY_FALLBACK": str(measure_py),
        "_MEASURE_PY_CACHE": ("", 0.0),
        "MEASURE_PY_RESOLVE_TTL": 300,
        "DASHBOARD_FRESH_SECONDS": 120,
        "_last_regen": 0.0,
        "_resolve_measure_py": lambda: str(measure_py),
        "_log_regen": lambda msg: None,
    }
    ns["sys"].executable = sys.executable
    ns["__name__"] = "dashboard_server_test"

    import textwrap
    m = re.search(r"^    def _maybe_refresh_dashboard\(self\):.*?\n(?=^    def |^class |\Z)",
                  src, re.M | re.S)
    assert m, "_maybe_refresh_dashboard missing from generated daemon"
    dedented = textwrap.dedent(m.group(0))
    dedented = dedented.replace("(self):", "():")
    exec(dedented, ns)

    import time as _time
    old_mtime = _time.time() - 9999
    os.utime(str(dashboard), (old_mtime, old_mtime))

    ns["_maybe_refresh_dashboard"]()

    assert cap.get("start_new_session") is True
    assert "creationflags" not in cap


def test_daemon_regen_closes_errf_on_success(monkeypatch, tmp_path):
    """The generated daemon must close errf after Popen (no fd leak)."""
    if "measure" in sys.modules:
        del sys.modules["measure"]
    import measure
    src = measure._generate_daemon_script()
    if "measure" in sys.modules:
        del sys.modules["measure"]
    # The source must have a finally: errf.close() pattern.
    assert "errf.close()" in src, "generated daemon must close errf in a finally block"
    assert re.search(r"finally:\s+.*errf\.close\(\)", src, re.S), (
        "errf.close() must be in a finally block"
    )


# ---------------------------------------------------------------------------
# measure.py: run_ensure_health auto-update spawn (line ~34367) -- spawn_detached
# ---------------------------------------------------------------------------
def _stub_ensure_health(m, monkeypatch, tmp_path):
    """Stub heavy helpers so run_ensure_health reaches the auto-update spawn."""
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
    cap = _capture_spawn_detached_popen(monkeypatch)
    m.run_ensure_health()
    assert "creationflags" in cap, "nt auto-update spawn must pass creationflags"
    assert cap["creationflags"] == _DETACH_FLAGS
    assert "start_new_session" not in cap


def test_ensure_health_auto_update_posix_uses_start_new_session(m, monkeypatch, tmp_path):
    _set_posix_spawn_utils(monkeypatch)
    _stub_ensure_health(m, monkeypatch, tmp_path)
    cap = _capture_spawn_detached_popen(monkeypatch)
    m.run_ensure_health()
    assert cap.get("start_new_session") is True
    assert "creationflags" not in cap


# ---------------------------------------------------------------------------
# hermes_hook_bridge.py: run_rollup (~185) and run_dashboard (~226) -- spawn_detached
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
    cap = _capture_spawn_detached_popen(monkeypatch)
    hermes.run_rollup("sid", "hermes", "stop")
    assert "creationflags" in cap
    assert cap["creationflags"] == _DETACH_FLAGS
    assert "start_new_session" not in cap


def test_hermes_rollup_posix_uses_start_new_session(hermes, monkeypatch):
    _set_posix_spawn_utils(monkeypatch)
    cap = _capture_spawn_detached_popen(monkeypatch)
    hermes.run_rollup("sid", "hermes", "stop")
    assert cap.get("start_new_session") is True
    assert "creationflags" not in cap


def test_hermes_dashboard_nt_uses_creationflags(hermes, monkeypatch):
    _set_nt_spawn_utils(monkeypatch)
    cap = _capture_spawn_detached_popen(monkeypatch)
    hermes.run_dashboard("sid", 24844)
    assert "creationflags" in cap
    assert "start_new_session" not in cap


def test_hermes_dashboard_posix_uses_start_new_session(hermes, monkeypatch):
    _set_posix_spawn_utils(monkeypatch)
    cap = _capture_spawn_detached_popen(monkeypatch)
    hermes.run_dashboard("sid", 24844)
    assert cap.get("start_new_session") is True
    assert "creationflags" not in cap


# ---------------------------------------------------------------------------
# copilot_hook_bridge.py: handle_stop (~589) -- spawn_detached
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
    cap = _capture_spawn_detached_popen(monkeypatch)
    monkeypatch.setattr(copilot, "_to_dir", lambda: None)
    copilot.handle_stop({"sessionId": "sid", "toolName": "stop"})
    assert "creationflags" in cap
    assert cap["creationflags"] == _DETACH_FLAGS
    assert "start_new_session" not in cap


def test_copilot_stop_posix_uses_start_new_session(copilot, monkeypatch):
    _set_posix_spawn_utils(monkeypatch)
    cap = _capture_spawn_detached_popen(monkeypatch)
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
    """run.py SPECIAL: on nt use CREATE_NO_WINDOW ONLY (not
    CREATE_NEW_PROCESS_GROUP -- it is inert for reaping and disables the
    child's Ctrl+C self-terminate). The child MUST inherit run.py's stdio
    (no DEVNULL)."""
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
    assert cap["creationflags"] == _CREATE_NO_WINDOW
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


def test_run_py_timeout_reap_nt_uses_proc_kill(monkeypatch, tmp_path):
    """On nt, TimeoutExpired must reap via proc.kill() (TerminateProcess of
    proc.pid only), NOT taskkill /F /T which would walk the PPID tree and
    wrongly kill the detached session-end-flush worker. The child proc IS
    measure.py (module_runner.py runs it in-process), so proc.kill()
    releases the trends.db lock directly."""
    mod = _load_run_py()
    _set_nt(monkeypatch, mod)
    root = _make_plugin_root(tmp_path)
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(root / "_data"))
    monkeypatch.setattr(mod, "_check_consent", lambda: True)
    kill_calls = []
    class _FakeProc:
        pid = 54321
        def poll(self):
            return None  # still running
        def wait(self, timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired(cmd="x", timeout=timeout)
            return 0
        def kill(self):
            kill_calls.append("proc.kill")
    def fake_popen(cmd, **k):
        return _FakeProc()
    monkeypatch.setattr(mod.subprocess, "Popen", fake_popen)
    # Ensure subprocess.run is NOT called (no taskkill).
    run_calls = []
    def fake_run(cmd, **k):
        run_calls.append(cmd)
        class _R:
            returncode = 0
        return _R()
    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    mod.sys.argv = ["run.py", "scripts/dummy_hook.py", "--quiet"]
    mod.main()
    assert len(kill_calls) == 1, "nt timeout must reap via proc.kill()"
    assert kill_calls[0] == "proc.kill"
    assert run_calls == [], "nt timeout must NOT use taskkill/subprocess.run"


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


def test_run_py_forward_and_exit_nt_uses_proc_kill(monkeypatch):
    """_forward_and_exit(SIGTERM, None) on nt must reap via proc.kill()
    (TerminateProcess of proc.pid only), NOT taskkill /F /T which would
    walk the PPID tree and kill the detached session-end-flush worker.
    Wraps the os._exit(0)."""
    mod = _load_run_py()
    _set_nt(monkeypatch, mod)
    kill_calls = []
    run_calls = []
    def fake_run(cmd, **k):
        run_calls.append(cmd)
        class _R:
            returncode = 0
        return _R()
    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    class _FakeProc:
        pid = 77777
        def poll(self):
            return None  # still running
        def kill(self):
            kill_calls.append("proc.kill")

    monkeypatch.setattr(mod, "_child_proc", _FakeProc())
    # Prevent os._exit from killing the test process.
    monkeypatch.setattr(mod.os, "_exit", lambda code=0: None)

    import signal as _signal
    mod._forward_and_exit(_signal.SIGTERM, None)

    assert len(kill_calls) == 1, "nt signal handler must reap via proc.kill()"
    assert kill_calls[0] == "proc.kill"
    assert run_calls == [], "nt signal handler must NOT use taskkill/subprocess.run"


def test_run_py_forward_and_exit_posix_uses_killpg(monkeypatch):
    """_forward_and_exit(SIGTERM, None) on posix must reap via os.killpg."""
    mod = _load_run_py()
    _set_posix(monkeypatch, mod)
    killpg_calls = []
    def fake_killpg(pgid, sig):
        killpg_calls.append(("killpg", pgid, sig))
    monkeypatch.setattr(mod.os, "killpg", fake_killpg)
    monkeypatch.setattr(mod.os, "getpgid", lambda pid: pid)

    class _FakeProc:
        pid = 88888
        def poll(self):
            return None
        def kill(self):
            pass

    monkeypatch.setattr(mod, "_child_proc", _FakeProc())
    monkeypatch.setattr(mod.os, "_exit", lambda code=0: None)

    import signal as _signal
    mod._forward_and_exit(_signal.SIGTERM, None)

    assert any(isinstance(c, tuple) and c[0] == "killpg" for c in killpg_calls), (
        "posix signal handler must reap via os.killpg"
    )


# ---------------------------------------------------------------------------
# Real-spawn smoke test (runs only on actual Windows; CI windows-latest leg).
# Exercises spawn_utils.spawn_detached against a real child process and
# asserts the Popen is returned (not None) and the child exits 0.
# ---------------------------------------------------------------------------
@pytest.mark.skipif(os.name != "nt", reason="real-spawn smoke test is Windows-only")
def test_spawn_detached_real_child():
    """On real Windows, spawn_detached must actually spawn a child that exits 0.

    This is the only test that exercises the real CreateProcess path with
    CREATE_BREAKAWAY_FROM_JOB. The mock-based tests above verify the kwargs
    are correct; this one verifies the OS accepts them.
    """
    import spawn_utils
    proc = spawn_utils.spawn_detached(
        [sys.executable, "-c", "import sys; sys.exit(0)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )
    assert proc is not None, "spawn_detached returned None on real Windows"
    rc = proc.wait(timeout=10)
    assert rc == 0, f"real child exited {rc}, expected 0"
