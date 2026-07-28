"""Cross-platform detached-subprocess spawn helpers.

On POSIX, ``start_new_session=True`` detaches the child into its own process
group. On Windows, ``start_new_session`` is silently ignored, so the child
inherits the parent's console (causing a ~1s window flash on every prompt) and
dies with the parent's job object. The Windows fix mirrors the daemon-revive
spawn in measure.py (~line 21658, CXP-1): OR together DETACHED_PROCESS,
CREATE_NEW_PROCESS_GROUP, and CREATE_BREAKAWAY_FROM_JOB via getattr so the
flags degrade to 0 on builds where an attribute is missing.

Use ``detach_spawn_kwargs()`` for fire-and-forget background spawns where the
child must survive the parent (session-end flush, daemon regen, rollup, etc.).
For spawns that must INHERIT the parent's stdio (hooks/run.py), do NOT use this
helper -- those need CREATE_NO_WINDOW, not DETACHED_PROCESS.
"""
from __future__ import annotations

import os
import subprocess


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
