#!/usr/bin/env python3
"""Cross-platform behavioral contract for hook deadlines and lease locks."""

from __future__ import annotations

import importlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "token-optimizer" / "scripts"
MEASURE = SCRIPTS / "measure.py"
sys.path.insert(0, str(SCRIPTS))

from hook_runtime import HookDeadline, LeaseLock  # noqa: E402


def _python(code, *, timeout=3):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SCRIPTS)
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )
    return result, time.monotonic() - started


@pytest.mark.parametrize("blocker", ["pipe", "socket", "subprocess"])
def test_deadline_hard_exits_blocking_operations_without_platform_guards(blocker):
    setup = {
        "pipe": "r, w = os.pipe(); os.read(r, 1)",
        "socket": "a, b = socket.socketpair(); a.recv(1)",
        "subprocess": (
            "subprocess.Popen([sys.executable, '-c', "
            "'import time; time.sleep(1.5)'], stdout=subprocess.DEVNULL, "
            "stderr=subprocess.DEVNULL).wait()"
        ),
    }[blocker]
    code = f"""
import os, signal, socket, subprocess, sys
sys.modules["fcntl"] = None
if hasattr(signal, "SIGALRM"):
    del signal.SIGALRM
from hook_runtime import HookDeadline
HookDeadline(0.15).start()
{setup}
"""
    result, elapsed = _python(code)
    assert (
        result.returncode == 0
        and 0.08 <= elapsed < 0.9
        and "hook budget exceeded" in result.stderr
    )


def test_normal_completion_cancels_watchdog_without_late_exit():
    code = """
import time
from hook_runtime import HookDeadline
deadline = HookDeadline(0.1).start()
deadline.cancel()
time.sleep(0.2)
print("survived")
"""
    result, _elapsed = _python(code)
    assert result.returncode == 0 and result.stdout.strip() == "survived"


def test_live_pid_does_not_prevent_expired_lease_recovery(tmp_path):
    path = tmp_path / "state.lease"
    now = time.time()
    path.write_text(json.dumps({
        "pid": os.getpid(),
        "nonce": "expired-owner",
        "created_wall": now - 2,
        "expires_wall": now - 1,
    }))
    contender = LeaseLock(path, acquire_timeout=0, reclaim_grace=0)
    acquired = contender.acquire()
    contender.release()
    assert acquired and not path.exists()


def test_dead_pid_does_not_make_an_unexpired_lease_reclaimable(tmp_path):
    path = tmp_path / "state.lease"
    now = time.time()
    record = {
        "pid": 999_999_999,
        "nonce": "unexpired-owner",
        "created_wall": now,
        "expires_wall": now + 30,
    }
    path.write_text(json.dumps(record))
    acquired = LeaseLock(path, acquire_timeout=0, reclaim_grace=0).acquire()
    assert not acquired and json.loads(path.read_text())["nonce"] == record["nonce"]


@pytest.mark.parametrize(
    "record",
    [
        "{not-json",
        json.dumps({
            "pid": 1,
            "nonce": "future-owner",
            "created_wall": time.time() + 60,
            "expires_wall": time.time() + 61,
        }),
    ],
)
def test_unassessable_lease_metadata_fails_open_without_reclamation(tmp_path, record):
    path = tmp_path / "state.lease"
    path.write_text(record)
    acquired = LeaseLock(path, acquire_timeout=0, reclaim_grace=0).acquire()
    assert not acquired and path.exists() and path.read_text() == record


def test_release_never_unlinks_a_different_nonce(tmp_path):
    path = tmp_path / "state.lease"
    owner = LeaseLock(path, acquire_timeout=0)
    acquired = owner.acquire()
    replacement = {
        "pid": os.getpid(),
        "nonce": "replacement-owner",
        "created_wall": time.time(),
        "expires_wall": time.time() + 30,
    }
    path.write_text(json.dumps(replacement))
    owner.release()
    assert (
        acquired
        and path.exists()
        and json.loads(path.read_text())["nonce"] == "replacement-owner"
    )


def test_many_contenders_have_one_winner_and_bounded_losers(tmp_path):
    lock_path = tmp_path / "race.lease"
    wins_path = tmp_path / "wins.txt"
    code = """
import sys, time
from hook_runtime import LeaseLock
lock = LeaseLock(sys.argv[1], acquire_timeout=0.075)
if lock.acquire():
    with open(sys.argv[2], "a", encoding="utf-8") as out:
        out.write("won\\n")
    time.sleep(0.2)
    lock.release()
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SCRIPTS)
    started = time.monotonic()
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", code, str(lock_path), str(wins_path)],
            env=env,
        )
        for _ in range(12)
    ]
    codes = [process.wait(timeout=2) for process in processes]
    elapsed = time.monotonic() - started
    wins = wins_path.read_text().splitlines()
    assert codes == [0] * 12 and wins == ["won"] and elapsed < 1.2


def test_owner_hard_exit_recovers_only_after_lease_expiry(tmp_path):
    path = tmp_path / "orphan.lease"
    code = """
