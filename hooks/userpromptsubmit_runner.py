#!/usr/bin/env python3
"""Single-import UserPromptSubmit dispatcher (issue #139).

Replaces the six separate ``UserPromptSubmit`` hooks.json entries that each
spawned ``python-launcher.sh -> run.py -> module_runner.py -> runpy(measure.py)``
(eighteen processes per prompt, with the 1.88 MB ``measure.py`` imported six
times). This runner is invoked ONCE per prompt, imports ``measure.py`` ONCE,
and runs all six subcommands in-process with per-subcommand failure isolation.

Behavior is byte-identical to the six-entry dispatch it replaces:
  - The three always-on subcommands (``quality-cache --warn --quiet``,
    ``prompt-continuity --quiet``, ``verbosity-steer --quiet``) run every prompt.
  - The three harness-only subcommands (``ensure-health --once-per-session``,
    ``quality-cache --force --quiet --once-per-session``,
    ``compact-restore --new-session-only --once-per-session``) run only when the
    harness guard passes (replicated from the shell prefix that used to gate
    entries 4/5/6), and each is latched by the SAME per-session marker
    ``measure._ran_once_this_session`` uses in the ``__main__`` dispatch.
  - Each subcommand installs/clears its own 8s wall-clock budget exactly as the
    ``__main__`` handler does, so a pathological hang still exits 0 (the
    ``HookDeadline`` watchdog calls ``os._exit(0)``) and never blocks the prompt.
  - One subcommand throwing/aborting never aborts the others (each is wrapped in
    ``_run_safely``); the hook always exits 0.
  - stdout is shared across subcommands (the inherited stream Claude Code reads
    for ``additionalContext`` injection), preserving the context-injection
    contract.

No ``measure.py`` edit: every call uses the real, verified module-level
entrypoints the ``__main__`` dispatch itself calls (signatures confirmed against
source before this file was written). The runner only re-orchestrates them.

Run: ``hooks/userpromptsubmit_runner.py`` (via run.py -> module_runner.py).
"""
from __future__ import annotations

import io
import json
import os
import sys
import traceback
from contextlib import redirect_stdout
from pathlib import Path


def _resolve_measure_dir() -> str:
    """Locate the directory holding measure.py so ``import measure`` works.

    module_runner.py puts THIS file's parent (``hooks/``) on ``sys.path[0]``;
    measure.py lives in ``skills/token-optimizer/scripts/``. Resolve it from
    ``CLAUDE_PLUGIN_ROOT`` (set by the host before hook invocation) with a
    ``__file__``-relative fallback (the plugin root is this file's
    grandparent), and insert it ahead of ``hooks/`` so measure.py and its
    sibling modules (runtime_env, plugin_env, hook_io, hook_runtime) resolve.
    """
    candidates: list[Path] = []
    pr = os.environ.get("CLAUDE_PLUGIN_ROOT", "").strip()
    if pr:
        candidates.append(Path(pr) / "skills" / "token-optimizer" / "scripts")
    try:
        candidates.append(
            Path(__file__).resolve().parent.parent
            / "skills" / "token-optimizer" / "scripts"
        )
    except Exception:
        pass
    for c in candidates:
        try:
            if (c / "measure.py").is_file():
                return str(c.resolve())
        except OSError:
            continue
    # Last resort: assume CWD-relative scripts layout (manual/dev invocation).
    return str((Path.cwd() / "skills" / "token-optimizer" / "scripts").resolve())


_MEASURE_DIR = _resolve_measure_dir()
if _MEASURE_DIR and _MEASURE_DIR not in sys.path:
    sys.path.insert(0, _MEASURE_DIR)

import measure  # noqa: E402  (path bootstrapped above)


def _read_hook_input() -> dict:
    """Read the hook stdin JSON once, non-blocking, shared across subcommands.

    Uses measure's own shared reader (Windows pipe-peek + Unix select) so the
    behavior matches what each ``__main__`` handler saw individually. 1 MB cap
    is the largest any of the six handlers reads (the quality-cache handler).
    """
    try:
        return measure._read_stdin_hook_input(max_bytes=1_000_000) or {}
    except Exception:
        return {}


