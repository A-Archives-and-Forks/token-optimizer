"""Issue #140: CLAUDE_PLUGIN_DATA identity leak + 2 robustness fixes."""

from __future__ import annotations

import importlib
import json
import os
import time
from pathlib import Path
from unittest import mock

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "token-optimizer" / "scripts"


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _load_plugin_env(monkeypatch: pytest.MonkeyPatch, data_base: Path):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    module = importlib.import_module("plugin_env")
    module._PLUGIN_DATA_BASE = data_base
    module._INSTALLED_PLUGINS = data_base.parent / "installed_plugins.json"
    # Use the real runtime_env to get the env var tuple, but override it
    # so tests control the env var list explicitly.
    module.resolve_plugin_data_dir.cache_clear()
    return module


# ---------------------------------------------------------------------------
# Fix (a): foreign CLAUDE_PLUGIN_DATA rejected; TOKEN_OPTIMIZER_PLUGIN_DATA
#          still honoured; hook + CLI resolve the same root.
# ---------------------------------------------------------------------------

def test_foreign_claude_plugin_data_rejected(monkeypatch, tmp_path):
    """A foreign plugin's CLAUDE_PLUGIN_DATA (not in installed_plugins.json)
    is rejected even though it sits under plugins/data/."""
    data_base = tmp_path / "data"
    our_identity = data_base / "token-optimizer-us"
    foreign = data_base / "token-optimizer-foreign"
    our_identity.mkdir(parents=True)
    foreign.mkdir(parents=True)

    # Write installed_plugins.json listing only OUR identity.
    (data_base.parent / "installed_plugins.json").write_text(
        json.dumps({"plugins": {"token-optimizer@us": []}}), encoding="utf-8"
    )

    # Set CLAUDE_PLUGIN_DATA to the FOREIGN identity — should be REJECTED.
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(foreign))
    monkeypatch.delenv("TOKEN_OPTIMIZER_PLUGIN_DATA", raising=False)

    module = _load_plugin_env(monkeypatch, data_base)
    # Override env vars tuple so we test exactly these two.
    module._PLUGIN_DATA_ENV_VARS = ("CLAUDE_PLUGIN_DATA", "TOKEN_OPTIMIZER_PLUGIN_DATA")

    result = module.resolve_plugin_data_dir()
    # Foreign CLAUDE_PLUGIN_DATA is NOT a registered identity → skipped.
    # TOKEN_OPTIMIZER_PLUGIN_DATA is not set → fall through to glob.
    # Glob picks the only token-optimizer-* dir (our_identity is the only
    # non-registered one; foreign is not registered either, but lexical sort
    # picks "token-optimizer-foreign" first, and since both are not registered,
    # the first sorted one wins in the glob fallback).
    # Actually: the glob fallback doesn't check registration. Let me re-think.
    # After env vars are exhausted, _registered_plugin_data_dirs() returns
    # our_identity (the one in installed_plugins.json). So the result should
    # be our_identity.
    assert result == our_identity, (
        f"Expected {our_identity}, got {result} — foreign CLAUDE_PLUGIN_DATA "
        f"was not rejected"
    )


def test_dedicated_var_honored_when_claude_rejected(monkeypatch, tmp_path):
    """When CLAUDE_PLUGIN_DATA is foreign, TOKEN_OPTIMIZER_PLUGIN_DATA is still
    honoured unconditionally."""
    data_base = tmp_path / "data"
    dedicated = data_base / "token-optimizer-dedicated"
    foreign = data_base / "token-optimizer-foreign"
    dedicated.mkdir(parents=True)
    foreign.mkdir(parents=True)

    (data_base.parent / "installed_plugins.json").write_text(
        json.dumps({"plugins": {"token-optimizer@dedicated": []}}), encoding="utf-8"
    )

    # CLAUDE_PLUGIN_DATA = foreign (rejected), TOKEN_OPTIMIZER_PLUGIN_DATA = dedicated (honoured).
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(foreign))
    monkeypatch.setenv("TOKEN_OPTIMIZER_PLUGIN_DATA", str(dedicated))

    module = _load_plugin_env(monkeypatch, data_base)
    module._PLUGIN_DATA_ENV_VARS = ("CLAUDE_PLUGIN_DATA", "TOKEN_OPTIMIZER_PLUGIN_DATA")

    result = module.resolve_plugin_data_dir()
    assert result == dedicated, (
        f"Expected dedicated {dedicated}, got {result} — "
        f"TOKEN_OPTIMIZER_PLUGIN_DATA was not honoured"
    )


