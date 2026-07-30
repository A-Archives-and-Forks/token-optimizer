"""Acceptance tests for Lane B / #104: Windows pythonw.exe preference in
``hooks/python-launcher.sh``.

Covers the FABLE red-team amendments:
* swap at BOTH exec sites, at exec time (cache record still names python.exe,
  but exec uses pythonw.exe -- proven by the cached-path round-trip);
* skip WindowsApps pythonw aliases;
* stdout-pipe guard (pythonw GUI-subsystem can null sys.stdout -> dark hook
  protocol): swap only when fd 1 is a pipe, never a tty;
* non-Windows behaviour byte-for-byte unchanged (python.exe used, no swap);
* py.exe (``py -3``) is never swapped -- documented, not fixed;
* mirror stays byte-identical with the canonical launcher.
"""

from __future__ import annotations

import os
import pty
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LAUNCHER = REPO / "hooks" / "python-launcher.sh"
MIRROR = REPO / "plugins" / "token-optimizer" / "hooks" / "python-launcher.sh"


def _defs() -> str:
    """Function definitions from the launcher, up to (not including) the
    top-level ``_setup_interpreter_cache`` invocation. Includes every helper
    the exec sites depend on."""
    source = LAUNCHER.read_text(encoding="utf-8")
    return source[: source.index("\n_setup_interpreter_cache\n")]


def _fake_interp(tmp_path: Path, name: str, marker: str) -> Path:
    """A stand-in interpreter 'binary' (an executable shell script) that
    reads stdin, prints a marker line, and exits with FAKE_PY_EXIT. Lets us
    prove which interpreter the launcher actually exec'd and that the
    stdin->stdout round-trip survives, without needing a real Windows box.

    A liveness probe (`-c ""` with an empty program, as the launcher uses to
    test pythonw.exe) short-circuits to exit 0 so the twin looks healthy and
    the swap proceeds; the real exec then runs the round-trip and uses
    FAKE_PY_EXIT. Without this, a nonzero FAKE_PY_EXIT would make the probe
    reject the twin as broken and no swap would ever happen."""
    path = tmp_path / name
    path.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "-c" ] && [ "$2" = "" ]; then exit 0; fi\n'
        "input=$(cat)\n"
        f"printf '{marker} echo=%s\\n' \"$input\"\n"
        'exit "${FAKE_PY_EXIT:-0}"\n',
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _run(script: str, stdin: str, env: dict, *, use_pty: bool = False):
    """Run a bash driver script. Returns (stdout, returncode, stderr)."""
    full_env = os.environ.copy()
    full_env.update(env)
    if use_pty:
        master, slave = pty.openpty()
        proc = subprocess.Popen(
            ["/bin/bash", "-c", script],
            stdin=subprocess.PIPE,
            stdout=slave,
            stderr=slave,
            env=full_env,
            close_fds=True,
        )
        os.close(slave)
        assert proc.stdin is not None
        try:
            proc.stdin.write(stdin.encode("utf-8"))
            proc.stdin.close()
        except BrokenPipeError:
            pass
        chunks: list[bytes] = []
        deadline = time.time() + 10
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            import select

            ready, _, _ = select.select([master], [], [], remaining)
            if not ready:
                if proc.poll() is not None:
                    break
                continue
            try:
                data = os.read(master, 4096)
            except OSError:
                break
            if not data:
                break
            chunks.append(data)
        rc = proc.wait()
        try:
            os.close(master)
        except OSError:
            pass
        out = b"".join(chunks).decode("utf-8", errors="replace")
        # PTYs echo input + CR; strip the echoed stdin line and CRs.
        out = out.replace("\r\n", "\n").replace("\r", "\n")
        return out, rc, ""
    result = subprocess.run(
        ["/bin/bash", "-c", script],
        input=stdin.encode("utf-8"),
        env=full_env,
        capture_output=True,
        timeout=15,
    )
    return (
        result.stdout.decode("utf-8", errors="replace"),
        result.returncode,
        result.stderr.decode("utf-8", errors="replace"),
    )


