"""handle_stop must not remove a session lock another process is holding.

inflight-{sid}.lock is the published LeaseLock rendezvous name. Removing it
while a PostToolUse handler is inside its critical section lets a third
process publish onto the free name and run concurrently, and the displaced
holder never finds out, because release() operates on its own candidate inode.
"""
import os
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "token-optimizer" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import copilot_hook_bridge as bridge  # noqa: E402
from hook_runtime import LeaseLock  # noqa: E402


def _stop_payload(sid):
    return {"session_id": sid, "hook_event_name": "Stop", "cwd": os.getcwd()}


def test_handle_stop_leaves_a_held_session_lock_alone(tmp_path, monkeypatch):
    """The bug: a bare unlink removed a lock still held by another holder."""
    sid = "held-session"
    monkeypatch.setattr(bridge, "_to_dir", lambda: tmp_path)
    lock_path = tmp_path / f"inflight-{sid}.lock"

    holder = LeaseLock(lock_path, acquire_timeout=0)
    assert holder.acquire(), "precondition: holder must take the lock"
    try:
        assert lock_path.exists()
        bridge.handle_stop(_stop_payload(sid))
        # Before the fix this file was gone, so a third process could publish
        # onto the same name and enter the critical section concurrently.
        assert lock_path.exists(), (
            "handle_stop removed a lock that another process still holds"
        )
    finally:
        holder.release()
        if lock_path.exists():
            lock_path.unlink()


def test_handle_stop_still_removes_an_unheld_session_lock(tmp_path, monkeypatch):
    """The cleanup must still happen in the normal, uncontended case."""
    sid = "free-session"
    monkeypatch.setattr(bridge, "_to_dir", lambda: tmp_path)
    lock_path = tmp_path / f"inflight-{sid}.lock"

    stale = LeaseLock(lock_path, acquire_timeout=0)
    assert stale.acquire()
    stale.release()  # release() leaves the published name behind by design
    assert lock_path.exists(), "precondition: released lock leaves its published name"

    bridge.handle_stop(_stop_payload(sid))
    assert not lock_path.exists(), "handle_stop should reap an unheld lock"


def test_handle_stop_always_removes_the_tally_file(tmp_path, monkeypatch):
    """The .json tally is not a lock and is unconditionally the Stop event's to clear."""
    sid = "tally-session"
    monkeypatch.setattr(bridge, "_to_dir", lambda: tmp_path)
    tally = tmp_path / f"inflight-{sid}.json"
    tally.write_text("{}", encoding="utf-8")

    lock_path = tmp_path / f"inflight-{sid}.lock"
    holder = LeaseLock(lock_path, acquire_timeout=0)
    assert holder.acquire()
    try:
        bridge.handle_stop(_stop_payload(sid))
        assert not tally.exists(), "tally must be cleared even when the lock is held"
        assert lock_path.exists(), "held lock must survive"
    finally:
        holder.release()
        if lock_path.exists():
            lock_path.unlink()
