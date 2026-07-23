"""The `measure.py route` command returns a native recommendation per platform.

It must work on every runtime (not just Claude) and must never emit a very cheap
model for a significant task.

Run: python3 -m pytest tests/test_route_subcommand.py -v
"""

import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "token-optimizer" / "scripts"
MEASURE = SCRIPTS / "measure.py"


def _route(task, runtime=None, as_json=True):
    env = dict(os.environ)
    if runtime:
        env["TOKEN_OPTIMIZER_RUNTIME"] = runtime
    args = [sys.executable, str(MEASURE), "route", "--task", task]
    if as_json:
        args.append("--json")
    out = subprocess.run(args, capture_output=True, text=True, env=env, timeout=60)
    return out


def test_route_json_easy_task():
    out = _route("fix typo in README")
    line = [l for l in out.stdout.splitlines() if l.strip().startswith("{")]
    assert line, out.stdout
    data = json.loads(line[-1])
    assert data["significance"] == "easy"
    assert "model" in data and "effort" in data


def test_route_hard_task_not_cheap():
    out = _route("migrate the production auth database to a new schema")
    line = [l for l in out.stdout.splitlines() if l.strip().startswith("{")]
    data = json.loads(line[-1])
    assert data["significance"] != "easy"
    assert data["model"] not in ("haiku", "sol", "luna")
    assert data["effort"] not in ("minimal", "low")


def test_route_codex_uses_native_names():
    out = _route("migrate the production database", runtime="codex")
    line = [l for l in out.stdout.splitlines() if l.strip().startswith("{")]
    data = json.loads(line[-1])
    assert data["effort_knob"] == "model_reasoning_effort"
    assert data["model"].startswith("gpt-")


def test_route_positional_task_not_dropped():
    # Task passed positionally (no --task flag) must not be silently stripped.
    env = dict(os.environ)
    args = [sys.executable, str(MEASURE), "route", "--json",
            "migrate", "the", "production", "auth", "database", "schema"]
    out = subprocess.run(args, capture_output=True, text=True, env=env, timeout=60)
    line = [l for l in out.stdout.splitlines() if l.strip().startswith("{")]
    data = json.loads(line[-1])
    assert data["significance"] == "hard"
    assert data["model"] not in ("haiku", "sol", "luna")


def test_route_task_equals_form():
    # `--task=...` (equals form) must be recognized, not treated as literal text.
    env = dict(os.environ)
    args = [sys.executable, str(MEASURE), "route", "--json",
            "--task=migrate the production database"]
    out = subprocess.run(args, capture_output=True, text=True, env=env, timeout=60)
    line = [l for l in out.stdout.splitlines() if l.strip().startswith("{")]
    data = json.loads(line[-1])
    assert data["significance"] == "hard"


def test_route_text_output():
    out = _route("rename this variable", as_json=False)
    assert "model:" in out.stdout and "effort:" in out.stdout
    assert out.returncode == 0


def _route_env(task, env):
    args = [sys.executable, str(MEASURE), "route", "--task", task, "--json"]
    out = subprocess.run(args, capture_output=True, text=True, env=env, timeout=60)
    line = [l for l in out.stdout.splitlines() if l.strip().startswith("{")]
    assert line, out.stdout + out.stderr
    return json.loads(line[-1])


def test_opencode_reads_configured_models(tmp_path):
    # opencode ladder is resolved from opencode.json (model / small_model), not the table.
    cfg_dir = tmp_path / "opencode"
    cfg_dir.mkdir()
    (cfg_dir / "opencode.json").write_text(
        '{"model":"anthropic/claude-opus-4","small_model":"anthropic/claude-haiku-4"}',
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["TOKEN_OPTIMIZER_RUNTIME"] = "opencode"
    env["XDG_CONFIG_HOME"] = str(tmp_path)
    env.pop("OPENCODE_CONFIG_DIR", None)

    easy = _route_env("fix a typo", env)
    assert easy["model"] == "claude-haiku-4", easy          # small_model -> budget
    hard = _route_env("migrate the production auth database schema", env)
    assert hard["model"] == "claude-opus-4", hard           # model -> capable/frontier


def test_opencode_absent_config_falls_back_to_table(tmp_path):
    # No opencode.json under the config home -> table default (haiku), never a crash.
    env = dict(os.environ)
    env["TOKEN_OPTIMIZER_RUNTIME"] = "opencode"
    env["XDG_CONFIG_HOME"] = str(tmp_path)   # empty; no opencode/opencode.json
    env.pop("OPENCODE_CONFIG_DIR", None)
    easy = _route_env("fix a typo", env)
    assert easy["model"] == "haiku", easy