def _selection_driver(candidate: str, *, msys: bool, safe: bool) -> str:
    """Drive _maybe_swap_to_pythonw in isolation and return _PYW_INTERP."""
    # bash: return 0 == success/true, return 1 == failure/false.
    overrides = (
        f"_is_msys_platform() {{ return {0 if msys else 1}; }}\n"
        f"_is_safe_prefix() {{ return {0 if safe else 1}; }}\n"
        "_setup_interpreter_cache() { :; }\n"
        "_write_interpreter_cache() { :; }\n"
    )
    body = overrides + (
        f'_maybe_swap_to_pythonw "{candidate}"\n'
        'printf "%s\\n" "$_PYW_INTERP"\n'
    )
    return _defs() + "\n" + body


# ---------------------------------------------------------------------------
# Selection logic (no exec): _maybe_swap_to_pythonw picks the right binary.
# ---------------------------------------------------------------------------


def test_selects_pythonw_when_present_off_windowsapps(tmp_path):
    py = _fake_interp(tmp_path, "python.exe", "PYTHON_SELECTED")
    pw = _fake_interp(tmp_path, "pythonw.exe", "PYTHONW_SELECTED")
    out, rc, _err = _run(_selection_driver(str(py), msys=True, safe=True), "", {})
    assert rc == 0
    assert out.strip() == str(pw)


def test_falls_back_to_python_when_no_pythonw(tmp_path):
    py = _fake_interp(tmp_path, "python.exe", "PYTHON_SELECTED")
    out, rc, _err = _run(_selection_driver(str(py), msys=True, safe=True), "", {})
    assert rc == 0
    assert out.strip() == str(py)


def test_skips_windowsapps_pythonw_alias(tmp_path):
    store = tmp_path / "WindowsApps"
    store.mkdir()
    py = _fake_interp(store, "python.exe", "PYTHON_SELECTED")
    _fake_interp(store, "pythonw.exe", "PYTHONW_SELECTED")  # present but an alias
    out, rc, _err = _run(_selection_driver(str(py), msys=True, safe=True), "", {})
    assert rc == 0
    assert out.strip() == str(py)


def test_skips_pythonw_outside_safe_prefix(tmp_path):
    py = _fake_interp(tmp_path, "python.exe", "PYTHON_SELECTED")
    _fake_interp(tmp_path, "pythonw.exe", "PYTHONW_SELECTED")
    out, rc, _err = _run(_selection_driver(str(py), msys=True, safe=False), "", {})
    assert rc == 0
    assert out.strip() == str(py)


def test_python3_exe_swaps_to_pythonw(tmp_path):
    py = _fake_interp(tmp_path, "python3.exe", "PYTHON_SELECTED")
    pw = _fake_interp(tmp_path, "pythonw.exe", "PYTHONW_SELECTED")
    out, rc, _err = _run(_selection_driver(str(py), msys=True, safe=True), "", {})
    assert rc == 0
    assert out.strip() == str(pw)


def test_py_launcher_is_not_swapped(tmp_path):
    pyl = _fake_interp(tmp_path, "py.exe", "PY_SELECTED")
    _fake_interp(tmp_path, "pythonw.exe", "PYTHONW_SELECTED")
    out, rc, _err = _run(_selection_driver(str(pyl), msys=True, safe=True), "", {})
    assert rc == 0
    assert out.strip() == str(pyl)


def test_non_windows_is_noop_even_with_pythonw_present(tmp_path):
    py = _fake_interp(tmp_path, "python.exe", "PYTHON_SELECTED")
    _fake_interp(tmp_path, "pythonw.exe", "PYTHONW_SELECTED")
    out, rc, _err = _run(_selection_driver(str(py), msys=False, safe=True), "", {})
    assert rc == 0
    assert out.strip() == str(py)


# ---------------------------------------------------------------------------
# stdout guard: a tty on fd 1 keeps python.exe (pythonw could null a tty and
# a console is already attached, so there is no flash to avoid).
# ---------------------------------------------------------------------------