import os, sys
from hook_runtime import LeaseLock
lock = LeaseLock(sys.argv[1], acquire_timeout=0, lease_seconds=0.25, reclaim_grace=0)
if not lock.acquire():
    raise SystemExit(2)
os._exit(0)
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SCRIPTS)
    subprocess.run([sys.executable, "-c", code, str(path)], env=env, check=True)
    immediate = LeaseLock(path, acquire_timeout=0, reclaim_grace=0).acquire()
    time.sleep(0.3)
    recovered_lock = LeaseLock(path, acquire_timeout=0, reclaim_grace=0)
    recovered = recovered_lock.acquire()
    recovered_lock.release()
    assert immediate is False and recovered is True


@pytest.fixture()
def measure_module(monkeypatch, tmp_path):
    monkeypatch.setenv("TOKEN_OPTIMIZER_SNAPSHOT_DIR", str(tmp_path / "snapshots"))
    sys.modules.pop("measure", None)
    module = importlib.import_module("measure")
    module = importlib.reload(module)
    yield module
    sys.modules.pop("measure", None)


def test_windows_simulation_uses_portable_config_and_tripwire_leases(
    measure_module, monkeypatch, tmp_path
):
    module = measure_module
    monkeypatch.setattr(module, "_HAS_FCNTL", False)
    monkeypatch.setattr(module, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(module, "_CONFIG_LOCK_PATH", tmp_path / "config" / ".config.lock")
    monkeypatch.setattr(module, "SNAPSHOT_DIR", tmp_path / "snapshots")
    with module._config_lock() as config_acquired:
        pass
    with module._tripwire_lock() as tripwire_acquired:
        pass
    assert config_acquired is True and tripwire_acquired is True


def test_config_contention_is_bounded_and_skips_mutation(
    measure_module, monkeypatch, tmp_path
):
    module = measure_module
    config_dir = tmp_path / "config"
    config_path = config_dir / "config.json"
    lock_path = config_dir / ".config.lock"
    monkeypatch.setattr(module, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(module, "CONFIG_PATH", config_path)
    monkeypatch.setattr(module, "_CONFIG_LOCK_PATH", lock_path)
    holder = LeaseLock(lock_path.with_suffix(".lease"), acquire_timeout=0)
    holder_acquired = holder.acquire()
    started = time.monotonic()
    module._write_config_flag("must_not_write", True)
    elapsed = time.monotonic() - started
    holder.release()
    assert holder_acquired and elapsed < 0.25 and not config_path.exists()


def test_throttle_only_cache_miss_never_parses_transcript(
    measure_module, monkeypatch, tmp_path
):
    module = measure_module
    transcript = tmp_path / "session-1.jsonl"
    transcript.write_text('{"type":"user"}\n')
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(module, "QUALITY_CACHE_DIR", cache_dir)
    monkeypatch.setattr(
        module,
        "_parse_jsonl_for_quality",
        lambda _path: pytest.fail("cache miss fell through to transcript parsing"),
    )
    result = module.quality_cache(
        session_jsonl=str(transcript),
        pure_time_throttle=True,
        quiet=True,
    )
    assert result is None and not cache_dir.exists()


def test_throttle_only_cli_exits_before_reading_open_stdin(tmp_path):
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    process = subprocess.Popen(
        [
            sys.executable,
            str(MEASURE),
            "quality-cache",
            "--throttle-only",
            "--quiet",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
    )
    try:
        returncode = process.wait(timeout=1.5)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    assert returncode == 0


def test_all_three_user_prompt_handlers_install_and_clear_budgets():
    source = MEASURE.read_text(encoding="utf-8")
    names = ["prompt-continuity", "verbosity-steer", "quality-cache"]
    blocks = []
    for index, name in enumerate(names):
        start = source.index(f'elif args[0] == "{name}":')
        if index + 1 < len(names):
            end = source.index(f'elif args[0] == "{names[index + 1]}":', start)
        else:
            end = source.index('elif args[0] == "v5":', start)
        blocks.append(source[start:end])
    assert all(
        "_install_hook_budget(" in block and "_clear_hook_budget(" in block
        for block in blocks
    )
