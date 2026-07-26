"""Orphan reclamation must not delete data an installer recreates mid-flight.

Rechecking preconditions is not enough on its own: shutil.rmtree resolves the
PATHNAME, so anything that recreates the identity between the last check and
the delete gets destroyed. The reclaimer claims the tree with an atomic rename
first, which frees the pathname for a concurrent reinstall.
"""
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "token-optimizer" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import plugin_env  # noqa: E402


@pytest.fixture
def orphan(tmp_path, monkeypatch):
    """A stale, unregistered, non-active identity dir eligible for reclaim."""
    active = tmp_path / "active-identity"
    active.mkdir()
    stale = tmp_path / "stale-identity"
    stale.mkdir()
    (stale / "data.json").write_text("{}", encoding="utf-8")
    old = time.time() - (400 * 86400)
    import os
    os.utime(stale / "data.json", (old, old))
    os.utime(stale, (old, old))

    monkeypatch.setattr(plugin_env, "_registered_plugin_data_dirs", lambda: [active])
    monkeypatch.setattr(plugin_env, "_announce_orphan_reclamation", lambda *a, **k: None)
    monkeypatch.setattr(
        plugin_env, "find_sibling_plugin_data_dirs",
        lambda _active: [stale] if stale.exists() else [],
    )

    # resolve_plugin_data_dir is lru_cached and the reclaimer calls
    # .cache_clear() on it mid-loop, so the stub needs that attribute too.
    def _resolve():
        return active
    _resolve.cache_clear = lambda: None
    monkeypatch.setattr(plugin_env, "resolve_plugin_data_dir", _resolve)

    # Deletion is opt-in by design; without this the reclaimer only warns.
    monkeypatch.setenv("TOKEN_OPTIMIZER_RECLAIM_ORPHANED_DATA", "1")
    return active, stale


def test_recreated_identity_survives_reclamation(orphan, monkeypatch):
    """An installer recreating the dir during the delete must keep its data.

    _latest_tree_mtime is the last read before the destructive step, so hooking
    it reproduces the real interleaving: the installer wins the pathname right
    as the reclaimer commits.
    """
    active, stale = orphan
    real_mtime = plugin_env._latest_tree_mtime
    fired = {"n": 0}

    def racing_mtime(p):
        result = real_mtime(p)
        # Fire once, after the reclaimer has claimed the tree by rename: the
        # installer now recreates the ORIGINAL pathname with fresh data.
        if fired["n"] == 0 and not stale.exists():
            fired["n"] = 1
            stale.mkdir(parents=True, exist_ok=True)
            (stale / "fresh-install.json").write_text('{"live":true}', encoding="utf-8")
        return result

    monkeypatch.setattr(plugin_env, "_latest_tree_mtime", racing_mtime)
    plugin_env.reclaim_orphaned_plugin_data_dirs(min_age_days=30)

    assert stale.exists(), "the recreated identity directory was deleted"
    assert (stale / "fresh-install.json").exists(), (
        "reclamation destroyed data an installer wrote after the pathname was freed"
    )


def test_genuinely_stale_identity_is_still_reclaimed(orphan):
    """The fix must not make reclamation stop working."""
    active, stale = orphan
    removed = plugin_env.reclaim_orphaned_plugin_data_dirs(min_age_days=30)
    assert not stale.exists(), "a genuinely stale identity should be removed"
    assert stale in removed


def test_no_quarantine_directory_is_left_behind(orphan):
    """A rename-claim must never leak a .reclaiming-* directory."""
    active, stale = orphan
    parent = stale.parent
    plugin_env.reclaim_orphaned_plugin_data_dirs(min_age_days=30)
    leftovers = [p.name for p in parent.iterdir() if ".reclaiming-" in p.name]
    assert leftovers == [], f"quarantine directories leaked: {leftovers}"