def test_tty_stdout_keeps_python_exe(tmp_path):
    py = _fake_interp(tmp_path, "python.exe", "PYTHON_SELECTED")
    _fake_interp(tmp_path, "pythonw.exe", "PYTHONW_SELECTED")
    out, rc, _err = _run(
        _selection_driver(str(py), msys=True, safe=True), "", {}, use_pty=True
    )
    assert rc == 0
    # PTY echoes the candidate path; assert python.exe selected, pythonw not.
    assert str(py) in out
    assert "pythonw.exe" not in out


# ---------------------------------------------------------------------------
# Integration: the launcher actually exec's pythonw.exe and the hook protocol
# stdin -> stdout round-trip survives, exit code propagates, and the process
# is waited on (no early EOF).
# ---------------------------------------------------------------------------


def _discovered_driver(fake_py: Path) -> str:
    overrides = (
        "_is_msys_platform() { return 0; }\n"
        "_is_safe_prefix() { return 0; }\n"
        "_setup_interpreter_cache() { :; }\n"
        "_write_interpreter_cache() { :; }\n"
    )
    body = overrides + (
        'set +e\n'
        f'_exec_discovered_interpreter "{fake_py}" "" "$@"\n'
        "rc=$?\n"
        "set -e\n"
        'if [ "$rc" -ne 0 ]; then printf "EXEC_FAILED rc=%s\\n" "$rc" >&2; exit "$rc"; fi\n'
        'printf "EXEC_DID_NOT_REPLACE\\n" >&2\n'
        "exit 1\n"
    )
    return _defs() + "\n" + body


def _cached_driver(fake_py: Path, cache_file: Path) -> str:
    record = f"INTERP\t{fake_py}\n"
    cache_file.write_text(record, encoding="utf-8")
    overrides = (
        "_is_msys_platform() { return 0; }\n"
        "_is_safe_prefix() { return 0; }\n"
        "_setup_interpreter_cache() { :; }\n"
        "_write_interpreter_cache() { :; }\n"
    )
    body = overrides + (
        f'_PY_CACHE_FILE="{cache_file}"\n'
        'set +e\n'
        '_exec_cached_interpreter "$@"\n'
        "rc=$?\n"
        "set -e\n"
        'if [ "$rc" -ne 0 ]; then printf "EXEC_FAILED rc=%s\\n" "$rc" >&2; exit "$rc"; fi\n'
        'printf "EXEC_DID_NOT_REPLACE\\n" >&2\n'
        "exit 1\n"
    )
    return _defs() + "\n" + body


def test_discovered_path_execs_pythonw_and_round_trips_stdout(tmp_path):
    py = _fake_interp(tmp_path, "python.exe", "PYTHON_SELECTED")
    _fake_interp(tmp_path, "pythonw.exe", "PYTHONW_SELECTED")
    out, rc, err = _run(
        _discovered_driver(py),
        '{"session_id":"x"}',
        {"FAKE_PY_EXIT": "0"},
    )
    assert "EXEC_DID_NOT_REPLACE" not in err
    assert "EXEC_FAILED" not in err
    assert "PYTHONW_SELECTED" in out
    assert "PYTHON_SELECTED" not in out
    assert '{"session_id":"x"}' in out  # stdin survived the round-trip
    assert rc == 0


def test_discovered_path_propagates_nonzero_exit(tmp_path):
    py = _fake_interp(tmp_path, "python.exe", "PYTHON_SELECTED")
    _fake_interp(tmp_path, "pythonw.exe", "PYTHONW_SELECTED")
    out, rc, err = _run(
        _discovered_driver(py),
        '{"session_id":"x"}',
        {"FAKE_PY_EXIT": "7"},
    )
    assert "PYTHONW_SELECTED" in out
    assert rc == 7


