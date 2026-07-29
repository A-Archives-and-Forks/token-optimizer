#!/usr/bin/env python3
"""Regression tests for #101: PreCompact leaves the LIVE session's SQLite
read-cache intact after compaction.

Before the fix, ``hooks.json`` PreCompact ran ``read_cache.py --clear --quiet``
with no ``--session``, so ``main()`` defaulted to ``session_id="all"`` and
``handle_clear`` took the ``elif session_id=="all"`` branch -- which only unlinks
legacy ``*.json``/``*.tmp`` and prunes stores >48h. It NEVER called
``store.clear_file_entries()`` for the current session, so post-compaction an
unchanged file's ``file_reads`` row survived and a re-Read was judged redundant
even though compaction dropped it from context.

The fix adds a NEW ``--clear-compacted`` flag (wired to SessionStart matcher
``compact``) that reads ``session_id`` from the stdin hook input and calls
``store.clear_file_entries()`` ONLY -- never ``handle_clear``'s session-scoped
branch, which would also unlink the decisions telemetry log and legacy cache
json on every compaction.

Run: python3 -m pytest tests/test_read_cache_clear_compacted.py -v
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "token-optimizer" / "scripts"
READ_CACHE = SCRIPTS / "read_cache.py"

# A real Claude-style session id (8-4-4-4-12 hex) so the savings/uuid path is
# exercised identically to production, not the short-id unjoinable fallback.
SESSION_S = "11111111-2222-3333-4444-555555555555"
SESSION_S2 = "22222222-3333-4444-5555-666666666666"


def _seed_file_entry(snapshot_dir: Path, session_id: str, file_path: str,
                     mtime_ns: int, size_bytes: int) -> None:
    """Seed a file_reads row directly via SessionStore (bypassing the hook)."""
    sys.path.insert(0, str(SCRIPTS))
    try:
        from session_store import SessionStore  # noqa: WPS433 (test seed)
    finally:
        if str(SCRIPTS) in sys.path:
            sys.path.remove(str(SCRIPTS))
    store = SessionStore(session_id, snapshot_dir=snapshot_dir)
    try:
        store.upsert_file_entry(
            file_path,
            {
                "mtime_ns": mtime_ns,
                "size_bytes": size_bytes,
                "ranges_seen": [[0, 0]],  # whole file already seen
                "tokens_est": max(1, size_bytes // 4),
                "read_count": 1,
                "last_access": time.time(),
            },
        )
    finally:
        store.close()


def _file_entries(snapshot_dir: Path, session_id: str) -> dict:
    sys.path.insert(0, str(SCRIPTS))
    try:
        from session_store import SessionStore  # noqa: WPS433
    finally:
        if str(SCRIPTS) in sys.path:
            sys.path.remove(str(SCRIPTS))
    store = SessionStore(session_id, snapshot_dir=snapshot_dir)
    try:
        return store.get_all_file_entries()
    finally:
        store.close()


def _run_read_cache(snapshot_dir: Path, args: list[str],
                    stdin_payload: dict | None, extra_env: dict | None = None):
    """Run read_cache.py as a subprocess pinned to an isolated snapshot dir."""
    env = dict(os.environ)
    env["TOKEN_OPTIMIZER_SNAPSHOT_DIR"] = str(snapshot_dir)
    # Keep the read cache enabled and deterministic for the end-to-end test.
    env.setdefault("TOKEN_OPTIMIZER_READ_CACHE", "1")
    if extra_env:
        env.update(extra_env)
    stdin_data = json.dumps(stdin_payload) if stdin_payload is not None else None
    return subprocess.run(
        [sys.executable, str(READ_CACHE), *args],
        input=stdin_data,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


# ---------------------------------------------------------------------------
# Acceptance bullet 1: clear-compacted empties the named session's file_reads
# ---------------------------------------------------------------------------

def test_clear_compacted_empties_named_session_file_reads(tmp_path):
    file_f = str(tmp_path / "unchanged.txt")
    Path(file_f).write_text("hello world\n", encoding="utf-8")
    st = os.stat(file_f)

    _seed_file_entry(tmp_path, SESSION_S, file_f, st.st_mtime_ns, st.st_size)
    assert _file_entries(tmp_path, SESSION_S), "seed should have created a row"

    out = _run_read_cache(
        tmp_path, ["--clear-compacted", "--quiet"], {"session_id": SESSION_S}
    )
    assert out.returncode == 0, out.stderr

    entries = _file_entries(tmp_path, SESSION_S)
    assert entries == {}, (
        f"--clear-compacted must empty file_reads for the named session; "
        f"got {entries}"
    )


# ---------------------------------------------------------------------------
# Acceptance bullet 2: a different live session's file_reads are untouched
# ---------------------------------------------------------------------------

def test_clear_compacted_leaves_other_live_session_untouched(tmp_path):
    file_f = str(tmp_path / "a.txt")
    file_g = str(tmp_path / "b.txt")
    Path(file_f).write_text("a\n", encoding="utf-8")
    Path(file_g).write_text("b\n", encoding="utf-8")
    st_f = os.stat(file_f)
    st_g = os.stat(file_g)

    _seed_file_entry(tmp_path, SESSION_S, file_f, st_f.st_mtime_ns, st_f.st_size)
    _seed_file_entry(tmp_path, SESSION_S2, file_g, st_g.st_mtime_ns, st_g.st_size)

    out = _run_read_cache(
        tmp_path, ["--clear-compacted", "--quiet"], {"session_id": SESSION_S}
    )
    assert out.returncode == 0, out.stderr

    assert _file_entries(tmp_path, SESSION_S) == {}, "S should be cleared"
    s2 = _file_entries(tmp_path, SESSION_S2)
    assert file_g in s2, (
        f"session S2 is a different live session and must be UNTOUCHED; "
        f"got {s2}"
    )


# ---------------------------------------------------------------------------
# Acceptance bullet 3: bare --clear (no --session) retains its documented
# "all" behavior (legacy json/tmp + decisions logs unlinked, old stores pruned)
# ---------------------------------------------------------------------------

def test_bare_clear_without_session_retains_all_behavior(tmp_path):
    snapshot_dir = tmp_path
    cache_dir = snapshot_dir / "read-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    decisions_dir = cache_dir / "decisions"
    decisions_dir.mkdir(parents=True, exist_ok=True)

    # Legacy per-session cache json + tmp (the "all" branch unlinks these).
    legacy_json = cache_dir / "legacysession.json"
    legacy_tmp = cache_dir / "legacysession.tmp"
    legacy_json.write_text("{}", encoding="utf-8")
    legacy_tmp.write_text("tmp", encoding="utf-8")

    # A decisions telemetry log (the "all" branch unlinks *.jsonl under decisions/).
    decisions_log = decisions_dir / "legacysession.jsonl"
    decisions_log.write_text('{"decision":"allow"}\n', encoding="utf-8")

    # A live session-store db (freshly seeded -> NOT pruned by cleanup_old_stores,
    # which only reaps stores >48h old; this is the documented behavior).
    file_f = str(tmp_path / "live.txt")
    Path(file_f).write_text("live\n", encoding="utf-8")
    st = os.stat(file_f)
    _seed_file_entry(snapshot_dir, SESSION_S, file_f, st.st_mtime_ns, st.st_size)

    out = _run_read_cache(snapshot_dir, ["--clear", "--quiet"], None)
    assert out.returncode == 0, out.stderr

    assert not legacy_json.exists(), "bare --clear must unlink legacy *.json"
    assert not legacy_tmp.exists(), "bare --clear must unlink legacy *.tmp"
    assert not decisions_log.exists(), (
        "bare --clear must unlink decisions/*.jsonl (documented 'all' behavior)"
    )
    # The fresh store is <48h old so cleanup_old_stores leaves it; the file_reads
    # row is NOT cleared by the 'all' branch (only legacy json + pruning). This
    # is exactly the gap #101 fixes via --clear-compacted for the live session.
    assert _file_entries(snapshot_dir, SESSION_S), (
        "bare --clear 'all' does not clear a fresh session's file_reads -- "
        "that is the documented behavior the NEW flag exists to fill"
    )


# ---------------------------------------------------------------------------
# Acceptance bullet (a): the decisions telemetry log STILL EXISTS after the
# compaction clear (proves we did NOT route through handle_clear's wipe-y branch)
# ---------------------------------------------------------------------------

def test_clear_compacted_preserves_decisions_log(tmp_path):
    snapshot_dir = tmp_path
    cache_dir = snapshot_dir / "read-cache"
    decisions_dir = cache_dir / "decisions"
    decisions_dir.mkdir(parents=True, exist_ok=True)

    file_f = str(tmp_path / "unchanged.txt")
    Path(file_f).write_text("hello world\n", encoding="utf-8")
    st = os.stat(file_f)
    _seed_file_entry(snapshot_dir, SESSION_S, file_f, st.st_mtime_ns, st.st_size)

    # Mirror what _decisions_log_path(session_id) produces so the test asserts
    # the exact file the wipe-y branch would have unlinked.
    import re
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "", SESSION_S) or "unknown"
    decisions_log = decisions_dir / f"{safe_id}.jsonl"
    decisions_log.write_text('{"decision":"allow"}\n', encoding="utf-8")
    assert decisions_log.exists()

    out = _run_read_cache(
        snapshot_dir, ["--clear-compacted", "--quiet"], {"session_id": SESSION_S}
    )
    assert out.returncode == 0, out.stderr

    assert _file_entries(snapshot_dir, SESSION_S) == {}, (
        "file_reads must be cleared"
    )
    assert decisions_log.exists(), (
        "the decisions telemetry log MUST survive the compaction clear -- "
        "if this fails, --clear-compacted was routed through handle_clear's "
        "session branch (which unlinks the log) instead of clear_file_entries()"
    )


# ---------------------------------------------------------------------------
# Acceptance bullet (b): end-to-end -- the actual user-facing bug. Seed a
# file_reads row for an unchanged file, run the clear, then run the PreToolUse
# Read path and assert the re-Read is NOT denied.
# ---------------------------------------------------------------------------

def _pretool_read(stdout: str) -> str | None:
    """Return the permissionDecision emitted on stdout, or None if absent."""
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        hook_out = payload.get("hookSpecificOutput", {})
        decision = hook_out.get("permissionDecision")
        if decision:
            return str(decision)
    return None


def test_clear_compacted_then_read_not_denied_end_to_end(tmp_path):
    snapshot_dir = tmp_path
    file_f = str(tmp_path / "unchanged.txt")
    Path(file_f).write_text("hello world\n", encoding="utf-8")
    st = os.stat(file_f)

    # Seed a redundant-read row: mtime/size match the on-disk file and the whole
    # file range is already seen. In block mode this is a deny.
    _seed_file_entry(snapshot_dir, SESSION_S, file_f, st.st_mtime_ns, st.st_size)

    read_payload = {
        "session_id": SESSION_S,
        "tool_name": "Read",
        "tool_input": {"file_path": file_f, "offset": 0, "limit": 0},
    }

    # Sanity gate: BEFORE the clear, block mode denies the redundant re-Read.
    # This proves the seeded row is a real redundant-read the bug would trip on.
    pre = _run_read_cache(
        snapshot_dir, ["--quiet"], read_payload,
        extra_env={"TOKEN_OPTIMIZER_READ_CACHE_MODE": "block"},
    )
    assert pre.returncode == 0, pre.stderr
    assert _pretool_read(pre.stdout) == "deny", (
        f"pre-clear: expected the seeded unchanged file to be denied in block "
        f"mode (proves the seed is a real redundant read); stdout={pre.stdout!r}"
    )

    # The fix: clear ONLY the live session's file_reads post-compaction.
    out = _run_read_cache(
        snapshot_dir, ["--clear-compacted", "--quiet"], {"session_id": SESSION_S}
    )
    assert out.returncode == 0, out.stderr
    assert _file_entries(snapshot_dir, SESSION_S) == {}, (
        "the clear must have removed the redundant-read row"
    )

    # The user-facing bug: post-compaction re-Read of the unchanged file must
    # NOT be denied -- compaction dropped it from context, so the cache entry
    # must not survive to block the re-read.
    post = _run_read_cache(
        snapshot_dir, ["--quiet"], read_payload,
        extra_env={"TOKEN_OPTIMIZER_READ_CACHE_MODE": "block"},
    )
    assert post.returncode == 0, post.stderr
    decision = _pretool_read(post.stdout)
    assert decision != "deny", (
        f"post-clear re-Read of an unchanged file must NOT be denied after the "
        f"compaction clear; got permissionDecision={decision!r}; "
        f"stdout={post.stdout!r}"
    )