def _harness_only_context() -> bool:
    """Replicate the shell harness guard that gated hooks.json entries 4/5/6.

    Original shell::

        [ -n "$CLAUDE_CODE_CONTAINER_ID$CLAUDE_CODE_REMOTE" ] ||
        case "$AI_AGENT$CLAUDE_PLUGIN_ROOT" in *harness*|*/plugins/synced/*) ;; *) exit 0;; esac

    i.e. run the harness-only subcommands when EITHER a container/remote env is
    set OR the combined AI_AGENT+CLAUDE_PLUGIN_ROOT string contains "harness" or
    "/plugins/synced/". Byte-identical to the shell semantics.
    """
    container_id = os.environ.get("CLAUDE_CODE_CONTAINER_ID", "").strip()
    remote = os.environ.get("CLAUDE_CODE_REMOTE", "").strip()
    if container_id or remote:
        return True
    combined = os.environ.get("AI_AGENT", "") + os.environ.get("CLAUDE_PLUGIN_ROOT", "")
    return ("harness" in combined) or ("/plugins/synced/" in combined)


def _run_safely(name: str, fn, *args, **kwargs) -> None:
    """Run fn, swallow any failure to stderr, never propagate.

    Catches ``Exception`` and ``SystemExit`` so one subcommand's bug or internal
    ``sys.exit()`` cannot abort the others. ``_HookTimeout`` (a ``BaseException``
    raised only by tests that inject it) is caught inside each subcommand
    function; in production the ``HookDeadline`` watchdog calls ``os._exit(0)``
    directly, which is uncatchable and correctly terminates the whole hook 0.
    """
    try:
        fn(*args, **kwargs)
    except (Exception, SystemExit):
        try:
            sys.stderr.write(f"[Token Optimizer] {name} failed, continuing\n")
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
        except (OSError, ValueError):
            pass


# --------------------------------------------------------------------------- #
# Subcommand handlers — each mirrors its measure.py __main__ dispatch block.
# --------------------------------------------------------------------------- #


def _sub_quality_cache_warn(hook_input: dict) -> None:
    """``quality-cache --warn --quiet`` (always runs). Mirrors __main__ L40696."""
    quiet = True
    warn = True
    force = False
    throttle_only = False
    throttle = 120
    warn_threshold = 70
    session_jsonl = hook_input.get("transcript_path")
    session_id = hook_input.get("session_id")
    deadline = measure._install_hook_budget(8)
    try:
        try:
            measure._daemon_midsession_pulse()
        except Exception:
            pass
        _quality_cache_self_heal()
        measure.quality_cache(
            throttle_seconds=throttle,
            warn_threshold=warn_threshold,
            quiet=quiet,
            session_jsonl=session_jsonl,
            force=force,
            pure_time_throttle=throttle_only,
            session_id=session_id,
            warn=warn,
        )
    except measure._HookTimeout:
        sys.stderr.write(
            "[Token Optimizer] hook budget exceeded; skipping quality-cache tick "
            "to keep session responsive\n"
        )
    finally:
        measure._clear_hook_budget(deadline)


def _sub_prompt_continuity(hook_input: dict) -> None:
    """``prompt-continuity --quiet`` (always runs). Mirrors __main__ L40602."""
    prompt_text = (
        hook_input.get("prompt")
        or hook_input.get("current_prompt")
        or hook_input.get("user_prompt")
        or ""
    )
    sid = hook_input.get("session_id")
    cwd = hook_input.get("cwd")
    transcript_path = hook_input.get("transcript_path")
    if not cwd and transcript_path:
        try:
            cwd = str(Path(transcript_path).parent)
        except TypeError:
            cwd = None
    deadline = measure._install_hook_budget(8)
    try:
        hint = ""
        try:
            hint = measure._continuity_prompt_hint(
                prompt_text=prompt_text, session_id=sid, cwd=cwd
            )
        except Exception:
            hint = ""
        hint = (hint or "").strip()
        if hint:
            print(
                json.dumps(
                    {
                        "continue": True,
                        "hookSpecificOutput": {
                            "hookEventName": "UserPromptSubmit",
                            "additionalContext": hint,
                        },
                    }
                )
            )
    except measure._HookTimeout:
        pass
    finally:
        measure._clear_hook_budget(deadline)


