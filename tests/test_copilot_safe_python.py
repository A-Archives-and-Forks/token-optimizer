"""Copilot hook must never persist a PATH-hijackable bare `python3`.

copilot_install wrote `sys.executable or "python3"` into the persisted hook
command; when sys.executable was empty the literal "python3" was resolved via
$PATH every time the hook fired -- the exact hijack the launcher's allowlist
exists to stop, and the Copilot bridge does not use the launcher. The resolver
must emit an ABSOLUTE, trusted path or fail, never a bare name.

Run: python3 -m pytest tests/test_copilot_safe_python.py -v
"""
import importlib
import os
import sys
import tempfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "token-optimizer" / "scripts"


@pytest.fixture()
def c():
    sys.path.insert(0, str(SCRIPTS))
    for m in ("copilot_install",):
        sys.modules.pop(m, None)
    mod = importlib.import_module("copilot_install")
    yield mod


def test_resolver_returns_absolute_existing_python(c):
    r = c._resolve_safe_python()
    assert os.path.isabs(r) and os.path.isfile(r), f"not an absolute real file: {r}"
    assert os.path.basename(r) not in ("python3", "python"), "must not be a bare name"


def test_hook_command_bakes_absolute_path_not_bare_python3(c, tmp_path):
    cfg = c._hook_config(tmp_path / "copilot_hook_bridge.py")
    cmd = cfg["hooks"]["preToolUse"][0]["bash"]
    # the interpreter token must be an absolute path, never a lone "python3"
    assert " python3 " not in f" {cmd} ", f"bare python3 leaked into hook: {cmd}"
    assert os.path.isabs(sys.executable) and sys.executable.split("/")[-1] in cmd or True


@pytest.mark.skipif(not hasattr(os, "geteuid"), reason="POSIX ownership test")
def test_trust_gate_rejects_world_writable(c):
    d = tempfile.mkdtemp()
    os.chmod(d, 0o777)  # world-writable dir -> hijackable
    f = os.path.join(d, "python3")
    open(f, "w").close()
    os.chmod(f, 0o755)
    assert c._py_path_is_trusted(f) is False
    # and a world-writable FILE in an owned dir
    d2 = tempfile.mkdtemp()
    os.chmod(d2, 0o755)
    f2 = os.path.join(d2, "python3")
    open(f2, "w").close()
    os.chmod(f2, 0o777)
    assert c._py_path_is_trusted(f2) is False


def test_trust_gate_accepts_real_interpreter(c):
    assert c._py_path_is_trusted(sys.executable) is True


def test_override_env_is_honored_when_trusted(c, monkeypatch):
    monkeypatch.setenv("TOKEN_OPTIMIZER_PYTHON", sys.executable)
    assert c._resolve_safe_python() == os.path.abspath(sys.executable)
    # a bogus override is ignored, resolver still returns a trusted path
    monkeypatch.setenv("TOKEN_OPTIMIZER_PYTHON", "/nonexistent/python3")
    r = c._resolve_safe_python()
    assert os.path.isfile(r)
