"""Issue #114 mechanism proxy: SIGKILL run.py, grandchild must EOF stdout.

On Windows the host TerminateProcess-es run.py (bypassing Python handlers)
and the in-process measure.py grandchild can hold the inherited stdout pipe
open forever. This POSIX test SIGKILLs run.py the same way and asserts the
read end sees EOF within the per-command budget + slack.

After Fix 3 the collect/dashboard dispatches and module_runner HookDeadline
close the pipe even when run.py never reaps the child.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RUN_PY = REPO / "hooks" / "run.py"
MEASURE_REL = "skills/token-optimizer/scripts/measure.py"

# collect/dashboard budget is 20s; slack for process startup + EOF delivery.
EOF_BUDGET_SECONDS = 28


@pytest.mark.skipif(sys.platform == "win32", reason="use taskkill /PID (no /T) on Windows")
def test_sigkill_run_py_collect_closes_stdout_pipe(tmp_path):
    plugin_root = REPO
    env = dict(os.environ)
    env["CLAUDE_PLUGIN_ROOT"] = str(plugin_root)
    env["CLAUDE_PLUGIN_DATA"] = str(tmp_path / "plugin-data")
    env["HOME"] = str(tmp_path / "home")
    env["PYTHONUTF8"] = "1"
    (tmp_path / "home").mkdir()
    (tmp_path / "plugin-data").mkdir()

    proc = subprocess.Popen(
        [sys.executable, str(RUN_PY), MEASURE_REL, "collect", "--quiet"],
        cwd=str(plugin_root),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
    )
    try:
        # Wait until the grandchild is actually running (stdout pipe exists).
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            try:
                os.kill(proc.pid, 0)
                time.sleep(0.15)
                if time.monotonic() - (deadline - 8) >= 0.3:
                    break
            except OSError:
                break

        os.kill(proc.pid, signal.SIGKILL)

        start = time.monotonic()
        leftover = proc.stdout.read()
        elapsed = time.monotonic() - start
        # A successful read-to-EOF means the pipe closed. A hang would trip
        # pytest's default timeout or this explicit bound.
        assert elapsed < EOF_BUDGET_SECONDS, (
            f"stdout pipe still open {elapsed:.1f}s after SIGKILL of run.py "
            f"(pid={proc.pid}); grandchild leaked the hook pipe"
        )
        assert leftover is not None
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
    finally:
        if proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass
        if proc.stdout:
            proc.stdout.close()
        if proc.stderr:
            proc.stderr.close()