def test_cached_path_swaps_to_pythonw_at_exec_time(tmp_path):
    """The regression: a cache record holding python.exe must NOT keep
    flashing forever. The swap happens at exec time on cache hits too."""
    py = _fake_interp(tmp_path, "python.exe", "PYTHON_SELECTED")
    _fake_interp(tmp_path, "pythonw.exe", "PYTHONW_SELECTED")
    cache = tmp_path / "interpreter.cache"
    out, rc, err = _run(
        _cached_driver(py, cache),
        '{"session_id":"x"}',
        {"FAKE_PY_EXIT": "0"},
    )
    assert "EXEC_DID_NOT_REPLACE" not in err
    assert "EXEC_FAILED" not in err
    assert "PYTHONW_SELECTED" in out
    assert "PYTHON_SELECTED" not in out
    assert '{"session_id":"x"}' in out
    assert rc == 0


def test_cache_record_still_names_python_exe_after_swap(tmp_path):
    """The cache key/record is unchanged by the swap: discovery writes
    python.exe, exec uses pythonw.exe. Proves the swap is exec-only."""
    py = _fake_interp(tmp_path, "python.exe", "PYTHON_SELECTED")
    _fake_interp(tmp_path, "pythonw.exe", "PYTHONW_SELECTED")
    cache = tmp_path / "interpreter.cache"
    # Use the REAL _write_interpreter_cache so we observe what gets stored.
    driver = (
        "_is_msys_platform() { return 0; }\n"
        "_is_safe_prefix() { return 0; }\n"
        "_setup_interpreter_cache() { :; }\n"
        f'_PY_CACHE_FILE="{cache}"\n'
        'set +e\n'
        f'_exec_discovered_interpreter "{py}" "" "$@"\n'
        "rc=$?\n"
        "set -e\n"
        'if [ "$rc" -ne 0 ]; then printf "EXEC_FAILED rc=%s\\n" "$rc" >&2; exit "$rc"; fi\n'
        'printf "EXEC_DID_NOT_REPLACE\\n" >&2\n'
        "exit 1\n"
    )
    out, rc, err = _run(_defs() + "\n" + driver, '{"session_id":"x"}', {"FAKE_PY_EXIT": "0"})
    assert "PYTHONW_SELECTED" in out
    assert rc == 0
    record = cache.read_text(encoding="utf-8")
    assert "python.exe" in record
    assert "pythonw.exe" not in record


def test_non_windows_execs_python_exe_unchanged(tmp_path):
    """Non-Windows behaviour is byte-for-byte unchanged: python.exe is used,
    pythonw.exe is never selected."""
    py = _fake_interp(tmp_path, "python.exe", "PYTHON_SELECTED")
    _fake_interp(tmp_path, "pythonw.exe", "PYTHONW_SELECTED")
    driver = (
        "_is_msys_platform() { return 1; }\n"
        "_is_safe_prefix() { return 0; }\n"
        "_setup_interpreter_cache() { :; }\n"
        "_write_interpreter_cache() { :; }\n"
        'set +e\n'
        f'_exec_discovered_interpreter "{py}" "" "$@"\n'
        "rc=$?\n"
        "set -e\n"
        'if [ "$rc" -ne 0 ]; then printf "EXEC_FAILED rc=%s\\n" "$rc" >&2; exit "$rc"; fi\n'
        'printf "EXEC_DID_NOT_REPLACE\\n" >&2\n'
        "exit 1\n"
    )
    out, rc, err = _run(_defs() + "\n" + driver, '{"session_id":"x"}', {"FAKE_PY_EXIT": "0"})
    assert "PYTHON_SELECTED" in out
    assert "PYTHONW_SELECTED" not in out
    assert rc == 0


# ---------------------------------------------------------------------------
# Broken-twin guard: a corrupt pythonw.exe that passes -f/-x/-s but exits
# nonzero on a liveness probe must NOT be swapped to. The launcher keeps
# python.exe so the hook still runs, instead of exec'ing a dead twin that
# bricks every hook (exit 127) and makes the fallback ladder unreachable.
# ---------------------------------------------------------------------------


