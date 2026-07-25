"""Orphan plugin-data identities remain readable and are reclaimed safely."""

from __future__ import annotations

import importlib
import json
import os
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "token-optimizer" / "scripts"


def _load_plugin_env(monkeypatch: pytest.MonkeyPatch, data_base: Path):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    module = importlib.import_module("plugin_env")
    module._PLUGIN_DATA_BASE = data_base
    module._INSTALLED_PLUGINS = data_base.parent / "installed_plugins.json"
    module._PLUGIN_DATA_ENV_VARS = ("CLAUDE_PLUGIN_DATA",)
    module.resolve_plugin_data_dir.cache_clear()
    return module


def test_glob_fallback_is_stable_instead_of_mtime_selected(monkeypatch, tmp_path):
    data_base = tmp_path / "data"
    alpha = data_base / "token-optimizer-alpha"
    zeta = data_base / "token-optimizer-zeta"
    alpha.mkdir(parents=True)
    zeta.mkdir()
    now = time.time()
    os.utime(alpha, (now - 100, now - 100))
    os.utime(zeta, (now, now))
    module = _load_plugin_env(monkeypatch, data_base)

    assert module.resolve_plugin_data_dir() == alpha

    os.utime(alpha, (now + 100, now + 100))
    os.utime(zeta, (now - 100, now - 100))
    module.resolve_plugin_data_dir.cache_clear()
    assert module.resolve_plugin_data_dir() == alpha


def test_sibling_detection_excludes_active_identity(monkeypatch, tmp_path):
    data_base = tmp_path / "data"
    active = data_base / "token-optimizer-active"
    orphan = data_base / "token-optimizer-orphan"
    active.mkdir(parents=True)
    orphan.mkdir()
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(active))
    module = _load_plugin_env(monkeypatch, data_base)

    assert module.find_sibling_plugin_data_dirs() == [orphan]
    assert module.snapshot_dir_candidates() == [
        active / "data",
        orphan / "data",
    ]


def test_orphan_reclamation_is_opt_in_age_gated_and_announced(
    monkeypatch, tmp_path, capsys
):
    data_base = tmp_path / "data"
    active = data_base / "token-optimizer-active"
    old_orphan = data_base / "token-optimizer-old"
    fresh_orphan = data_base / "token-optimizer-fresh"
    for path in (active, old_orphan, fresh_orphan):
        (path / "data").mkdir(parents=True)
        (path / "data" / "payload.json").write_text("private", encoding="utf-8")
    old = time.time() - (31 * 86400)
    for path in (old_orphan / "data" / "payload.json", old_orphan / "data", old_orphan):
        os.utime(path, (old, old))
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(active))
    module = _load_plugin_env(monkeypatch, data_base)
    module._INSTALLED_PLUGINS.write_text(
        json.dumps({"plugins": {"token-optimizer@active": []}}),
        encoding="utf-8",
    )

    assert module.reclaim_orphaned_plugin_data_dirs(min_age_days=30) == []
    assert old_orphan.exists()
    assert "not deleted" in capsys.readouterr().err

    monkeypatch.setenv("TOKEN_OPTIMIZER_RECLAIM_ORPHANED_DATA", "1")
    removed = module.reclaim_orphaned_plugin_data_dirs(min_age_days=30)

    assert removed == [old_orphan]
    assert active.exists()
    assert fresh_orphan.exists()
    assert not old_orphan.exists()
    assert "Reclaimed orphaned plugin data identity" in capsys.readouterr().err
    assert "token-optimizer-old" in (
        active / "data" / "orphan-reclamation.log"
    ).read_text(encoding="utf-8")


def test_reclamation_never_deletes_a_registered_sibling(monkeypatch, tmp_path):
    data_base = tmp_path / "data"
    active = data_base / "token-optimizer-active"
    registered = data_base / "token-optimizer-registered"
    for path in (active, registered):
        path.mkdir(parents=True)
    old = time.time() - (90 * 86400)
    os.utime(registered, (old, old))
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(active))
    monkeypatch.setenv("TOKEN_OPTIMIZER_RECLAIM_ORPHANED_DATA", "1")
    module = _load_plugin_env(monkeypatch, data_base)
    module._INSTALLED_PLUGINS.write_text(
        json.dumps({
            "plugins": {
                "token-optimizer@active": [],
                "token-optimizer@registered": [],
            }
        }),
        encoding="utf-8",
    )

    assert module.reclaim_orphaned_plugin_data_dirs(min_age_days=30) == []
    assert registered.exists()


def test_expand_checks_sibling_archives_before_reporting_miss(
    monkeypatch, tmp_path, capsys
):
    active = tmp_path / "active"
    sibling = tmp_path / "sibling"
    entry = sibling / "tool-archive" / "session-1" / "tool-1.json"
    entry.parent.mkdir(parents=True)
    entry.write_text('{"response":"recovered from sibling"}', encoding="utf-8")
    monkeypatch.setenv("TOKEN_OPTIMIZER_SNAPSHOT_DIR", str(active))
    monkeypatch.syspath_prepend(str(SCRIPTS))
    measure = importlib.import_module("measure")
    monkeypatch.setattr(measure, "SNAPSHOT_DIR", active)
    monkeypatch.setattr(
        measure,
        "snapshot_dir_candidates",
        lambda: [active, sibling],
    )
    monkeypatch.setattr(measure, "_log_reexpand_debit", lambda *args: None)

    measure.expand_archived("tool-1", session_id="session-1")

    assert "recovered from sibling" in capsys.readouterr().out