def test_legitimate_claude_plugin_data_still_accepted(monkeypatch, tmp_path):
    """When CLAUDE_PLUGIN_DATA points to a REGISTERED identity, it IS accepted."""
    data_base = tmp_path / "data"
    registered = data_base / "token-optimizer-registered"
    registered.mkdir(parents=True)

    (data_base.parent / "installed_plugins.json").write_text(
        json.dumps({"plugins": {"token-optimizer@registered": []}}), encoding="utf-8"
    )

    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(registered))
    monkeypatch.delenv("TOKEN_OPTIMIZER_PLUGIN_DATA", raising=False)

    module = _load_plugin_env(monkeypatch, data_base)
    module._PLUGIN_DATA_ENV_VARS = ("CLAUDE_PLUGIN_DATA", "TOKEN_OPTIMIZER_PLUGIN_DATA")

    result = module.resolve_plugin_data_dir()
    assert result == registered, (
        f"Legitimate registered CLAUDE_PLUGIN_DATA should be accepted, got {result}"
    )


def test_hook_and_cli_resolve_same_root(monkeypatch, tmp_path):
    """Hook env (only CLAUDE_PLUGIN_DATA) and CLI env (no env vars) both
    resolve to the same registered identity via different paths."""
    data_base = tmp_path / "data"
    identity = data_base / "token-optimizer-main"
    identity.mkdir(parents=True)

    (data_base.parent / "installed_plugins.json").write_text(
        json.dumps({"plugins": {"token-optimizer@main": []}}), encoding="utf-8"
    )

    # Simulate hook: CLAUDE_PLUGIN_DATA set (no TOKEN_OPTIMIZER_PLUGIN_DATA).
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(identity))
    monkeypatch.delenv("TOKEN_OPTIMIZER_PLUGIN_DATA", raising=False)

    module = _load_plugin_env(monkeypatch, data_base)
    module._PLUGIN_DATA_ENV_VARS = ("CLAUDE_PLUGIN_DATA", "TOKEN_OPTIMIZER_PLUGIN_DATA")
    hook_result = module.resolve_plugin_data_dir()

    # Simulate CLI: no env vars set. Should fall through to
    # _registered_plugin_data_dirs().
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    module.resolve_plugin_data_dir.cache_clear()
    cli_result = module.resolve_plugin_data_dir()

    assert hook_result == identity, f"Hook resolved to {hook_result}"
    assert cli_result == identity, f"CLI resolved to {cli_result}"
    assert hook_result == cli_result, (
        f"Hook and CLI diverged: hook={hook_result}, cli={cli_result}"
    )


# ---------------------------------------------------------------------------
# Fix (b): refetch_guard denies only when target is renderable; scans older
#          entries on a bad match.
# ---------------------------------------------------------------------------

def test_refetch_guard_renderable_check(monkeypatch, tmp_path):
    """_lookup_archived only returns a hit when the archive entry file is
    actually renderable (exists, readable JSON, non-empty response)."""
    monkeypatch.syspath_prepend(str(SCRIPTS))
    rg = importlib.import_module("refetch_guard")

    snapshot_dir = tmp_path / "snapshots"
    archive_dir = snapshot_dir / "tool-archive" / "session-1"
    archive_dir.mkdir(parents=True)

    monkeypatch.setattr(rg, "resolve_snapshot_dir", lambda: snapshot_dir)

    # Write a manifest with two entries: newest (bad file) then older (good file).
    manifest = archive_dir / "manifest.jsonl"
    manifest.write_text(
        json.dumps({
            "tool_name": "mcp__test__query",
            "args_hash": "abc123",
            "tool_use_id": "bad-entry",
            "tokens_est": 500,
        }) + "\n" +
        json.dumps({
            "tool_name": "mcp__test__query",
            "args_hash": "abc123",
            "tool_use_id": "good-entry",
            "tokens_est": 300,
        }) + "\n",
        encoding="utf-8",
    )

    # Write the good entry file.
    good_entry = archive_dir / "good-entry.json"
    good_entry.write_text(json.dumps({"response": "valid content"}), encoding="utf-8")

    # The bad entry file does NOT exist (or is empty/corrupt).
    # This tests that _lookup_archived skips the bad newest entry and returns
    # the older good one.

    tool_use_id, tokens = rg._lookup_archived(
        "session-1", "mcp__test__query", "abc123"
    )
    assert tool_use_id == "good-entry", (
        f"Expected good-entry, got {tool_use_id} — bad entry was not skipped"
    )
    assert tokens == 300


