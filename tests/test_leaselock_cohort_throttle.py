#!/usr/bin/env python3
"""Regression contract for the ``cohort_throttle`` opt-out on ``LeaseLock``.

The default ``LeaseLock`` elevates ``reuse_wall`` for blocking acquisitions so
a thundering-herd cohort contending on the SAME mutation/file is suppressed
through the lease window. Archive writers, however, persist DISTINCT
``tool_use_id`` files, so cohort suppression is wrong for them: a trailing
distinct-PID archive call silently times out and drops its entry.

``cohort_throttle=False`` opts the lock out of ``reuse_wall`` elevation so a
released lease is immediately reclaimable by a distinct-PID successor.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "token-optimizer" / "scripts"


def _run_child(
    code: str,
    env: dict,
    *,
    timeout: int = 10,
    extra_args: list[str] | None = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", code, *(extra_args or [])],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


def _base_env(snapshot_dir: Path | None = None) -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SCRIPTS)
    if snapshot_dir is not None:
        env["TOKEN_OPTIMIZER_SNAPSHOT_DIR"] = str(snapshot_dir)
    # Force a deterministic HOME so any incidental config lookups stay isolated.
    env.setdefault("HOME", str(snapshot_dir or Path("/tmp")))
    return env


# ---------------------------------------------------------------------------
# TEST 1 (primary regression, LeaseLock level)
# ---------------------------------------------------------------------------

_TEST1_CHILD_A = textwrap.dedent(
    """
    import sys
    from pathlib import Path
    from hook_runtime import LeaseLock
    path = Path(sys.argv[1])
    lock = LeaseLock(
        path,
        acquire_timeout=0.075,
        lease_seconds=10.0,
        cohort_throttle=False,
    )
    if not lock.acquire():
        print("A_FAIL_ACQUIRE"); sys.exit(2)
    path.with_suffix(".marker").write_text("A")
    lock.release()
    print("A_OK")
    """
)

_TEST1_CHILD_B = textwrap.dedent(
    """
    import sys
    from pathlib import Path
    from hook_runtime import LeaseLock
    path = Path(sys.argv[1])
    lock = LeaseLock(
        path,
        acquire_timeout=0.075,
        lease_seconds=10.0,
        cohort_throttle=False,
    )
    ok = lock.acquire()
    if ok:
        lock.release()
        print("OK")
        sys.exit(0)
    else:
        print("FAIL")
        sys.exit(1)
    """
)


def test_distinct_pid_successor_acquires_released_archive_writer_lease(tmp_path):
    """A distinct-PID successor reclaims a released ``cohort_throttle=False``
    lease immediately (no 10s reuse_wall suppression)."""
    lock_path = tmp_path / "archive.lease"
    env = _base_env()

    child_a = _run_child(_TEST1_CHILD_A, env, extra_args=[str(lock_path)])
    assert child_a.returncode == 0, child_a.stderr

    child_b = _run_child(_TEST1_CHILD_B, env, extra_args=[str(lock_path)])
    assert child_b.returncode == 0, (
        f"successor did not acquire released lease: "
        f"rc={child_b.returncode} stdout={child_b.stdout!r} stderr={child_b.stderr!r}"
    )
    assert child_b.stdout.strip() == "OK"


# ---------------------------------------------------------------------------
# TEST 2 (preservation: default cohort_throttle still suppresses successor)
# ---------------------------------------------------------------------------

_TEST2_CHILD_A = textwrap.dedent(
    """
    import sys
    from hook_runtime import LeaseLock
    path = sys.argv[1]
    lock = LeaseLock(
        path,
        acquire_timeout=0.075,
        lease_seconds=10.0,
    )
    if not lock.acquire():
        print("A_FAIL_ACQUIRE"); sys.exit(2)
    lock.release()
    print("A_OK")
    """
)

_TEST2_CHILD_B = textwrap.dedent(
    """
    import sys
    from hook_runtime import LeaseLock
    path = sys.argv[1]
    lock = LeaseLock(
        path,
        acquire_timeout=0.075,
        lease_seconds=10.0,
    )
    ok = lock.acquire()
    if ok:
        lock.release()
        print("OK")
        sys.exit(0)
    else:
        print("FAIL")
        sys.exit(1)
    """
)


def test_default_cohort_throttle_still_suppresses_distinct_pid_successor(tmp_path):
    """Default ``cohort_throttle=True`` preserves thundering-herd protection:
    a distinct-PID successor is refused reclamation for the reuse_wall window."""
    lock_path = tmp_path / "herd.lease"
    env = _base_env()

    child_a = _run_child(_TEST2_CHILD_A, env, extra_args=[str(lock_path)])
    assert child_a.returncode == 0, child_a.stderr

    child_b = _run_child(_TEST2_CHILD_B, env, extra_args=[str(lock_path)])
    # Successor MUST be suppressed (acquire returns False within the 75ms
    # timeout because reuse_wall is elevated for the full 10s lease).
    assert child_b.returncode == 1, (
        f"successor unexpectedly acquired the herd-protected lease: "
        f"rc={child_b.returncode} stdout={child_b.stdout!r} stderr={child_b.stderr!r}"
    )
    assert child_b.stdout.strip() == "FAIL"


# ---------------------------------------------------------------------------
# TEST 3 (integration: real archive_original silent data loss)
# ---------------------------------------------------------------------------

_TEST3_CHILD = textwrap.dedent(
    """
    import sys
    from archive_result import archive_original
    content = sys.argv[1]
    key = sys.argv[2]
    result = archive_original(
        content=content,
        session_id="sess1",
        key=key,
        tool_name="Bash",
    )
    if result is None:
        print("NONE"); sys.exit(2)
    print(result)
    """
)


def test_archive_original_distinct_pids_distinct_keys_both_archived(tmp_path):
    """Two distinct-PID ``archive_original`` calls with distinct keys must both
    land on disk. Pre-fix the second call silently dropped its entry."""
    snapshot_dir = tmp_path / "snap"
    snapshot_dir.mkdir()
    env = _base_env(snapshot_dir)

    child_a = _run_child(
        _TEST3_CHILD, env, timeout=20, extra_args=["AAA", "toolA"]
    )
    assert child_a.returncode == 0, (
        f"child A failed: rc={child_a.returncode} "
        f"stdout={child_a.stdout!r} stderr={child_a.stderr!r}"
    )
    assert child_a.stdout.strip() == "toolA"

    child_b = _run_child(
        _TEST3_CHILD, env, timeout=20, extra_args=["BBB", "toolB"]
    )
    assert child_b.returncode == 0, (
        f"child B failed: rc={child_b.returncode} "
        f"stdout={child_b.stdout!r} stderr={child_b.stderr!r}"
    )
    assert child_b.stdout.strip() == "toolB"

    archive_root = snapshot_dir / "tool-archive" / "sess1"
    assert (archive_root / "toolA.json").exists(), (
        f"toolA.json missing; archive_root={archive_root} "
        f"contents={list(archive_root.glob('*')) if archive_root.exists() else 'NO DIR'}"
    )
    assert (archive_root / "toolB.json").exists(), (
        f"toolB.json missing (silent data loss); archive_root={archive_root} "
        f"contents={list(archive_root.glob('*')) if archive_root.exists() else 'NO DIR'}"
    )