def _sub_verbosity_steer(hook_input: dict) -> None:
    """``verbosity-steer --quiet`` (always runs). Mirrors __main__ L40642.

    Note: the __main__ dispatch hardcodes ``quiet=False`` (the ``--quiet`` CLI
    flag is not parsed for this subcommand). Match the REAL call shape, not the
    flag name.
    """
    transcript_path = hook_input.get("transcript_path")
    session_id = hook_input.get("session_id")
    deadline = measure._install_hook_budget(8)
    try:
        payload = measure.run_verbosity_steer(
            transcript_path=transcript_path,
            quiet=False,
            session_id=session_id,
        )
        if payload:
            print(payload)
    except measure._HookTimeout:
        pass
    except Exception:
        pass
    finally:
        measure._clear_hook_budget(deadline)


def _sub_ensure_health(hook_input: dict) -> None:
    """``ensure-health --once-per-session`` (harness-gated). Mirrors __main__ L41138."""
    sid = hook_input.get("session_id")
    if measure._ran_once_this_session("ensure-health", sid):
        return
    # FIX C: the daemon ensure/revive runs FIRST, under its own short guard,
    # BEFORE the 8s health budget is armed (mirrors __main__ L41167).
    measure._ensure_health_daemon_revive_first()
    deadline = measure._install_hook_budget(8)
    try:
        measure.run_ensure_health()
    except measure._HookTimeout:
        sys.stderr.write(
            "[Token Optimizer] hook budget exceeded; skipping ensure-health tick "
            "to keep session responsive\n"
        )
    finally:
        measure._clear_hook_budget(deadline)


def _sub_quality_cache_force(hook_input: dict) -> None:
    """``quality-cache --force --quiet --once-per-session`` (harness-gated).

    Mirrors __main__ L40696 with --force --quiet --once-per-session: the daemon
    pulse + self-heal run unconditionally (as in the dispatch), THEN the
    once-per-session gate, THEN quality_cache() with force=True.
    """
    quiet = True
    warn = False
    force = True
    throttle_only = False
    throttle = 120
    warn_threshold = 70
    session_jsonl = hook_input.get("transcript_path")
    session_id = hook_input.get("session_id")
    deadline = measure._install_hook_budget(8)
    try:
        try:
            measure._daemon_midsession_pulse()
        except Exception:
            pass
        _quality_cache_self_heal()
        if measure._ran_once_this_session("quality-cache-force", session_id):
            return
        measure.quality_cache(
            throttle_seconds=throttle,
            warn_threshold=warn_threshold,
            quiet=quiet,
            session_jsonl=session_jsonl,
            force=force,
            pure_time_throttle=throttle_only,
            session_id=session_id,
            warn=warn,
        )
    except measure._HookTimeout:
        sys.stderr.write(
            "[Token Optimizer] hook budget exceeded; skipping quality-cache tick "
            "to keep session responsive\n"
        )
    finally:
        measure._clear_hook_budget(deadline)


def _sub_compact_restore(hook_input: dict) -> None:
    """``compact-restore --new-session-only --once-per-session`` (harness-gated).

    Mirrors __main__ L40526: the --new-session-only --once-per-session copy
    checks-then-skips the marker, then runs compact_restore(new_session_only).
    Under Codex/Cowork the raw stdout is captured and wrapped in the documented
    additionalContext envelope (issue #81 / docs-grounding.md §1).
    """
    sid = hook_input.get("session_id")
    if measure._ran_once_this_session("compact-restore-new-session", sid):
        return
    deadline = measure._install_hook_budget(
        measure._int_env("TOKEN_OPTIMIZER_COMPACT_RESTORE_BUDGET", 8)
        if hasattr(measure, "_int_env")
        else 8
    )
    try:
        _cw = measure.is_cowork()
        if measure.detect_runtime() == "codex" or _cw:
            buf = io.StringIO()
            with redirect_stdout(buf):
                measure.compact_restore(session_id=sid, new_session_only=True)
            measure._emit_additional_context(
                buf.getvalue(), event="UserPromptSubmit" if _cw else "SessionStart"
            )
        else:
            measure.compact_restore(session_id=sid, new_session_only=True)
    except measure._HookTimeout:
        pass
    finally:
        measure._clear_hook_budget(deadline)