def test_refetch_guard_empty_response_is_not_renderable(monkeypatch, tmp_path):
    """An entry with an empty response field is NOT renderable."""
    monkeypatch.syspath_prepend(str(SCRIPTS))
    rg = importlib.import_module("refetch_guard")

    snapshot_dir = tmp_path / "snapshots"
    archive_dir = snapshot_dir / "tool-archive" / "session-1"
    archive_dir.mkdir(parents=True)

    monkeypatch.setattr(rg, "resolve_snapshot_dir", lambda: snapshot_dir)

    # Write manifest with one entry.
    manifest = archive_dir / "manifest.jsonl"
    manifest.write_text(
        json.dumps({
            "tool_name": "mcp__test__query",
            "args_hash": "abc123",
            "tool_use_id": "empty-response",
            "tokens_est": 500,
        }) + "\n",
        encoding="utf-8",
    )

    # Write entry with empty response.
    entry = archive_dir / "empty-response.json"
    entry.write_text(json.dumps({"response": ""}), encoding="utf-8")

    tool_use_id, tokens = rg._lookup_archived(
        "session-1", "mcp__test__query", "abc123"
    )
    assert tool_use_id is None, (
        f"Empty response should not be renderable, got {tool_use_id}"
    )


def test_refetch_guard_corrupt_json_not_renderable(monkeypatch, tmp_path):
    """An entry with corrupt JSON is NOT renderable."""
    monkeypatch.syspath_prepend(str(SCRIPTS))
    rg = importlib.import_module("refetch_guard")

    snapshot_dir = tmp_path / "snapshots"
    archive_dir = snapshot_dir / "tool-archive" / "session-1"
    archive_dir.mkdir(parents=True)

    monkeypatch.setattr(rg, "resolve_snapshot_dir", lambda: snapshot_dir)

    manifest = archive_dir / "manifest.jsonl"
    manifest.write_text(
        json.dumps({
            "tool_name": "mcp__test__query",
            "args_hash": "abc123",
            "tool_use_id": "corrupt",
            "tokens_est": 500,
        }) + "\n",
        encoding="utf-8",
    )

    # Write corrupt JSON.
    (archive_dir / "corrupt.json").write_text("not json{{{", encoding="utf-8")

    tool_use_id, tokens = rg._lookup_archived(
        "session-1", "mcp__test__query", "abc123"
    )
    assert tool_use_id is None, (
        f"Corrupt JSON should not be renderable, got {tool_use_id}"
    )


# ---------------------------------------------------------------------------
# Fix (c): archive_result serves full result when entry was pruned post-write.
# ---------------------------------------------------------------------------

