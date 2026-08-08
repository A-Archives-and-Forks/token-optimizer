"""_context_window_for_model_str docstring must not overclaim.

The function resolves a context window for a specific model string, but
GLOBAL env overrides (CLAUDE_CODE_DISABLE_1M_CONTEXT,
TOKEN_OPTIMIZER_CONTEXT_SIZE, _cli_context_size) still take precedence over
the model-string tier. The earlier docstring claimed the function keyed to
"the model that actually produced the tokens, not an env/config global that
may name a different model entirely" -- that was an overclaim. These tests
pin the actual precedence so the behavior and the docstring stay in sync.
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
def m(monkeypatch):
    if "measure" in sys.modules:
        del sys.modules["measure"]
    monkeypatch.delenv("CLAUDE_CODE_DISABLE_1M_CONTEXT", raising=False)
    monkeypatch.delenv("TOKEN_OPTIMIZER_CONTEXT_SIZE", raising=False)
    mod = importlib.import_module("measure")
    importlib.reload(mod)
    monkeypatch.setattr(mod, "_cli_context_size", None)
    yield mod
    if "measure" in sys.modules:
        del sys.modules["measure"]


def test_model_string_1m_when_no_override(m):
    """A 1M variant resolves to 1M when no env override is set."""
    assert m._context_window_for_model_str("claude-opus-5[1m]") == 1_000_000


def test_model_string_200k_when_no_override(m):
    """A haiku model resolves to 200K when no override is set (the only 200k
    path via the model-string tier; all non-haiku models default to 1M)."""
    assert m._context_window_for_model_str("claude-haiku-4-5") == 200_000


def test_env_override_wins_over_model_string(m, monkeypatch):
    """TOKEN_OPTIMIZER_CONTEXT_SIZE is a GLOBAL override -- it wins even
    when the model string names a 1M variant. This is by design (explicit user
    intent), and the docstring now documents it instead of overclaiming."""
    monkeypatch.setenv("TOKEN_OPTIMIZER_CONTEXT_SIZE", "200000")
    assert m._context_window_for_model_str("claude-opus-5[1m]") == 200_000, (
        "global env override must win over the model string tier (F5)"
    )


def test_disable_1m_wins_over_everything(m, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_1M_CONTEXT", "1")
    monkeypatch.setenv("TOKEN_OPTIMIZER_CONTEXT_SIZE", "500000")
    assert m._context_window_for_model_str("claude-opus-5[1m]") == 200_000


def test_cli_context_size_wins_over_model_string(m, monkeypatch):
    monkeypatch.setattr(m, "_cli_context_size", 300_000)
    assert m._context_window_for_model_str("claude-opus-5[1m]") == 300_000


def test_none_for_empty_model(m):
    assert m._context_window_for_model_str("") is None
    assert m._context_window_for_model_str(None) is None
