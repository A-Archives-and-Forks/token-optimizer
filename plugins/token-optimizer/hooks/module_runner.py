#!/usr/bin/env python3
"""Runs a hook script as a module instead of as `__main__`, so CPython reuses
its __pycache__ bytecode across invocations.

Script-mode execution (`python foo.py`) never checks or writes __pycache__ for
the file being run as __main__ -- only for things it imports. Since every hook
call is a fresh process, running measure.py (35k+ lines) as a script recompiles
it from source every single time: ~0.3s of pure CPython parse/compile on top of
whatever the hook actually does. Module-mode goes through the import system,
which does check/write __pycache__, cutting that to ~0.1s after the first call.

sys.path is stripped of '' and '.' (the cwd-equivalent entries the interpreter
would otherwise add) before inserting scripts_dir explicitly, so a same-named
file in the invoking project's own working directory (e.g. a project that
happens to have its own measure.py at its root) can never shadow the plugin's
module.
"""
from __future__ import annotations

import runpy
import sys


def main() -> int:
    if len(sys.argv) < 3:
        return 0

    scripts_dir = sys.argv[1]
    module_name = sys.argv[2]
    script_args = sys.argv[3:]

    sys.path = [p for p in sys.path if p not in ("", ".")]
    sys.path.insert(0, scripts_dir)
    sys.argv = [module_name, *script_args]

    runpy.run_module(module_name, run_name="__main__", alter_sys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
