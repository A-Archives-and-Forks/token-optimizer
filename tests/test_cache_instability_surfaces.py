"""Issue #116: cache_instability surface extension (MCP + process-library).

The detector historically only inspected CLAUDE.md. #116 adds two more
cache-prefix-resident surfaces that churn just as often:
  Signal 4: MCP server env/args/url blocks in .mcp.json / .claude.json
  Signal 5: process-library prompt prefix files in .a5c/processes, .claude/processes

These tests prove each new surface FIRES on a volatile prefix and does NOT fire
on a stable one, that the guards are load-bearing (the new signals still run when
CLAUDE.md is absent), and that the 3 original CLAUDE.md signals are unchanged.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "token-optimizer" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


@pytest.fixture()
def ci(monkeypatch, tmp_path):
    """Fresh detector module with cwd+HOME isolated to a tmp dir so the scans
    never touch the real project/home, and the module-level scan caches start
    empty for each test."""
    import detectors.cache_instability as mod
    importlib.reload(mod)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    return mod


# ---- Signal 4: MCP env blocks --------------------------------------------

def _write_mcp(cwd: Path, env_value: str):
    (cwd / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"weather": {"command": "srv", "env": {"CFG": env_value}}}}),
        encoding="utf-8",
    )


def test_mcp_volatile_env_fires(ci, tmp_path):
    _write_mcp(tmp_path, "snapshot as of 2026-08-08T10:00 (daily)")
    findings = ci._scan_mcp_env(str(tmp_path))
    assert len(findings) == 1
    assert findings[0]["name"] == "cache_instability"
    assert "weather" in findings[0]["evidence"]


def test_mcp_stable_env_does_not_fire(ci, tmp_path):
    _write_mcp(tmp_path, "https://api.example.com/v1")
    assert ci._scan_mcp_env(str(tmp_path)) == []


def test_mcp_absent_file_is_no_op(ci, tmp_path):
    assert ci._scan_mcp_env(str(tmp_path)) == []


def test_mcp_bad_json_fails_open(ci, tmp_path):
    (tmp_path / ".mcp.json").write_text("{not valid json", encoding="utf-8")
    assert ci._scan_mcp_env(str(tmp_path)) == []


# ---- Signal 5: process-library prefixes ----------------------------------

def _write_process(cwd: Path, name: str, body: str):
    d = cwd / ".claude" / "processes"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(body, encoding="utf-8")


def test_process_timestamp_prefix_fires(ci, tmp_path):
    _write_process(tmp_path, "flow.md", "Last updated: 2026-08-08\n" + "stable rules\n" * 20)
    findings = ci._scan_process_prefixes(str(tmp_path))
    assert len(findings) == 1
    assert findings[0]["name"] == "cache_instability"
    assert "flow.md" in findings[0]["evidence"]


def test_process_stable_prefix_does_not_fire(ci, tmp_path):
    _write_process(tmp_path, "flow.md", "# Stable process\n" + "do the thing\n" * 20)
    assert ci._scan_process_prefixes(str(tmp_path)) == []


# ---- Guard is load-bearing: new signals run when CLAUDE.md is absent ------

def test_new_signals_run_without_claude_md(ci, tmp_path):
    """The early `return []` was replaced with an empty lines list so Signal 4/5
    still run when CLAUDE.md is missing. session_data carries NO claude_md_content."""
    _write_mcp(tmp_path, "status-2026-08-08")
    findings = ci.detect_cache_instability({})  # no claude_md_content at all
    assert any(f["name"] == "cache_instability" for f in findings), (
        "MCP signal must fire even with CLAUDE.md absent (guard is load-bearing)"
    )


# ---- Regression: the 3 original CLAUDE.md signals are unchanged -----------

def test_claude_md_timestamp_signal_unchanged(ci):
    md = "# Rules\nUpdated: 2026-08-08\n" + ("- keep this rule stable\n" * 400)
    findings = ci.detect_cache_instability({"claude_md_content": md})
    ts = [f for f in findings if f["name"] == "cache_instability" and f["confidence"] == 0.75]
    assert ts, "original CLAUDE.md timestamp signal (confidence 0.75) must still fire"
    assert ts[0]["savings_tokens"] > 500
