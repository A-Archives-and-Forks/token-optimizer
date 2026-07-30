#!/usr/bin/env python3
"""statusLine must survive a plugin version bump with no blank window.

Root cause of the old "my status line is gone after updating" reports:
``_get_statusline_path`` wrote the VERSION-PINNED plugin-cache path into
settings.json::

    ~/.claude/plugins/cache/<mkt>/token-optimizer/5.11.67/skills/.../statusline.js

The moment the plugin updated, that path stopped existing. Claude Code renders
a statusLine whose command is missing as a silent blank line, and the repair
(``_fix_stale_settings_paths``) only runs at the NEXT SessionStart -- so the
first session after every update showed a blank status line.

Fix: for plugin-cache installs prefer the marketplace CLONE path, which has no
version segment and therefore survives updates::

    ~/.claude/plugins/marketplaces/<mkt>/skills/.../statusline.js

Fallback (clone pruned, non-cache install) keeps the version-pinned write plus
the existing self-heal.

Run: python3 -m pytest tests/test_statusline_stable_path.py -q
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "token-optimizer" / "scripts"


def _load_measure(monkeypatch, tmp_path, script_file: Path):
    """Import measure.py as if it were running from ``script_file``."""
    monkeypatch.setenv("TOKEN_OPTIMIZER_SNAPSHOT_DIR", str(tmp_path / "data"))
    monkeypatch.syspath_prepend(str(SCRIPTS))
    sys.modules.pop("measure", None)
    spec = importlib.util.spec_from_file_location("measure", SCRIPTS / "measure.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["measure"] = mod
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "CLAUDE_DIR", tmp_path / "claude")
    monkeypatch.setattr(mod, "__file__", str(script_file))
    return mod


def _build_install(tmp_path, version: str, *, with_marketplace_clone: bool = True):
    """A realistic plugin-cache layout plus (optionally) the marketplace clone."""
    claude = tmp_path / "claude"
    cache_scripts = (claude / "plugins" / "cache" / "alexgreensh-token-optimizer"
                     / "token-optimizer" / version / "skills" / "token-optimizer" / "scripts")
    cache_scripts.mkdir(parents=True, exist_ok=True)
    for name in ("measure.py", "statusline.js"):
        (cache_scripts / name).write_text("// cache copy\n", encoding="utf-8")

    clone_scripts = (claude / "plugins" / "marketplaces" / "alexgreensh-token-optimizer"
                     / "skills" / "token-optimizer" / "scripts")
    if with_marketplace_clone:
        clone_scripts.mkdir(parents=True, exist_ok=True)
        for name in ("measure.py", "statusline.js"):
            (clone_scripts / name).write_text("// clone copy\n", encoding="utf-8")
    return cache_scripts, clone_scripts


def test_plugin_cache_install_uses_the_stable_clone_path(tmp_path, monkeypatch):
    cache_scripts, clone_scripts = _build_install(tmp_path, "5.11.67")
    m = _load_measure(monkeypatch, tmp_path, cache_scripts / "measure.py")

    path = m._get_statusline_path()

    assert path == str(clone_scripts / "statusline.js"), path
    assert "5.11.67" not in path, "version-pinned path leaked into statusLine"


def test_version_bump_leaves_a_working_statusline_no_blank_window(tmp_path, monkeypatch):
    """THE regression: the path written before an update still resolves after."""
    cache_scripts, clone_scripts = _build_install(tmp_path, "5.11.67")
    m = _load_measure(monkeypatch, tmp_path, cache_scripts / "measure.py")
    written = m._get_statusline_path()
    assert Path(written).is_file()

    # Simulate the update: the old version dir is replaced by a new one.
    import shutil
    shutil.rmtree(cache_scripts.parents[3] / "5.11.67")
    new_scripts = (cache_scripts.parents[3] / "5.11.68"
                   / "skills" / "token-optimizer" / "scripts")
    new_scripts.mkdir(parents=True)
    for name in ("measure.py", "statusline.js"):
        (new_scripts / name).write_text("// cache copy v2\n", encoding="utf-8")

    assert Path(written).is_file(), (
        "the statusLine path written before the update no longer exists -- "
        "this is the blank-status-line window"
    )


def test_version_pinned_path_would_have_broken(tmp_path, monkeypatch):
    """Control: prove the old behavior really did break, so the fix is load-bearing."""
    cache_scripts, _clone = _build_install(tmp_path, "5.11.67")
    old_style = cache_scripts / "statusline.js"
    assert old_style.is_file()

    import shutil
    shutil.rmtree(cache_scripts.parents[3] / "5.11.67")

    assert not old_style.is_file(), "fixture does not model the update correctly"


def test_falls_back_when_marketplace_clone_is_missing(tmp_path, monkeypatch):
    cache_scripts, _clone = _build_install(tmp_path, "5.11.67", with_marketplace_clone=False)
    m = _load_measure(monkeypatch, tmp_path, cache_scripts / "measure.py")

    path = m._get_statusline_path()

    assert path == str(cache_scripts / "statusline.js"), path


def test_non_cache_install_is_unchanged(tmp_path, monkeypatch):
    """Script/dev installs keep the plain resolved sibling path."""
    plain = tmp_path / "repo" / "skills" / "token-optimizer" / "scripts"
    plain.mkdir(parents=True)
    (plain / "statusline.js").write_text("// dev\n", encoding="utf-8")
    m = _load_measure(monkeypatch, tmp_path, plain / "measure.py")

    assert m._get_statusline_path() == str(plain / "statusline.js")


def test_clone_path_outside_the_marketplaces_dir_is_rejected(tmp_path, monkeypatch):
    """Containment: a symlinked clone must not redirect the statusLine."""
    cache_scripts, clone_scripts = _build_install(tmp_path, "5.11.67",
                                                  with_marketplace_clone=False)
    outside = tmp_path / "elsewhere" / "scripts"
    outside.mkdir(parents=True)
    (outside / "statusline.js").write_text("// evil\n", encoding="utf-8")
    mkt = tmp_path / "claude" / "plugins" / "marketplaces" / "alexgreensh-token-optimizer"
    mkt.parent.mkdir(parents=True, exist_ok=True)
    mkt.symlink_to(outside.parents[0], target_is_directory=True)

    m = _load_measure(monkeypatch, tmp_path, cache_scripts / "measure.py")
    path = m._get_statusline_path()

    assert "elsewhere" not in path, f"symlinked clone escaped containment: {path}"
    assert path == str(cache_scripts / "statusline.js")


# --- G3 C-P2-3: existing installs get MIGRATED to the stable clone path -------

def _write_settings(measure_mod, tmp_path, monkeypatch, statusline_cmd):
    """Point measure.SETTINGS_PATH at a temp settings.json and seed a statusLine."""
    settings_path = tmp_path / "claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    import json
    settings_path.write_text(
        json.dumps({"statusLine": {"type": "command", "command": statusline_cmd}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(measure_mod, "SETTINGS_PATH", settings_path)
    return settings_path


def _current_statusline_cmd(settings_path):
    import json
    return json.loads(settings_path.read_text(encoding="utf-8"))["statusLine"]["command"]


def test_existing_version_pinned_statusline_is_migrated_to_clone(tmp_path, monkeypatch):
    """The installed base (version-pinned cache statusLine) must be lifted onto
    the update-surviving clone path at ensure-health, not left to keep blanking."""
    cache_scripts, clone_scripts = _build_install(tmp_path, "5.11.67")
    m = _load_measure(monkeypatch, tmp_path, cache_scripts / "measure.py")
    old_cmd = f"node '{cache_scripts / 'statusline.js'}'"
    settings_path = _write_settings(m, tmp_path, monkeypatch, old_cmd)

    assert m._migrate_statusline_to_stable_path() is True
    new_cmd = _current_statusline_cmd(settings_path)
    assert new_cmd == f"node '{clone_scripts / 'statusline.js'}'", new_cmd
    assert "/plugins/cache/" not in new_cmd
    assert "5.11.67" not in new_cmd


def test_migration_is_idempotent_when_already_on_clone(tmp_path, monkeypatch):
    cache_scripts, clone_scripts = _build_install(tmp_path, "5.11.67")
    m = _load_measure(monkeypatch, tmp_path, cache_scripts / "measure.py")
    clone_cmd = f"node '{clone_scripts / 'statusline.js'}'"
    settings_path = _write_settings(m, tmp_path, monkeypatch, clone_cmd)

    assert m._migrate_statusline_to_stable_path() is False
    assert _current_statusline_cmd(settings_path) == clone_cmd


def test_migration_noop_when_clone_missing(tmp_path, monkeypatch):
    """No clone to migrate to -> leave the version-pinned path (self-heal still runs)."""
    cache_scripts, _clone = _build_install(tmp_path, "5.11.67", with_marketplace_clone=False)
    m = _load_measure(monkeypatch, tmp_path, cache_scripts / "measure.py")
    old_cmd = f"node '{cache_scripts / 'statusline.js'}'"
    settings_path = _write_settings(m, tmp_path, monkeypatch, old_cmd)

    assert m._migrate_statusline_to_stable_path() is False
    assert _current_statusline_cmd(settings_path) == old_cmd


def test_migration_ignores_foreign_statusline(tmp_path, monkeypatch):
    """A user's own non-token-optimizer statusLine must never be rewritten."""
    cache_scripts, clone_scripts = _build_install(tmp_path, "5.11.67")
    m = _load_measure(monkeypatch, tmp_path, cache_scripts / "measure.py")
    foreign = "node '/home/u/.config/mybar/bar.js'"
    settings_path = _write_settings(m, tmp_path, monkeypatch, foreign)

    assert m._migrate_statusline_to_stable_path() is False
    assert _current_statusline_cmd(settings_path) == foreign


def test_uninstall_matcher_still_matches_the_written_command(tmp_path, monkeypatch):
    """The F1/uninstall matcher keys on statusline.js + token-optimizer."""
    cache_scripts, clone_scripts = _build_install(tmp_path, "5.11.67")
    m = _load_measure(monkeypatch, tmp_path, cache_scripts / "measure.py")

    cmd = f"node '{m._get_statusline_path()}'"

    assert "statusline.js" in cmd and "token-optimizer" in cmd, cmd
    settings = {"statusLine": {"type": "command", "command": cmd}}
    assert m._remove_our_settings_entries(settings), "matcher no longer claims our statusLine"
    assert "statusLine" not in settings
