#!/usr/bin/env python3
"""Cross-platform hook dispatcher.

Invoked from hooks.json via a small bash launcher that locates a usable
Python 3 interpreter on macOS, Linux, and Windows:

  "command": "bash \"${CLAUDE_PLUGIN_ROOT}/hooks/python-launcher.sh\" \"${CLAUDE_PLUGIN_ROOT}/hooks/run.py\" <script-relative-path> [args...]"

The launcher handles Windows-specific gotchas (Program Files spaced paths,
Microsoft Store zero-byte stubs in WindowsApps, py launcher fallback) so
this file can assume it's running under a real Python 3.9+.

This dispatcher resolves the target script under CLAUDE_PLUGIN_ROOT,
checks it exists, and runs it with the same interpreter (sys.executable).
On timeout we kill the child (Popen.kill) to avoid leaking a process
holding the trends.db SQLite lock. Always exits 0 so hook failures never
block the user's tool call.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

# Defense in depth: the launcher script already filters interpreters, but
# if a user's PATH has a stale Python 3.7 that slipped through, bail early
# so later imports don't explode with confusing SyntaxError noise.
if sys.version_info < (3, 9):
    sys.exit(0)


def _check_consent() -> bool:
    """Return True if consent is given or assumed. Fail-open on any error."""
    try:
        home = Path.home()

        # Resolve config path from env (set by Claude Code before hook invocation)
        plugin_data = os.environ.get("CLAUDE_PLUGIN_DATA", "")
        if plugin_data:
            pd = Path(plugin_data).resolve()
            if not str(pd).startswith(str(home)):
                return True  # Path outside home = skip (fail-open)
            config_path = pd / "config" / "config.json"
        else:
            # Legacy / Codex fallback
            codex_home = os.environ.get("CODEX_HOME", "")
            if codex_home:
                ch = Path(codex_home).resolve()
                if not str(ch).startswith(str(home)):
                    return True
                config_path = ch / "token-optimizer" / "config.json"
            else:
                config_path = home / ".claude" / "token-optimizer" / "config.json"

        if not config_path.exists() or config_path.is_symlink():
            return True  # No config or symlink = fail-open

        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)

        if config.get("enterprise_consent_shown"):
            return True

        # Backward compat backfill: existing users who saw v5 welcome have implicitly consented
        if config.get("v5_welcome_shown"):
            config["enterprise_consent_shown"] = True
            # Atomic write (tempfile + os.replace)
            import tempfile
            fd, tmp = tempfile.mkstemp(dir=str(config_path.parent), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as tf:
                    json.dump(config, tf, indent=2)
                os.replace(tmp, str(config_path))
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            return True

        return False  # No consent and no v5_welcome_shown = skip data collection
    except Exception:
        return True  # Fail-open: never block on errors


def _plugin_disabled_by_host() -> bool:
    """Return True if the host explicitly turned this plugin off via
    ~/.claude/settings.json's enabledPlugins map, so main() can no-op before
    spawning the dispatch subprocess at all.

    Claude Code's plugin loader does not appear to reliably stop invoking an
    already-registered plugin's hooks.json commands for existing sessions
    after enabledPlugins[<name>@<marketplace>] is flipped to false (observed:
    8/8 sessions started after such an edit still ran hooks and printed
    "[Token Optimizer]" output). This is a defensive self-check so the plugin
    honors its own disable flag even when the host does not enforce it,
    instead of silently continuing to spend tokens on every hook event.

    Fail-open (return False) on any error -- a missing/unreadable settings
    file, or a plugin/marketplace name we can't resolve, must never silently
    disable the plugin for users who never touched this setting.
    """
    try:
        plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT", "").strip()
        if not plugin_root:
            return False
        meta_dir = Path(plugin_root) / ".claude-plugin"
        plugin_json = meta_dir / "plugin.json"
        marketplace_json = meta_dir / "marketplace.json"
        if not plugin_json.is_file() or not marketplace_json.is_file():
            return False
        plugin_name = json.loads(plugin_json.read_text(encoding="utf-8")).get("name", "").strip()
        marketplace_name = json.loads(marketplace_json.read_text(encoding="utf-8")).get("name", "").strip()
        if not plugin_name or not marketplace_name:
            return False
        settings_path = Path.home() / ".claude" / "settings.json"
        if not settings_path.is_file() or settings_path.is_symlink():
            return False
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        enabled_plugins = settings.get("enabledPlugins")
        if not isinstance(enabled_plugins, dict):
            return False
        key = f"{plugin_name}@{marketplace_name}"
        return enabled_plugins.get(key) is False
    except Exception:
        return False


def main() -> int:
    if len(sys.argv) < 2:
        return 0
    if _plugin_disabled_by_host():
        return 0

    script_rel = sys.argv[1]
    script_args = sys.argv[2:]

    rel_path = Path(script_rel)
    if rel_path.is_absolute() or ".." in rel_path.parts:
        return 0

    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT", "").strip()
    if plugin_root:
        root_path = Path(plugin_root)
    else:
        # Fallback: relative to this wrapper's parent directory.
        root_path = Path(__file__).resolve().parent.parent

    try:
        root_resolved = root_path.resolve(strict=True)
        candidate = root_resolved / rel_path
        if not candidate.is_file():
            return 0
        script_path = candidate.resolve(strict=True)
        if not script_path.is_relative_to(root_resolved):
            return 0
    except (OSError, ValueError):
        return 0

    # Use the interpreter that ran this wrapper so we inherit the correct
    # Python across macOS/Linux/Windows without relying on PATH.
    cmd = [sys.executable, str(script_path), *script_args]

    # Consent gate: skip data collection until acknowledged.
    # EXEMPT: ensure-health and consent commands bootstrap the consent flag itself.
    # Blocking them creates a deadlock (config.json exists without flags -> ensure-health
    # can't run -> flags never written -> plugin permanently inert).
    exempt_commands = {"ensure-health", "consent", "v5"}
    is_exempt = any(arg in exempt_commands for arg in script_args[:2])
    if not is_exempt and not _check_consent():
        return 0

    # Force UTF-8 in every dispatched script regardless of the host locale, so
    # non-ASCII session paths / transcript content (Hebrew, CJK, accented names)
    # never crash a hook with UnicodeDecode/EncodeError. PYTHONUTF8 also makes the
    # child's default open() encoding UTF-8; PYTHONIOENCODING covers its std streams.
    child_env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}

    proc = None
    try:
        proc = subprocess.Popen(cmd, env=child_env)
        try:
            proc.wait(timeout=120)
        except subprocess.TimeoutExpired:
            # Important: Popen.wait doesn't auto-kill on timeout. Leaving
            # the child alive would leak a process holding the trends.db
            # SQLite lock, starving the next hook invocation.
            try:
                # Guard: check if process already exited between TimeoutExpired
                # and this point — avoids killing a reused PID on some POSIX impl.
                if proc.poll() is None:
                    proc.kill()
                proc.wait(timeout=5)
            except (subprocess.SubprocessError, OSError):
                pass
    except (subprocess.SubprocessError, OSError):
        if proc is not None:
            try:
                proc.kill()
            except (subprocess.SubprocessError, OSError):
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
