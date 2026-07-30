#!/usr/bin/env python3
"""Suite-wide guard: no test may run a host-mutating verb against the real HOME.

Incident 2026-07-30. ``tests/test_cleanup_write_and_confirm.py`` spawned
``measure.py cleanup --confirm`` with only ``TOKEN_OPTIMIZER_SNAPSHOT_DIR``
pinned. That env var controls the DATA dir only; ``settings.json`` resolves
through ``CLAUDE_DIR = claude_home()``, which is independent of it. The
subprocess therefore operated on the developer's real ``~/.claude`` and
removed a live ``statusLine``. Running the full suite re-fired it every time.

The class of bug: a destructive CLI verb invoked as a subprocess whose config
root was never pinned. This test makes that class fail at collection time
instead of on someone's machine.

Rule enforced: any test file that spawns a subprocess with a destructive verb
(``cleanup``, ``setup-*``, ``*-uninstall``, ``--uninstall``) must also set
``CLAUDE_CONFIG_DIR`` somewhere in the file. ``claude_home()`` honors it for
any absolute, existing, non-symlink dir, so pinning it redirects settings.json
into the fixture.

This is a lint, not a proof -- a file can pin the var and still misuse it. The
per-file tests own the proof (see ``test_confirm_proceeds``, which asserts the
FIXTURE settings.json is what changed). This guard exists so a NEW destructive
test cannot land with no isolation at all.

Run: python3 -m pytest tests/test_host_safety_guard.py -q
"""

from __future__ import annotations

import re
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent

# Verbs that write host state: settings.json, launchd/systemd/schtasks, or the
# plugin manifests. Matched as CLI arguments, not as prose in a docstring.
_DESTRUCTIVE = re.compile(
    r"""["'](?:cleanup|setup-[a-z-]+|[a-z-]+-uninstall)["']|["']--uninstall["']"""
)
_SPAWNS = re.compile(r"subprocess\.(?:run|Popen|check_output|call)")
_PINS_CONFIG = "CLAUDE_CONFIG_DIR"

# This guard file itself names the verbs in prose/regex; exempt by name.
_EXEMPT = {"test_host_safety_guard.py"}


def _offenders() -> list[str]:
    bad = []
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        if path.name in _EXEMPT:
            continue
        try:
            src = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if not _SPAWNS.search(src):
            continue
        if not _DESTRUCTIVE.search(src):
            continue
        if _PINS_CONFIG not in src:
            bad.append(path.name)
    return bad


def test_no_destructive_subprocess_without_pinned_config_dir():
    offenders = _offenders()
    assert not offenders, (
        "These test files spawn a host-mutating measure.py verb without ever "
        "pinning CLAUDE_CONFIG_DIR, so the subprocess resolves settings.json "
        "from the REAL ~/.claude and can damage a developer's machine "
        f"(incident 2026-07-30): {offenders}\n"
        "Fix: set CLAUDE_CONFIG_DIR (absolute, existing, non-symlink) plus HOME "
        "to a tmp_path fixture in the subprocess env, and assert the FIXTURE "
        "file is the one that changed."
    )


def test_guard_detects_a_planted_offender(tmp_path, monkeypatch):
    """Negative test: the guard must actually catch an unisolated file."""
    planted = tmp_path / "test_planted_offender.py"
    planted.write_text(
        "import subprocess, sys\n"
        "def test_x():\n"
        "    subprocess.run([sys.executable, 'measure.py', 'cleanup', '--confirm'])\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(__import__("sys").modules[__name__], "TESTS_DIR", tmp_path)

    assert _offenders() == ["test_planted_offender.py"]


def test_guard_accepts_a_pinned_file(tmp_path, monkeypatch):
    ok = tmp_path / "test_pinned.py"
    ok.write_text(
        "import subprocess, sys\n"
        "def test_x(tmp_path):\n"
        "    env = {'CLAUDE_CONFIG_DIR': str(tmp_path)}\n"
        "    subprocess.run([sys.executable, 'measure.py', 'cleanup', '--confirm'], env=env)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(__import__("sys").modules[__name__], "TESTS_DIR", tmp_path)

    assert _offenders() == []
