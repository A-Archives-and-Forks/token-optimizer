#!/usr/bin/env python3
"""Anti-drift tests for the committed self-contained Cowork plugin.

Cowork's marketplace renders a plugin only when its ``source`` points at a
small self-contained dir. The desktop ``token-optimizer`` entry has
``source: "./"`` (the whole ~328MB repo) and does NOT render in Cowork, so a
SLIM sibling is committed at ``cowork/token-optimizer/`` and listed as its own
marketplace plugin ``token-optimizer-cowork``. That committed tree is BUILT by
``cowork_install.py --emit-committed`` from the master runtime set, so it can
silently drift from the source it was generated off. These tests are the
tripwire: they rebuild via the packager API and assert the committed tree still
matches, byte for byte where it must.

Covers:
  (a) committed hooks/hooks.json == build_cowork_hooks(master hooks/hooks.json)
      -- trimmed to the 4 Cowork events, no SessionStart, keepwarm dropped, and
      the run-once features carried on UserPromptSubmit.
  (b) committed .claude-plugin/plugin.json has name token-optimizer-cowork, the
      ./hooks/hooks.json pointer, and version == root plugin.json version.
  (c) skills/token-optimizer/scripts/measure.py exists in the committed dir and
      is byte-identical to the root one.
  (d) rebuilding via --emit-committed leaves ``git status`` clean for
      cowork/token-optimizer/ (no drift).
  (e) the marketplace lists exactly 3 plugins with the expected names/sources.

Run: python3 -m pytest tests/test_cowork_committed_plugin.py -q
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "token-optimizer" / "scripts"
HOOKS_JSON = REPO / "hooks" / "hooks.json"
COMMITTED = REPO / "cowork" / "token-optimizer"
MARKETPLACE = REPO / ".claude-plugin" / "marketplace.json"
ROOT_MANIFEST = REPO / ".claude-plugin" / "plugin.json"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cowork_install  # noqa: E402

MASTER_TEMPLATE = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _commands(hooks_by_event, event):
    out = []
    for group in hooks_by_event.get(event, []):
        for hook in group.get("hooks", []):
            out.append(hook.get("command", ""))
    return out


# --------------------------------------------------------------------------- #
# (a) committed hooks.json == build_cowork_hooks(master)
# --------------------------------------------------------------------------- #

def test_committed_hooks_equals_build_cowork_hooks():
    committed = _load(COMMITTED / "hooks" / "hooks.json")
    expected = cowork_install.build_cowork_hooks(MASTER_TEMPLATE)
    assert committed == expected, (
        "committed cowork/token-optimizer/hooks/hooks.json drifted from "
        "build_cowork_hooks(master hooks/hooks.json); rerun "
        "`cowork_install.py --emit-committed`"
    )


def test_committed_hooks_are_the_four_cowork_events_only():
    hooks = _load(COMMITTED / "hooks" / "hooks.json")["hooks"]
    assert set(hooks) == {"UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"}
    assert "SessionStart" not in hooks
    for absent in ("PreCompact", "PostCompact", "SessionEnd", "StopFailure", "CwdChanged"):
        assert absent not in hooks, f"{absent} must not survive into the committed plugin"


def test_committed_hooks_drop_keepwarm_and_carry_runonce_on_userpromptsubmit():
    hooks = _load(COMMITTED / "hooks" / "hooks.json")["hooks"]
    all_cmds = [c for evt in hooks for c in _commands(hooks, evt)]
    assert [c for c in all_cmds if "keepwarm" in c] == [], "keepwarm must be dropped"

    ups = _commands(hooks, "UserPromptSubmit")
    for needle in ("ensure-health", "quality-cache --force", "compact-restore --new-session-only"):
        assert any(needle in c for c in ups), (
            f"run-once command missing from committed UserPromptSubmit: {needle!r}"
        )
    assert any("--once-per-session" in c for c in ups), (
        "run-once guard (--once-per-session) missing from committed UserPromptSubmit"
    )


def test_every_committed_command_uses_plugin_root_resolver():
    hooks = _load(COMMITTED / "hooks" / "hooks.json")["hooks"]
    cmds = [c for evt in hooks for c in _commands(hooks, evt)]
    assert cmds, "expected at least one committed hook command"
    for command in cmds:
        assert "${CLAUDE_PLUGIN_ROOT}" in command, command


# --------------------------------------------------------------------------- #
# (b) committed plugin.json manifest shape
# --------------------------------------------------------------------------- #

def test_committed_manifest_name_version_and_hooks_pointer():
    manifest = _load(COMMITTED / ".claude-plugin" / "plugin.json")
    assert manifest["name"] == "token-optimizer-cowork"
    assert manifest["hooks"] == "./hooks/hooks.json"
    root_version = _load(ROOT_MANIFEST)["version"]
    assert manifest["version"] == root_version, (
        f"committed plugin version {manifest['version']!r} != root "
        f"plugin.json version {root_version!r}"
    )
    assert "cowork" in manifest["description"].lower()


# --------------------------------------------------------------------------- #
# (c) measure.py byte-identical to root
# --------------------------------------------------------------------------- #

def test_committed_measure_py_is_byte_identical_to_root():
    committed_measure = COMMITTED / "skills" / "token-optimizer" / "scripts" / "measure.py"
    root_measure = REPO / "skills" / "token-optimizer" / "scripts" / "measure.py"
    assert committed_measure.exists(), "measure.py missing from committed plugin"
    assert root_measure.exists(), "root measure.py missing"
    assert committed_measure.read_bytes() == root_measure.read_bytes(), (
        "committed measure.py drifted from the root copy; rerun --emit-committed"
    )


def test_committed_dir_is_self_contained():
    # The four load-bearing surfaces a Cowork install needs.
    assert (COMMITTED / ".claude-plugin" / "plugin.json").exists()
    assert (COMMITTED / "hooks" / "hooks.json").exists()
    assert (COMMITTED / "skills" / "token-optimizer" / "scripts" / "measure.py").exists()
    assert (COMMITTED / "commands").is_dir()


# --------------------------------------------------------------------------- #
# (d) rebuild leaves git clean (no drift)
# --------------------------------------------------------------------------- #

def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=REPO, capture_output=True, text=True, check=False
    )


def test_rebuild_emit_committed_leaves_git_clean():
    if _git("rev-parse", "--is-inside-work-tree").returncode != 0:
        pytest.skip("not a git work tree; cannot check for committed-plugin drift")

    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "cowork_install.py"), "--emit-committed"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"--emit-committed failed: {proc.stderr}"

    status = _git("status", "--porcelain", "--", "cowork/token-optimizer")
    assert status.returncode == 0, status.stderr
    assert status.stdout.strip() == "", (
        "rebuilding the committed Cowork plugin produced a diff (drift):\n"
        f"{status.stdout}\n"
        "The committed tree is not reproducible from --emit-committed."
    )


# --------------------------------------------------------------------------- #
# (e) marketplace shape
# --------------------------------------------------------------------------- #

def test_marketplace_lists_exactly_three_plugins():
    plugins = _load(MARKETPLACE)["plugins"]
    by_name = {p["name"]: p for p in plugins}
    assert len(plugins) == 3, f"expected 3 plugins, got {[p['name'] for p in plugins]}"
    assert set(by_name) == {"token-optimizer", "to-hook-probe", "token-optimizer-cowork"}
    assert by_name["token-optimizer"]["source"] == "./"
    assert by_name["to-hook-probe"]["source"] == "./cowork/to-hook-probe"
    assert by_name["token-optimizer-cowork"]["source"] == "./cowork/token-optimizer"


def test_marketplace_cowork_entry_version_matches_root_and_probe_pinned():
    by_name = {p["name"]: p for p in _load(MARKETPLACE)["plugins"]}
    root_version = _load(ROOT_MANIFEST)["version"]
    assert by_name["token-optimizer"]["version"] == root_version
    assert by_name["token-optimizer-cowork"]["version"] == root_version
    assert by_name["to-hook-probe"]["version"] == "0.1.0"
    kw = by_name["token-optimizer-cowork"]["keywords"]
    assert "cowork" in kw and "beta" in kw
    assert by_name["token-optimizer-cowork"]["category"] == "productivity"
