"""Regression coverage for the documented local-data retention contract."""

from __future__ import annotations

import importlib.util
import os
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "token-optimizer" / "scripts"


def _load_archive_result(monkeypatch: pytest.MonkeyPatch, snapshot_dir: Path):
    monkeypatch.setenv("TOKEN_OPTIMIZER_SNAPSHOT_DIR", str(snapshot_dir))
    monkeypatch.setenv("TOKEN_OPTIMIZER_ARCHIVE_RETENTION_HOURS", "24")
    monkeypatch.setenv("TOKEN_OPTIMIZER_ARCHIVE_RETENTION_MAX_FILES", "2")
    monkeypatch.setenv("TOKEN_OPTIMIZER_ARCHIVE_RETENTION_MAX_BYTES", "1000000")
    monkeypatch.syspath_prepend(str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("archive_result", SCRIPTS / "archive_result.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "archive_result", module)
    spec.loader.exec_module(module)
    return module


def test_quality_cache_default_matches_seven_day_docs():
    source = (SCRIPTS / "measure.py").read_text(encoding="utf-8")
    assert (
        '_int_env("TOKEN_OPTIMIZER_QUALITY_CACHE_RETENTION_DAYS", 7)'
        in source
    )


def test_archive_cleanup_uses_env_and_enforces_file_cap(monkeypatch, tmp_path):
    module = _load_archive_result(monkeypatch, tmp_path)
    root = tmp_path / "tool-archive"
    now = time.time()
    for index in range(3):
        session = root / f"session-{index}"
        session.mkdir(parents=True)
        (session / "result.json").write_bytes(b"x" * 10)
        os.utime(session, (now - (30 - index), now - (30 - index)))

    removed = module.cleanup_old_archives()

    assert removed == 1
    assert len(list(root.glob("*/*.json"))) == 2
    assert not (root / "session-0").exists()


def test_archive_cleanup_enforces_total_byte_cap(monkeypatch, tmp_path):
    module = _load_archive_result(monkeypatch, tmp_path)
    monkeypatch.setenv("TOKEN_OPTIMIZER_ARCHIVE_RETENTION_MAX_FILES", "100")
    monkeypatch.setenv("TOKEN_OPTIMIZER_ARCHIVE_RETENTION_MAX_BYTES", "15")
    root = tmp_path / "tool-archive"
    now = time.time()
    for index in range(2):
        session = root / f"session-{index}"
        session.mkdir(parents=True)
        (session / "result.json").write_bytes(b"x" * 10)
        os.utime(session, (now - (30 - index), now - (30 - index)))

    module.cleanup_old_archives()

    remaining_bytes = sum(p.stat().st_size for p in root.glob("*/*") if p.is_file())
    assert remaining_bytes <= 15
    assert not (root / "session-0").exists()


def test_hot_path_and_session_end_both_run_configured_cleanup():
    archiver = (SCRIPTS / "archive_result.py").read_text(encoding="utf-8")
    measure = (SCRIPTS / "measure.py").read_text(encoding="utf-8")
    hot_path = archiver[archiver.index("def archive_result(") :]
    worker = measure[
        measure.index("def _run_session_end_flush_worker(") :
        measure.index("def _defer_session_end_flush(")
    ]

    assert "cleanup_old_archives(skip_session_id=session_id)" in hot_path
    assert "max_age_hours=48" not in hot_path
    assert "cleanup_old_archives()" in worker
