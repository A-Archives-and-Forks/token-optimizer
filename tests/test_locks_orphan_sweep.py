"""Bug B regression: ``cleanup_old_archives`` sweeps stale ``.lease.candidate-*``
orphans from ``archive_root/.locks/``.

``hook_runtime.LeaseLock._try_create`` retains the candidate hard link after a
successful publish; only ``release()`` unlinks it, and ``HookDeadline._watch``
calls ``os._exit(0)`` which bypasses ``release()``.  ``cleanup_old_archives``
skips every dot-dir (the ``.locks`` directory is never scanned as a session
candidate), so orphan candidates accumulate without bound.

These tests prove (mapping to the validation contract):

  * VAL-B-001 - a stale ``.lease.candidate-{nonce}`` aged past the max lease
    duration (``lease_seconds + reclaim_grace`` = 10.25s for archive locks) is
    unlinked by ``cleanup_old_archives()``.
  * VAL-B-002 - a young ``.lease.candidate-{nonce}`` newer than the sweep
    threshold (a live acquisition in flight) survives ``cleanup_old_archives()``.
  * VAL-B-003 - ``.locks`` is still skipped as a *session* candidate (the
    dot-dir guard at the ``if session_dir.name.startswith("."): continue`` line
    is preserved) while a separate, bounded sweep reaps stale artifacts inside
    it; the dot-dir skip and the sweep coexist.

Run: python3 -m pytest tests/test_locks_orphan_sweep.py -q
"""

from __future__ import annotations

import importlib.util
import os
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "token-optimizer" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import hook_runtime  # noqa: E402

# Archive lock sites use LeaseLock(..., acquire_timeout=0) with NO deadline, so
# the max lease a candidate/reclaim artifact can represent is the default
# lease_seconds (10.0) + reclaim_grace (0.25) = 10.25s.  This is the sweep
# staleness threshold; the fix exposes it as the named module constant
# ``archive_result.LOCK_SWEEP_STALE_SECONDS``.
_DEFAULT_LEASE = hook_runtime.LeaseLock(
    SCRIPTS / ".probe.lease", acquire_timeout=0
)
MAX_LEASE_SECONDS = _DEFAULT_LEASE.lease_seconds + _DEFAULT_LEASE.reclaim_grace


def _load_archive_result(monkeypatch: pytest.MonkeyPatch, snapshot_dir: Path):
    monkeypatch.setenv("TOKEN_OPTIMIZER_SNAPSHOT_DIR", str(snapshot_dir))
    monkeypatch.setenv("TOKEN_OPTIMIZER_ARCHIVE_RETENTION_HOURS", "24")
    monkeypatch.setenv("TOKEN_OPTIMIZER_ARCHIVE_RETENTION_MAX_FILES", "1000")
    monkeypatch.setenv("TOKEN_OPTIMIZER_ARCHIVE_RETENTION_MAX_BYTES", "1000000")
    monkeypatch.syspath_prepend(str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "archive_result", SCRIPTS / "archive_result.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "archive_result", module)
    spec.loader.exec_module(module)
    return module


def _candidate_path(archive_root: Path, sid: str, nonce: str) -> Path:
    """Mirror the on-disk candidate naming ``.{sid}.lease.candidate-{nonce}``."""
    return archive_root / ".locks" / f".{sid}.lease.candidate-{nonce}"


def _age_old(path: Path) -> None:
    """Age ``path`` past the sweep staleness threshold (max lease duration)."""
    old = time.time() - (MAX_LEASE_SECONDS + 5)
    os.utime(path, (old, old))


def test_stale_candidate_older_than_max_lease_is_reaped(monkeypatch, tmp_path):
    """VAL-B-001: an orphan candidate aged past 10.25s is unlinked."""
    module = _load_archive_result(monkeypatch, tmp_path)
    root = tmp_path / "tool-archive"
    (root / ".locks").mkdir(parents=True)
    stale = _candidate_path(root, "sid-stale", "abc123")
    stale.write_bytes(b"orphan")
    os.chmod(stale, 0o600)
    _age_old(stale)

    assert stale.exists()  # sanity: present before cleanup
    module.cleanup_old_archives()

    assert not stale.exists(), "stale candidate orphan survived cleanup"


def test_young_candidate_newer_than_threshold_survives(monkeypatch, tmp_path):
    """VAL-B-002: a live (young) candidate is never reaped."""
    module = _load_archive_result(monkeypatch, tmp_path)
    root = tmp_path / "tool-archive"
    (root / ".locks").mkdir(parents=True)
    young = _candidate_path(root, "sid-live", "def456")
    young.write_bytes(b"live-acquirer")
    os.chmod(young, 0o600)
    # Just-created mtime (well within the sweep threshold).
    now = time.time()
    os.utime(young, (now, now))

    module.cleanup_old_archives()

    assert young.exists(), "young live candidate was reaped (false positive)"


def test_locks_dot_dir_session_skip_preserved_while_contents_swept(
    monkeypatch, tmp_path
):
    """VAL-B-003: ``.locks`` is not treated as a session dir, but stale
    candidates inside it are still reaped by the separate sweep."""
    module = _load_archive_result(monkeypatch, tmp_path)
    root = tmp_path / "tool-archive"
    locks = root / ".locks"
    locks.mkdir(parents=True)
    stale = _candidate_path(root, "sid-stale-2", "ghi789")
    stale.write_bytes(b"orphan")
    os.chmod(stale, 0o600)
    _age_old(stale)

    removed = module.cleanup_old_archives()

    # The .locks dot-dir itself is never pruned as a session dir: it still
    # exists and cleanup_old_archives reported zero removed session dirs.
    assert locks.exists(), ".locks dot-dir was pruned as a session dir"
    assert removed == 0, "cleanup_old_archives counted .locks as a removed session"
    # But the stale artifact inside it was reaped by the separate sweep.
    assert not stale.exists(), "stale candidate inside .locks survived the sweep"


def test_sweep_uses_named_module_constant_matching_max_lease_duration(monkeypatch, tmp_path):
    """The fix exposes a named module constant ``LOCK_SWEEP_STALE_SECONDS`` equal
    to ``lease_seconds + reclaim_grace`` (10.25s for archive locks).  This is a
    source-inspection-style assertion that the threshold is a named constant
    rather than an inline magic number."""
    module = _load_archive_result(monkeypatch, tmp_path)
    assert hasattr(module, "LOCK_SWEEP_STALE_SECONDS"), (
        "archive_result must expose a named LOCK_SWEEP_STALE_SECONDS constant"
    )
    assert module.LOCK_SWEEP_STALE_SECONDS == pytest.approx(MAX_LEASE_SECONDS)
    assert module.LOCK_SWEEP_STALE_SECONDS == pytest.approx(10.25)
