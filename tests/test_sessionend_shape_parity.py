"""Issue #114: every shipped SessionEnd command must use session-end-flush.

The collect --quiet && dashboard --quiet shape runs the heavy flush inline
with no budget and wedges Windows stop-hooks at 3/4. Both prior #114 fixes
only covered the session-end-flush argv path; this test would have caught
the still-shipped HOOK_COMMAND / hooks-starter.json fossil.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "token-optimizer" / "scripts"
MEASURE = SCRIPTS / "measure.py"

_FOSSIL_CHAIN = re.compile(r"collect\s+--quiet\s+&&")
_DASHBOARD_CHAIN = re.compile(r"dashboard\s+--quiet")


def _session_end_commands_from_hooks_json(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    cmds = []
    for group in data.get("hooks", {}).get("SessionEnd", []) or []:
        if not isinstance(group, dict):
            continue
        for hook in group.get("hooks", []) or []:
            if isinstance(hook, dict) and isinstance(hook.get("command"), str):
                cmds.append(hook["command"])
    return cmds


def _load_win32_hook_command() -> str:
    tree = ast.parse(MEASURE.read_text(encoding="utf-8"))
    node = None
    for candidate in tree.body:
        if not isinstance(candidate, ast.If):
            continue
        for stmt in ast.walk(candidate):
            if isinstance(stmt, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "HOOK_COMMAND" for t in stmt.targets
            ):
                node = candidate
                break
        if node is not None:
            break
    assert node is not None, "module-level HOOK_COMMAND assignment not found"
    namespace = {
        "sys": SimpleNamespace(
            platform="win32",
            executable="C:\\Python313\\python.exe",
        ),
        "shlex": __import__("shlex"),
        "Path": Path,
        "MEASURE_PY_PATH": Path("C:/Users/Test User/.claude/token-optimizer/scripts/measure.py"),
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(MEASURE), "exec"), namespace)
    return namespace["HOOK_COMMAND"]


def _load_posix_hook_command() -> str:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    import measure
    if sys.platform == "win32":
        pytest.skip("POSIX HOOK_COMMAND branch is the else of sys.platform == win32")
    return measure.HOOK_COMMAND


def _assert_current_shape(label: str, cmd: str) -> None:
    assert "session-end-flush" in cmd, f"{label} must invoke session-end-flush: {cmd!r}"
    assert _FOSSIL_CHAIN.search(cmd) is None, (
        f"{label} still ships the collect --quiet && fossil: {cmd!r}"
    )
    assert "dashboard --quiet" not in cmd, (
        f"{label} still ships a dashboard chain: {cmd!r}"
    )
    assert _DASHBOARD_CHAIN.search(cmd) is None or "session-end-flush" in cmd


def test_root_hooks_json_sessionend_is_flush_not_collect():
    cmds = _session_end_commands_from_hooks_json(REPO / "hooks" / "hooks.json")
    assert cmds, "root hooks/hooks.json must ship a SessionEnd hook"
    for i, cmd in enumerate(cmds):
        _assert_current_shape(f"root hooks.json SessionEnd[{i}]", cmd)


def test_codex_mirror_hooks_json_sessionend_is_flush_not_collect():
    cmds = _session_end_commands_from_hooks_json(
        REPO / "plugins" / "token-optimizer" / "hooks" / "hooks.json"
    )
    assert cmds, "Codex mirror must ship a SessionEnd hook"
    for i, cmd in enumerate(cmds):
        _assert_current_shape(f"codex-mirror hooks.json SessionEnd[{i}]", cmd)


def test_cowork_hooks_json_sessionend_is_flush_if_present():
    path = REPO / "cowork" / "token-optimizer" / "hooks" / "hooks.json"
    if not path.is_file():
        pytest.skip("cowork hooks.json not present")
    for i, cmd in enumerate(_session_end_commands_from_hooks_json(path)):
        _assert_current_shape(f"cowork hooks.json SessionEnd[{i}]", cmd)


def test_hook_command_win32_branch_is_flush_not_collect():
    cmd = _load_win32_hook_command()
    _assert_current_shape("win32 HOOK_COMMAND", cmd)
    assert "--trigger" in cmd and "end" in cmd
    assert cmd.endswith(">/dev/null 2>&1")


def test_hook_command_posix_branch_is_flush_not_collect():
    src = MEASURE.read_text(encoding="utf-8")
    assert "session-end-flush --trigger end" in src
    assert "collect --quiet &&" not in src
    if sys.platform != "win32":
        cmd = _load_posix_hook_command()
        _assert_current_shape("posix HOOK_COMMAND", cmd)


def test_hooks_starter_sessionend_is_flush_not_collect():
    cmds = _session_end_commands_from_hooks_json(
        REPO / "skills" / "token-optimizer" / "examples" / "hooks-starter.json"
    )
    # The starter uses the ``$MEASURE_PY`` install-time placeholder, not a
    # literal ``measure.py`` path, so key on the flush subcommand itself.
    flush_cmds = [c for c in cmds if "session-end-flush" in c and "compact-capture" not in c]
    assert flush_cmds, "hooks-starter.json must ship a SessionEnd flush command"
    for i, cmd in enumerate(flush_cmds):
        _assert_current_shape(f"hooks-starter.json SessionEnd[{i}]", cmd)


def test_codex_installer_does_not_emit_collect_dashboard_chain():
    sys.path.insert(0, str(SCRIPTS))
    import codex_install

    hooks = codex_install._managed_hooks()
    blob = json.dumps(hooks)
    assert "collect --quiet &&" not in blob
    assert "session-end-flush" in blob
    for event, groups in hooks.items():
        if event not in ("SessionEnd", "Stop"):
            continue
        for group in groups:
            for hook in group.get("hooks", []):
                cmd = hook.get("command", "")
                if "measure.py" in cmd and (
                    "session-end-flush" in cmd or "collect" in cmd or "dashboard" in cmd
                ):
                    assert "session-end-flush" in cmd, f"codex {event} still heavy: {cmd!r}"
                    assert "collect --quiet &&" not in cmd