def _broken_pythonw(tmp_path: Path) -> Path:
    """A pythonw.exe twin that is executable and non-empty (passes -f/-x/-s)
    but exits nonzero when actually run -- a stand-in for a corrupt/garbage
    install twin or a 0xC000-style Windows stub."""
    path = tmp_path / "pythonw.exe"
    path.write_text("#!/bin/sh\nexit 13\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_broken_pythonw_twin_not_selected(tmp_path):
    py = _fake_interp(tmp_path, "python.exe", "PYTHON_SELECTED")
    _broken_pythonw(tmp_path)
    out, rc, _err = _run(_selection_driver(str(py), msys=True, safe=True), "", {})
    assert rc == 0
    assert out.strip() == str(py)


def test_broken_pythonw_twin_keeps_python_exe_and_hook_runs(tmp_path):
    py = _fake_interp(tmp_path, "python.exe", "PYTHON_SELECTED")
    _broken_pythonw(tmp_path)
    out, rc, err = _run(
        _discovered_driver(py),
        '{"session_id":"x"}',
        {"FAKE_PY_EXIT": "0"},
    )
    assert "EXEC_DID_NOT_REPLACE" not in err
    assert "EXEC_FAILED" not in err
    assert "PYTHON_SELECTED" in out
    assert "PYTHONW" not in out
    assert '{"session_id":"x"}' in out  # the hook still ran, stdin survived
    assert rc == 0


def test_broken_pythonw_twin_keeps_python_exe_on_cache_hit(tmp_path):
    py = _fake_interp(tmp_path, "python.exe", "PYTHON_SELECTED")
    _broken_pythonw(tmp_path)
    cache = tmp_path / "interpreter.cache"
    out, rc, err = _run(
        _cached_driver(py, cache),
        '{"session_id":"x"}',
        {"FAKE_PY_EXIT": "0"},
    )
    assert "EXEC_DID_NOT_REPLACE" not in err
    assert "EXEC_FAILED" not in err
    assert "PYTHON_SELECTED" in out
    assert "PYTHONW" not in out
    assert '{"session_id":"x"}' in out
    assert rc == 0


def test_cache_record_naming_pythonw_exe_is_rejected(tmp_path):
    """A cache record that names /pythonw.exe is rejected outright: the
    swap is exec-only, so such a record is poisoned or a stale broken-twin
    artefact. Discovery re-runs instead of exec'ing a possibly-dead twin."""
    _fake_interp(tmp_path, "python.exe", "PYTHON_SELECTED")
    bad_pythonw = _broken_pythonw(tmp_path)
    cache = tmp_path / "interpreter.cache"
    # Poison the cache with a pythonw.exe path that passes -x/-s and is in a
    # safe prefix (overrides make _is_safe_prefix always pass), so the ONLY
    # thing rejecting it is the /pythonw.exe guard.
    cache.write_text(f"INTERP\t{bad_pythonw}\n", encoding="utf-8")
    out, rc, err = _run(
        _cached_driver(bad_pythonw, cache),
        '{"session_id":"x"}',
        {"FAKE_PY_EXIT": "0"},
    )
    # _exec_cached_interpreter returns 1 -> driver prints EXEC_FAILED and
    # exits 1. The dead twin is NOT exec'd (no PYTHONW output).
    assert "EXEC_FAILED" in err
    assert "PYTHONW" not in out
    assert "PYTHON_SELECTED" not in out
    assert rc != 0


# ---------------------------------------------------------------------------
# Documentation + mirror invariants.
# ---------------------------------------------------------------------------



def test_documents_py_launcher_only_installs_still_flash():
    source = LAUNCHER.read_text(encoding="utf-8")
    assert "py-launcher-only" in source
    assert "still flash" in source


def test_mirror_remains_byte_identical():
    assert LAUNCHER.read_bytes() == MIRROR.read_bytes()
    assert b"_maybe_swap_to_pythonw" in LAUNCHER.read_bytes()