def test_archive_result_post_prune_serves_full_result(monkeypatch, tmp_path):
    """When _cleanup_archives_if_due deletes the just-written entry, the hook
    does NOT emit a pointer — it returns without printing, so the original
    tool output reaches context."""
    monkeypatch.syspath_prepend(str(SCRIPTS))
    ar = importlib.import_module("archive_result")

    snapshot_dir = tmp_path / "snapshots"
    snapshot_dir.mkdir(parents=True)
    monkeypatch.setenv("TOKEN_OPTIMIZER_SNAPSHOT_DIR", str(snapshot_dir))
    monkeypatch.setattr(ar, "SNAPSHOT_DIR", snapshot_dir)
    monkeypatch.setattr(ar, "TRENDS_DB", snapshot_dir / "trends.db")

    # Force retention to 1 byte so the ceiling gate (max_bytes > 0) triggers.
    # Setting it to 0 would disable the retention ceiling entirely.
    monkeypatch.setenv("TOKEN_OPTIMIZER_ARCHIVE_RETENTION_MAX_BYTES", "1")

    # Disable the rate-limit marker so cleanup always runs.
    cleanup_marker = snapshot_dir / ".archive-cleanup.last"
    cleanup_marker.parent.mkdir(parents=True, exist_ok=True)

    # Mock stdin with a large MCP result.
    hook_input = {
        "tool_name": "mcp__test__big_query",
        "tool_use_id": "test-entry-1",
        "tool_response": {"text": "x" * 5000},
        "session_id": "test-session",
        "tool_input": {"query": "hello"},
    }

    # Mock read_stdin_hook_input to return our input.
    monkeypatch.setattr(
        ar, "read_stdin_hook_input", lambda _max_bytes: hook_input
    )

    # Disable SessionStore (no DB in test).
    monkeypatch.setattr(ar, "SessionStore", None)

    # Ensure the cleanup rate-limit marker is old so cleanup runs.
    cleanup_marker.touch()
    os.utime(cleanup_marker, (0, 0))

    # Capture stdout.
    import sys
    from io import StringIO

    captured = StringIO()
    monkeypatch.setattr(sys, "stdout", captured)

    # Run archive_result — it should write the entry, run cleanup (which
    # deletes it because max_bytes=0), detect the entry is gone, and return
    # without printing an updatedMCPToolOutput.
    ar.archive_result(quiet=True)

    output = captured.getvalue()
    # Should NOT emit a replacement pointer since the entry was pruned.
    assert "updatedMCPToolOutput" not in output, (
        f"Pointer emitted after entry was pruned: {output[:200]}"
    )
    # Should NOT emit the expand instruction.
    assert "expand" not in output.lower(), (
        f"expand instruction emitted after entry was pruned: {output[:200]}"
    )


def test_archive_result_emits_pointer_when_entry_survives(monkeypatch, tmp_path):
    """Normal path: entry survives cleanup, pointer IS emitted."""
    monkeypatch.syspath_prepend(str(SCRIPTS))
    ar = importlib.import_module("archive_result")

    snapshot_dir = tmp_path / "snapshots"
    snapshot_dir.mkdir(parents=True)
    monkeypatch.setenv("TOKEN_OPTIMIZER_SNAPSHOT_DIR", str(snapshot_dir))
    monkeypatch.setattr(ar, "SNAPSHOT_DIR", snapshot_dir)
    monkeypatch.setattr(ar, "TRENDS_DB", snapshot_dir / "trends.db")

    # Normal retention (100MB, won't prune anything).
    monkeypatch.setenv("TOKEN_OPTIMIZER_ARCHIVE_RETENTION_MAX_BYTES", "104857600")

    hook_input = {
        "tool_name": "mcp__test__query",
        "tool_use_id": "test-entry-2",
        "tool_response": {"text": "x" * 5000},
        "session_id": "test-session",
        "tool_input": {"query": "hello"},
    }

    monkeypatch.setattr(
        ar, "read_stdin_hook_input", lambda _max_bytes: hook_input
    )
    monkeypatch.setattr(ar, "SessionStore", None)

    # Ensure cleanup runs.
    cleanup_marker = snapshot_dir / ".archive-cleanup.last"
    cleanup_marker.parent.mkdir(parents=True, exist_ok=True)
    cleanup_marker.touch()
    os.utime(cleanup_marker, (0, 0))

    import sys
    from io import StringIO

    captured = StringIO()
    monkeypatch.setattr(sys, "stdout", captured)

    ar.archive_result(quiet=True)

    output = captured.getvalue()
    assert "updatedMCPToolOutput" in output, (
        f"Pointer NOT emitted when entry survived: {output[:200]}"
    )
    assert "expand" in output.lower(), (
        f"expand instruction missing: {output[:200]}"
    )
