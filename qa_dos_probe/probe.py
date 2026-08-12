"""DoS probe: count _read_checkpoint_sidecar calls during the new-session
relevance-scoring loop in compact_restore.

Mirrors the caller at measure.py:28540-28548:
    for cp in candidates:
        s = checkpoint_relevance_score(opening_ctx, cp["path"], pool=candidates, cwd=cur)
where pool = ALL candidates (n). We build n synthetic checkpoint sidecars in a
temp dir and score each candidate against the full pool, exactly as the hook does.
"""
import json
import os
import sys
import time
import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "plugins" / "token-optimizer" / "skills" / "token-optimizer" / "scripts"
MEASURE = SCRIPTS / "measure.py"
sys.path.insert(0, str(SCRIPTS))

# Load measure.py as a module (it has an __main__ guard, so import is safe).
spec = importlib.util.spec_from_file_location("measure", str(MEASURE))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

# --- Instrument _read_checkpoint_sidecar with a call counter ---
_read_counter = {"n": 0, "paths": []}
_orig_read = m._read_checkpoint_sidecar


def _counting_read(checkpoint_path):
    _read_counter["n"] += 1
    _read_counter["paths"].append(str(checkpoint_path))
    return _orig_read(checkpoint_path)


m._read_checkpoint_sidecar = _counting_read


def make_fixtures(tmpdir, n):
    """Create n fake checkpoints: a .md stub + a .json sidecar each."""
    paths = []
    projects = ["gambit", "attention", "optimizer", "acme", "frobnicate",
                "widget", "deepcore", "nimbus", "titan", "helios"]
    for i in range(n):
        proj = projects[i % len(projects)]
        stem = f"11111111-2222-3333-4444-{i:012d}-20260101-120000-auto"
        md = tmpdir / f"{stem}.md"
        js = tmpdir / f"{stem}.json"
        md.write_text("# Session State Checkpoint\nGenerated: 2026-01-01\nbody\n", encoding="utf-8")
        sidecar = {
            "active_task": f"continue the {proj} refactor and wire the monitor",
            "topic": f"{proj} competitor analysis and dashboard",
            "decisions": [f"switch {proj} to async io", f"cap {proj} batch at 64"],
            "active_plan": f"- land {proj} v2\n- ship {proj} metrics",
            "modified_files": [
                {"path": f"/Users/x/work/{proj}/src/{proj}_core.py"},
                {"path": f"/Users/x/work/{proj}/tests/test_{proj}.py"},
                {"path": f"/Users/x/work/{proj}/README.md"},
            ],
            "recent_reads": [
                f"/Users/x/work/{proj}/docs/{proj}.md",
                f"/Users/x/work/{proj}/src/{proj}_core.py",
            ],
        }
        js.write_text(json.dumps(sidecar), encoding="utf-8")
        paths.append(md)
    return paths


def run(n):
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)
        paths = make_fixtures(tmpdir, n)
        candidates = [{"path": p} for p in paths]
        opening_ctx = "continue the gambit work from last session"
        cwd = "/Users/x/work/gambit"

        _read_counter["n"] = 0
        _read_counter["paths"] = []
        t0 = time.perf_counter()
        for cp in candidates:
            try:
                m.checkpoint_relevance_score(
                    opening_ctx, str(cp["path"]), pool=candidates, cwd=cwd)
            except Exception as e:
                pass
        wall = time.perf_counter() - t0
        reads = _read_counter["n"]
    return reads, wall


if __name__ == "__main__":
    print(f"{'N':>6} {'reads':>10} {'wall_s':>10} {'reads/N^2':>10} {'reads/N':>10}")
    results = []
    for n in [10, 50, 100, 200]:
        reads, wall = run(n)
        ratio = reads / (n * n) if n else 0
        per_n = reads / n if n else 0
        results.append((n, reads, wall))
        print(f"{n:>6} {reads:>10} {wall:>10.4f} {ratio:>10.4f} {per_n:>10.2f}")
    # Extrapolate
    print("\nExtrapolation (reads = a*N^2 + b*N):")
    # Fit quadratic through origin-ish: use largest point to estimate leading coeff
    n_big, r_big, _ = results[-1]
    a = r_big / (n_big * n_big)
    for n in [500, 1000]:
        est = a * n * n
        print(f"  N={n}: ~{est:.0f} sidecar reads")
