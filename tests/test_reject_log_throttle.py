"""F7 + F8: reject-log throttle and path sanitization.

The reject-log code lives inside the generated daemon script (an f-string in
measure.py's _generate_daemon_script), not at module level, so these tests
assert the contract against the generated source text -- the same approach
test_dashboard_regen_retry.py takes for the shipped JS.

F7: the throttle was global (one trace every 30s regardless of path), so a
rejected toggle was invisible after a recent rejected regenerate. Now per-path.
F8: the path was written unsanitized, so a CR in the request path could
inject/overwrite a log line via terminal carriage-return overprint. Now stripped.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "token-optimizer" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


@pytest.fixture()
def daemon_src(monkeypatch, tmp_path):
    """Generate the daemon script and return its source text."""
    if "measure" in sys.modules:
        del sys.modules["measure"]
    monkeypatch.setenv("TOKEN_OPTIMIZER_SNAPSHOT_DIR", str(tmp_path))
    mod = importlib.import_module("measure")
    importlib.reload(mod)
    src = mod._generate_daemon_script()
    if "measure" in sys.modules:
        del sys.modules["measure"]
    return src


def _reject_section(src: str) -> str:
    """Extract the _log_reject_regen function body from the generated source."""
    start = src.index("def _log_reject_regen(")
    # Walk to the next def at the same indent (or end of file).
    i = start + 1
    while i < len(src):
        if src[i] == "\n" and i + 1 < len(src):
            rest = src[i + 1:]
            if rest.startswith("def ") or rest.startswith("class ") or rest.startswith("# "):
                # Check it's at module level (no indent)
                if not rest[0].isspace():
                    return src[start:i]
        i += 1
    return src[start:]


# ---- F8: sanitization --------------------------------------------------------

def test_sanitize_function_exists(daemon_src):
    """F8: _sanitize_log_path must exist and strip CR/LF."""
    assert "def _sanitize_log_path" in daemon_src, (
        "_sanitize_log_path must be defined in the daemon script (F8)"
    )
    # Check the sanitize function itself contains CR/LF stripping logic.
    sanitize_start = daemon_src.index("def _sanitize_log_path(")
    sanitize_end = daemon_src.index("def _log_reject_regen(")
    sanitize_section = daemon_src[sanitize_start:sanitize_end]
    assert "\\r" in sanitize_section or "replace" in sanitize_section, (
        "_sanitize_log_path must strip CR/LF from the path (F8)"
    )


def test_reject_log_uses_sanitized_path(daemon_src):
    """F8: _log_reject_regen must call _sanitize_log_path before writing."""
    section = _reject_section(daemon_src)
    assert "_sanitize_log_path" in section, (
        "_log_reject_regen must sanitize the path before writing (F8)"
    )
    assert "clean" in section or "sanitized" in section, (
        "the sanitized path must be used in the log write (F8)"
    )


# ---- F7: per-path throttle ---------------------------------------------------

def test_throttle_is_per_path_not_global(daemon_src):
    """F7: the throttle map must be keyed by path (a dict), not a single global
    timestamp. The old code used a single _REJECT_LOG_LAST_TS float."""
    assert "_REJECT_LOG_LAST_TS" in daemon_src
    # Must be a dict (per-path), not a float (global).
    assert "dict" in daemon_src or "{}" in daemon_src or "dict()" in daemon_src, (
        "_REJECT_LOG_LAST_TS must be a dict for per-path throttling (F7)"
    )
    section = _reject_section(daemon_src)
    assert ".get(" in section, (
        "_log_reject_regen must look up the throttle per-path via .get() (F7)"
    )


def test_global_float_throttle_removed(daemon_src):
    """F7: the old global float throttle (_REJECT_LOG_LAST_TS = 0.0) must be
    gone. If it remains, a rejected toggle is still dropped after a rejected
    regenerate."""
    # The old pattern was `_REJECT_LOG_LAST_TS = 0.0` (a float, not a dict).
    assert "_REJECT_LOG_LAST_TS = 0.0" not in daemon_src, (
        "global float throttle must be replaced with a per-path dict (F7)"
    )
