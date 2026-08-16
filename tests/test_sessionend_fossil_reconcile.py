"""Issue #114 Layer 2: rename-aware settings.json fossil reconcile.

A collect && dashboard SessionEnd hook fossilized in ~/.claude/settings.json
is never rewritten by exact-identity dedup (measure.py:collect !=
measure.py:session-end-flush). The reconcile must rewrite or remove that
shape and leave unrelated hooks alone.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "token-optimizer" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

FOSSIL = (
    "python3 '/x/measure.py' collect --quiet && "
    "python3 '/x/measure.py' dashboard --quiet"
)
CURRENT = "python3 '/x/measure.py' session-end-flush --trigger end"
UNRELATED = "echo user-own-keep-me"


def _settings(session_end_hooks, stop_hooks=None):
    hooks = {"SessionEnd": [{"hooks": session_end_hooks}]}
    if stop_hooks is not None:
        hooks["Stop"] = [{"hooks": stop_hooks}]
    hooks["PreCompact"] = [{"hooks": [{"type": "command", "command": UNRELATED}]}]
    return {"hooks": hooks}


def _hook(cmd, **extra):
    entry = {"type": "command", "command": cmd}
    entry.update(extra)
    return entry


@pytest.fixture()
def measure_mod():
    import measure
    return measure


def test_reconcile_function_exists(measure_mod):
    assert hasattr(measure_mod, "_reconcile_sessionend_fossils"), (
        "missing _reconcile_sessionend_fossils — Layer 2 was never implemented"
    )


def test_rewrite_fossil_without_async(measure_mod, tmp_path, monkeypatch):
    settings_path = tmp_path / "settings.json"
    payload = _settings([_hook(FOSSIL)])
    settings_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(measure_mod, "SETTINGS_PATH", settings_path)
    monkeypatch.setattr(measure_mod, "CLAUDE_DIR", tmp_path)

    result = measure_mod._reconcile_sessionend_fossils()
    assert result.get("rewritten", 0) >= 1 or result.get("removed", 0) >= 1

    data = json.loads(settings_path.read_text(encoding="utf-8"))
    se_cmds = [
        h.get("command", "")
        for g in data["hooks"]["SessionEnd"]
        for h in g.get("hooks", [])
        if isinstance(h, dict)
    ]
    assert any("session-end-flush" in c and "--trigger" in c and "end" in c for c in se_cmds)
    assert not any("collect --quiet &&" in c for c in se_cmds)
    rewritten = next(h for g in data["hooks"]["SessionEnd"] for h in g["hooks"] if "session-end-flush" in h["command"])
    assert rewritten.get("async") is True
    assert data["hooks"]["PreCompact"][0]["hooks"][0]["command"] == UNRELATED


def test_rewrite_fossil_preserves_existing_async_and_timeout(measure_mod, tmp_path, monkeypatch):
    settings_path = tmp_path / "settings.json"
    payload = _settings([_hook(FOSSIL, **{"async": True, "timeout": 45})])
    settings_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(measure_mod, "SETTINGS_PATH", settings_path)
    monkeypatch.setattr(measure_mod, "CLAUDE_DIR", tmp_path)

    measure_mod._reconcile_sessionend_fossils()
    data = json.loads(settings_path.read_text(encoding="utf-8"))
    rewritten = next(
        h
        for g in data["hooks"]["SessionEnd"]
        for h in g["hooks"]
        if "session-end-flush" in h["command"]
    )
    assert rewritten.get("async") is True
    assert rewritten.get("timeout") == 45


def test_remove_fossil_when_current_shape_already_present(measure_mod, tmp_path, monkeypatch):
    settings_path = tmp_path / "settings.json"
    payload = _settings([_hook(FOSSIL), _hook(CURRENT, **{"async": True})])
    settings_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(measure_mod, "SETTINGS_PATH", settings_path)
    monkeypatch.setattr(measure_mod, "CLAUDE_DIR", tmp_path)

    result = measure_mod._reconcile_sessionend_fossils()
    assert result.get("removed", 0) >= 1

    data = json.loads(settings_path.read_text(encoding="utf-8"))
    se_cmds = [
        h.get("command", "")
        for g in data["hooks"]["SessionEnd"]
        for h in g.get("hooks", [])
        if isinstance(h, dict)
    ]
    assert se_cmds == [CURRENT]
    assert data["hooks"]["PreCompact"][0]["hooks"][0]["command"] == UNRELATED


def test_remove_legacy_stop_duplicate_when_plugin_provides_it(measure_mod, tmp_path, monkeypatch):
    settings_path = tmp_path / "settings.json"
    stop_fossil = "python3 '/x/measure.py' collect --quiet"
    stop_flush = "python3 '/x/measure.py' session-end-flush --trigger stop --quiet"
    payload = _settings(
        [_hook(CURRENT, **{"async": True})],
        stop_hooks=[_hook(stop_fossil), _hook(stop_flush), _hook(UNRELATED)],
    )
    settings_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(measure_mod, "SETTINGS_PATH", settings_path)
    monkeypatch.setattr(measure_mod, "CLAUDE_DIR", tmp_path)
    monkeypatch.setattr(measure_mod, "_is_plugin_installed", lambda: True)

    result = measure_mod._reconcile_sessionend_fossils()
    assert result.get("stop_removed", 0) >= 1 or result.get("removed", 0) >= 1

    data = json.loads(settings_path.read_text(encoding="utf-8"))
    stop_cmds = [
        h.get("command", "")
        for g in data["hooks"].get("Stop", [])
        for h in g.get("hooks", [])
        if isinstance(h, dict)
    ]
    assert UNRELATED in stop_cmds
    assert not any("collect --quiet" in c and "session-end-flush" not in c for c in stop_cmds)


def test_is_hook_current_rejects_collect_shape(measure_mod):
    fossil_settings = {"hooks": {"SessionEnd": [{"hooks": [_hook(FOSSIL)]}]}}
    current_settings = {"hooks": {"SessionEnd": [{"hooks": [_hook(CURRENT)]}]}}
    assert measure_mod._is_hook_current(fossil_settings) is False
    assert measure_mod._is_hook_current(current_settings) is True
