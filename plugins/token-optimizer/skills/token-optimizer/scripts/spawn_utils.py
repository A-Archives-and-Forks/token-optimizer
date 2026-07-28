"""Cross-platform detached-subprocess spawn helpers.

On POSIX, ``start_new_session=True`` detaches the child into its own process
group. On Windows, ``start_new_session`` is silently ignored, so the child
inherits the parent's console (causing a ~1s window flash on every prompt) and
dies with the parent's job object. The Windows fix mirrors the daemon-revive
spawn in measure.py (~line 21658, CXP-1): OR together DETACHED_PROCESS,
CREATE_NEW_PROCESS_GROUP, and CREATE_BREAKAWAY_FROM_JOB via getattr so the
flags degrade to 0 on builds where an attribute is missing.

Use ``detach_spawn_kwargs()`` for the raw kwargs dict, or ``spawn_detached()``
for fire-and-forget background spawns where the child must survive the parent
(session-end flush, daemon regen, rollup, etc.). ``spawn_detached`` retries
without ``CREATE_BREAKAWAY_FROM_JOB`` if ``CreateProcess`` fails with
``ACCESS_DENIED`` inside a restrictive Windows Job Object, then returns the
``Popen`` or ``None`` (never raises).

For spawns that must INHERIT the parent's stdio (hooks/run.py), do NOT use this
helper -- those need CREATE_NO_WINDOW, not DETACHED_PROCESS.
"""
from __future__ import annotations

import logging
import os
import subprocess

logger = logging.getLogger(__name__)


def detach_spawn_kwargs():
    """Return Popen kwargs that detach the child on the current OS.

    Spread into ``subprocess.Popen(..., **detach_spawn_kwargs())`` alongside
    the caller's own kwargs (stdout, stderr, env, etc.).

    POSIX:  ``{"start_new_session": True}``
    Windows: ``{"creationflags": DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
              | CREATE_BREAKAWAY_FROM_JOB}``

    The Windows flag OR uses ``getattr(subprocess, name, 0)`` so a flag
    absent on an older Python build contributes 0 instead of raising.
    """
    if os.name == "nt":
        flags = 0
        for _name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP",
                      "CREATE_BREAKAWAY_FROM_JOB"):
            flags |= getattr(subprocess, _name, 0)
        return {"creationflags": flags} if flags else {}
    return {"start_new_session": True}


def spawn_detached(argv, **popen_kwargs):
    """Fire-and-forget detached spawn. Returns the ``Popen`` or ``None``.

    Combines the caller's kwargs with ``detach_spawn_kwargs()`` and calls
    ``subprocess.Popen``. On Windows, if ``CreateProcess`` raises ``OSError``
    (e.g. ``ACCESS_DENIED`` when ``CREATE_BREAKAWAY_FROM_JOB`` is not allowed
    inside a restrictive parent Job Object), retries ONCE with
    ``creationflags`` minus ``CREATE_BREAKAWAY_FROM_JOB`` (i.e.
    ``DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`` only) and logs the
    fallback at warning level (no logging is configured in the runtime, so
    logger.debug would be dropped by lastResort at WARNING). Every caller
    already swallows ``OSError``, so without this retry the background worker
    would silently never spawn.

    Never raises: returns ``None`` on failure.
    """
    kwargs = dict(popen_kwargs)
    kwargs.update(detach_spawn_kwargs())
    try:
        return subprocess.Popen(argv, **kwargs)
    except OSError:
        if os.name != "nt":
            logger.warning("[spawn_utils] Popen failed (non-nt): %r", argv)
            return None
        flags = kwargs.get("creationflags", 0)
        breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
        if not (flags & breakaway):
            logger.warning("[spawn_utils] Popen failed, no breakaway to drop: %r", argv)
            return None
        kwargs["creationflags"] = flags & ~breakaway
        logger.warning(
            "[spawn_utils] retrying without CREATE_BREAKAWAY_FROM_JOB: %r", argv)
        try:
            return subprocess.Popen(argv, **kwargs)
        except OSError:
            logger.warning(
                "[spawn_utils] Popen retry without CREATE_BREAKAWAY_FROM_JOB "
                "also failed: %r", argv)
            return None