def _quality_cache_self_heal() -> None:
    """Replicate the quality-cache dispatch's self-healing block (__main__
    L40743-40755): if the quality-cache hook is missing from settings.json and
    this is NOT a plugin install and quality_bar_disabled is unset, reinstall it.

    For plugin installs (the hooks.json context this runner runs in) the
    ``_is_plugin`` check is True and the block is a no-op, exactly as in the
    dispatch. Replicated verbatim so non-plugin manual installs keep the same
    self-heal behavior. Fail-open: never raises.
    """
    try:
        _is_plugin = measure._is_running_from_plugin_cache() or measure._is_plugin_installed()
        try:
            _qb_disabled = False
            if measure.CONFIG_PATH.exists():
                _qb_cfg = json.loads(measure.CONFIG_PATH.read_text(encoding="utf-8"))
                _qb_disabled = _qb_cfg.get("quality_bar_disabled", False)
            if (
                not _is_plugin
                and not _qb_disabled
                and measure.SETTINGS_PATH.exists()
            ):
                _sh_settings = json.loads(measure.SETTINGS_PATH.read_text(encoding="utf-8"))
                _sh_hooks = _sh_settings.get("hooks", {}).get("UserPromptSubmit", [])
                if not any("quality-cache" in str(h) for h in _sh_hooks):
                    measure.setup_quality_bar(quiet=True)
        except Exception:
            pass
    except Exception:
        pass


def _check_consent() -> bool:
    """Consent gate for the consolidated runner, mirroring ``run._check_consent``.

    run.py exempts this script from its own consent gate (issue #139 P0 fix:
    the runner is dispatched with no distinguishing args, so the
    ``ensure-health`` exempt-command match never fires there; the runner
    contains the ensure-health bootstrap itself). The per-subcommand consent
    decision therefore lives HERE.

    Imports ``run._check_consent`` (the canonical check) so the logic never
    drifts between the two files. Fails open (True) if ``run.py`` cannot be
    imported -- matching run.py's own fail-open philosophy -- so a host where
    the hooks dir is not on ``sys.path`` never silently disables the plugin.
    """
    try:
        import run as _run_mod  # noqa: E402  (sibling hooks/run.py)
        return _run_mod._check_consent()
    except Exception:
        return True


def main() -> int:
    hook_input = _read_hook_input()

    # Consent gate (issue #139 P0 fix). Pre-consolidation, the six
    # UserPromptSubmit hooks.json entries each passed distinguishing args, so
    # the ensure-health entry was consent-exempt (it bootstraps the
    # v5_welcome_shown / enterprise_consent_shown flags) and the other five
    # were consent-gated (returned 0 when consent was False). The consolidated
    # runner is dispatched with no args, so run.py exempts the whole runner
    # path and delegates the per-subcommand decision here.
    #
    # When consent is False: ONLY ensure-health runs (it writes the consent
    # flags via _show_v5_welcome + the v5_welcome_shown write, the bootstrap),
    # and only when the harness guard passes. On Cowork (the no-SessionStart
    # host where this deadlock is fatal) the harness guard is True, so
    # ensure-health bootstraps. On native Claude Code, SessionStart already
    # bootstraps consent, so consent is True before UserPromptSubmit fires and
    # this branch is not reached. The other five subcommands skip, preserving
    # the original consent-gated semantics exactly. When consent is True: all
    # six run per their existing gates.
    if not _check_consent():
        if _harness_only_context():
            _run_safely("ensure-health", _sub_ensure_health, hook_input)
        return 0

    # 1-3: always-on subcommands. Ordering (issue #139 P2): the cheap,
    # user-visible subcommands (prompt-continuity, verbosity-steer) run BEFORE
    # the heavier quality-cache --warn. HookDeadline's os._exit(0) kills the
    # whole process uncatchably, so a hang in an early subcommand skips all
    # later ones; running the cheap user-visible work first minimizes what a
    # quality-cache hang can suppress. The harness-only 4-6 still run after the
    # gate (gating-order semantics preserved).
    _run_safely("prompt-continuity", _sub_prompt_continuity, hook_input)
    _run_safely("verbosity-steer", _sub_verbosity_steer, hook_input)
    _run_safely("quality-cache --warn", _sub_quality_cache_warn, hook_input)

    # 4-6: harness-only subcommands. The shell guard that used to prefix
    # hooks.json entries 4/5/6 is evaluated once here; when it fails, all three
    # are skipped exactly as the shell `exit 0` skipped each entry.
    if not _harness_only_context():
        return 0

    _run_safely("ensure-health", _sub_ensure_health, hook_input)
    _run_safely("quality-cache --force", _sub_quality_cache_force, hook_input)
    _run_safely("compact-restore", _sub_compact_restore, hook_input)

    return 0


if __name__ == "__main__":
    sys.exit(main())
