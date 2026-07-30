#!/usr/bin/env python3
"""#106 F2 (P1): the uninstall tombstone must OUTLIVE the uninstall.

Root cause of the regression these tests pin:
``_uninstall_*_daemon`` wrote the adv-006 ``.daemon-thrash`` tombstone first
(so a racing respawn exits cleanly), then UNLINKED it again at the end of the
per-identity sweep. Each generated ``dashboard-server.py`` bakes in its own
identity's breadcrumb path and checks it on start::

    if os.path.exists(<breadcrumb>): return "noop-tombstoned"

so deleting the breadcrumb re-armed daemon self-revive: an orphaned
LaunchAgent/unit could resurrect the daemon after a "successful" uninstall and
mint a fresh 0600 CSRF token.

Contract pinned here:
  1. After uninstall, EVERY swept identity has a tombstone (not just the
     active one) -- a sibling's orphaned daemon must stay dead too.
  2. ``--this-install-only`` still tombstones the identity it did sweep.
  3. A legitimate reinstall is the ONLY thing that clears the tombstone
     (``setup_daemon`` unlinks it before starting), so uninstall never
     re-arms revive.

Run: python3 -m pytest tests/test_daemon_uninstall_tombstone_persists.py -q
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "token-optimizer" / "scripts"
TOMBSTONE = ".daemon-thrash"


@pytest.fixture()
def measure(tmp_path, monkeypatch):
    """Import measure.py with SNAPSHOT_DIR pinned into tmp_path."""
    monkeypatch.setenv("TOKEN_OPTIMIZER_SNAPSHOT_DIR", str(tmp_path / "active" / "data"))
    monkeypatch.syspath_prepend(str(SCRIPTS))
    sys.modules.pop("measure", None)
    spec = importlib.util.spec_from_file_location("measure", SCRIPTS / "measure.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["measure"] = mod
    spec.loader.exec_module(mod)
    yield mod
    sys.modules.pop("measure", None)


def _seed_identity(snap_dir: Path) -> None:
    """A daemon-bearing identity: script + 0600 token + host, no tombstone."""
    snap_dir.mkdir(parents=True, exist_ok=True)
    (snap_dir / "dashboard-server.py").write_text("# daemon\n", encoding="utf-8")
    token = snap_dir / "daemon-token"
    token.write_text("secret\n", encoding="utf-8")
    token.chmod(0o600)
    (snap_dir / "dashboard-host").write_text("127.0.0.1\n", encoding="utf-8")


def _run_uninstall(measure, monkeypatch, snap_dirs, this_install_only=False):
    """Drive the launchd uninstaller with the identity sweep stubbed to
    `snap_dirs` and every OS call neutralized."""
    monkeypatch.setattr(
        measure, "_daemon_identity_snapshot_dirs",
        lambda only: [snap_dirs[0]] if only else list(snap_dirs),
    )
    monkeypatch.setattr(measure, "_ALL_LAUNCH_AGENT_LABELS", ())
    monkeypatch.setattr(measure, "_reclaim_posix_daemon_port", lambda *a, **k: None)
    measure._uninstall_launchd_daemon(this_install_only=this_install_only)


def test_tombstone_persists_in_every_swept_identity(measure, tmp_path, monkeypatch):
    """THE regression: post-uninstall, both identities keep a tombstone."""
    active = tmp_path / "active" / "data"
    sibling = tmp_path / "sibling" / "data"
    _seed_identity(active)
    _seed_identity(sibling)

    _run_uninstall(measure, monkeypatch, [active, sibling])

    for snap in (active, sibling):
        assert (snap / TOMBSTONE).exists(), (
            f"{snap} has no tombstone after uninstall -- daemon self-revive is "
            "re-armed and an orphaned job can mint a fresh 0600 token"
        )
        # The real daemon artifacts ARE gone.
        assert not (snap / "dashboard-server.py").exists()
        assert not (snap / "daemon-token").exists()


def test_tombstone_survives_when_it_already_existed(measure, tmp_path, monkeypatch):
    """A pre-existing tombstone (prior uninstall / thrash back-off) is kept."""
    active = tmp_path / "active" / "data"
    _seed_identity(active)
    (active / TOMBSTONE).write_text("", encoding="utf-8")

    _run_uninstall(measure, monkeypatch, [active])

    assert (active / TOMBSTONE).exists()


def test_this_install_only_still_tombstones_the_swept_identity(measure, tmp_path, monkeypatch):
    """Scoped uninstall must not leave its own identity revivable."""
    active = tmp_path / "active" / "data"
    sibling = tmp_path / "sibling" / "data"
    _seed_identity(active)
    _seed_identity(sibling)

    _run_uninstall(measure, monkeypatch, [active, sibling], this_install_only=True)

    assert (active / TOMBSTONE).exists()
    # Sibling untouched by a scoped uninstall (its daemon files remain).
    assert (sibling / "dashboard-server.py").exists()
    assert (sibling / "daemon-token").exists()


def test_reinstall_is_the_only_path_that_clears_the_tombstone(measure, tmp_path):
    """setup_daemon's start path unlinks it; nothing in uninstall may.

    Guards the lifecycle contract: uninstall writes, install clears. A future
    refactor that re-adds an unlink to the uninstall sweep fails the first test
    above; this one pins the other half (the clear still exists on the start
    path) so the tombstone can never become permanently sticky either.
    """
    source = (SCRIPTS / "measure.py").read_text(encoding="utf-8")
    assert 'if DAEMON_THRASH_BREADCRUMB.exists():' in source, (
        "setup_daemon's stale-tombstone clear disappeared -- a reinstall would "
        "start a daemon that immediately exits noop-tombstoned"
    )
    assert '_unlink_if_exists(files["thrash_breadcrumb"])' not in source, (
        "an uninstall path unlinks the tombstone again -- #106 F2 P1 regression"
    )


def test_uninstall_reclaims_the_daemon_port(measure, tmp_path, monkeypatch):
    """#106 F2 (P1b): unregistering must be followed by an actual process kill."""
    active = tmp_path / "active" / "data"
    _seed_identity(active)
    calls = []
    monkeypatch.setattr(
        measure, "_daemon_identity_snapshot_dirs", lambda only: [active]
    )
    monkeypatch.setattr(measure, "_ALL_LAUNCH_AGENT_LABELS", ())
    monkeypatch.setattr(
        measure, "_reclaim_posix_daemon_port",
        lambda *a, **k: calls.append("reclaim"),
    )

    measure._uninstall_launchd_daemon()

    assert calls == ["reclaim"], (
        "uninstall did not reclaim the daemon port -- the running daemon keeps "
        "serving with its CSRF token in memory (#106 F2 P1b)"
    )
