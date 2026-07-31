#!/usr/bin/env python3
"""Regression tests for CLAUDE_CONFIG_DIR resolution in runtime_env.claude_home().

Claude Code lets CLAUDE_CONFIG_DIR relocate its config (projects/, settings.json,
the trends DB, the dashboard) anywhere -- including OUTSIDE $HOME (containers, CI
runners, relocated volumes). claude_home() must honor a valid override so those
users are not silently pinned to a stale ~/.claude, while still rejecting unsafe
inputs (symlinks, relative paths).

Run directly:  python3 tests/test_claude_config_dir.py
Exits non-zero on first failure.
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "token-optimizer" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import runtime_env  # noqa: E402

ENV = "CLAUDE_CONFIG_DIR"


def _claude_home_with(value):
    """Call claude_home() with CLAUDE_CONFIG_DIR set to `value` (None = unset)."""
    saved = os.environ.get(ENV)
    try:
        if value is None:
            os.environ.pop(ENV, None)
        else:
            os.environ[ENV] = value
        return runtime_env.claude_home()
    finally:
        if saved is None:
            os.environ.pop(ENV, None)
        else:
            os.environ[ENV] = saved


def test_unset_falls_back_to_default():
    assert _claude_home_with(None) == Path.home() / ".claude"


def test_honors_valid_out_of_home_dir(tmp_path, monkeypatch):
    # A real absolute dir that is NOT under $HOME (the case the old under-$HOME
    # confinement broke). Fake HOME so the relationship remains deterministic
    # even on Windows, where the system temp directory is usually under HOME.
    fake_home = tmp_path / "home"
    override = tmp_path / "outside-home"
    fake_home.mkdir()
    override.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    resolved = _claude_home_with(str(override))
    assert resolved == override.resolve(), f"expected {override}, got {resolved}"
    # And it must NOT have silently fallen back to ~/.claude.
    assert resolved != Path.home() / ".claude"


def test_honors_valid_in_home_dir(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    config_dir = fake_home / "relocated-claude"
    config_dir.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    assert _claude_home_with(str(config_dir)) == config_dir.resolve()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only symlink semantics")
def test_rejects_symlink():
    with tempfile.TemporaryDirectory() as d:
        real = Path(d) / "real"
        real.mkdir()
        link = Path(d) / "link"
        link.symlink_to(real)
        # A symlinked CLAUDE_CONFIG_DIR is rejected -> fall back to default.
        assert _claude_home_with(str(link)) == Path.home() / ".claude"


def test_rejects_relative_path():
    assert _claude_home_with("relative/not/absolute") == Path.home() / ".claude"


def test_rejects_nonexistent_dir():
    missing = Path(tempfile.gettempdir()) / "does-not-exist-token-optimizer-xyz"
    assert _claude_home_with(str(missing)) == Path.home() / ".claude"


def test_empty_string_falls_back():
    assert _claude_home_with("   ") == Path.home() / ".claude"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
