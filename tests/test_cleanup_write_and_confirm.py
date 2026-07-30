#!/usr/bin/env python3
"""#106 F3 P2: symlink/mode preservation on settings writes + confirm gate.

P2-7 os.replace onto SETTINGS_PATH detached a dotfiles-managed symlink
      (settings.json became a regular file, so the user's repo silently
      stopped tracking it) and dropped the mode from 0644 to mkstemp's 0600.
P2-8 `dry = "--dry-run" in args` meant a typo ("--dryrun", "--dry_run")
      silently performed the REAL destructive run.

Run: python3 -m pytest tests/test_cleanup_write_and_confirm.py -q
"""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "token-optimizer" / "scripts"
MEASURE = SCRIPTS / "measure.py"


@pytest.fixture()
def measure(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKEN_OPTIMIZER_SNAPSHOT_DIR", str(tmp_path / "data"))
    monkeypatch.syspath_prepend(str(SCRIPTS))
    sys.modules.pop("measure", None)
    spec = importlib.util.spec_from_file_location("measure", MEASURE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["measure"] = mod
    spec.loader.exec_module(mod)
    yield mod
    sys.modules.pop("measure", None)


# ---------------------------------------------------------------------------
# P2-7: symlinked settings.json + mode
# ---------------------------------------------------------------------------

def test_symlinked_settings_stays_a_symlink(measure, tmp_path, monkeypatch):
    real_dir = tmp_path / "dotfiles"
    real_dir.mkdir()
    real = real_dir / "settings.json"
    real.write_text(json.dumps({"model": "opus"}), encoding="utf-8")
    real.chmod(0o644)

    home = tmp_path / "claude"
    home.mkdir()
    link = home / "settings.json"
    link.symlink_to(real)
    monkeypatch.setattr(measure, "SETTINGS_PATH", link)

    assert measure._write_settings_atomic({"model": "sonnet"}) is True

    assert link.is_symlink(), "the dotfiles symlink was replaced by a regular file"
    assert link.resolve() == real.resolve()
    assert json.loads(real.read_text(encoding="utf-8")) == {"model": "sonnet"}, (
        "the write did not reach the symlink target"
    )


def test_file_mode_is_preserved(measure, tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    settings.write_text("{}", encoding="utf-8")
    settings.chmod(0o644)
    monkeypatch.setattr(measure, "SETTINGS_PATH", settings)

    measure._write_settings_atomic({"a": 1})

    mode = stat.S_IMODE(os.stat(settings).st_mode)
    assert mode == 0o644, f"mode changed to {oct(mode)} (mkstemp default leaks 0600)"


def test_missing_file_still_written(measure, tmp_path, monkeypatch):
    """No destination to copy a mode from -- must still write."""
    settings = tmp_path / "settings.json"
    monkeypatch.setattr(measure, "SETTINGS_PATH", settings)

    assert measure._write_settings_atomic({"a": 1}) is True
    assert json.loads(settings.read_text(encoding="utf-8")) == {"a": 1}


# ---------------------------------------------------------------------------
# P2-8: confirm gate on the destructive path
# ---------------------------------------------------------------------------

def _run_cleanup(tmp_path, *flags):
    env = dict(os.environ)
    env["TOKEN_OPTIMIZER_SNAPSHOT_DIR"] = str(tmp_path / "data")
    return subprocess.run(
        [sys.executable, str(MEASURE), "cleanup", *flags],
        capture_output=True, text=True, env=env, timeout=120,
    )


def test_bare_cleanup_refuses_without_confirm(tmp_path):
    p = _run_cleanup(tmp_path)
    assert p.returncode == 1
    assert "Nothing was changed" in p.stdout
    assert "--confirm" in p.stdout
    assert "PRESERVED" in p.stdout, "must state data is preserved"


def test_typo_dry_run_flag_is_rejected_not_executed(tmp_path):
    """The regression: a mistyped --dry-run used to run for real."""
    for typo in ("--dryrun", "--dry_run", "--dry-runn"):
        p = _run_cleanup(tmp_path, typo)
        assert p.returncode == 2, f"{typo} was not rejected"
        assert "Unknown flag" in p.stdout
        assert "Nothing was changed" in p.stdout


def test_dry_run_needs_no_confirm(tmp_path):
    p = _run_cleanup(tmp_path, "--dry-run")
    assert p.returncode == 0, p.stderr
    assert "Unknown flag" not in p.stdout


def test_confirm_proceeds(tmp_path):
    p = _run_cleanup(tmp_path, "--confirm")
    assert p.returncode == 0, p.stderr
    assert "Nothing was changed" not in p.stdout


def test_known_flags_combine(tmp_path):
    p = _run_cleanup(tmp_path, "--dry-run", "--this-install-only")
    assert p.returncode == 0, p.stderr
    assert "Unknown flag" not in p.stdout
