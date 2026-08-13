#!/usr/bin/env python3
"""TO Cowork telemetry collector.

One process, five jobs:
  1. `/probe`            — receives to-hook-probe phone-home POSTs (the only
                           evidence channel for cloud sessions).
  2. `/v1/{logs,traces,metrics}` — receives Cowork's OTel OTLP/HTTP export
                           (org-admin points the endpoint here; Team/
                           Enterprise; http/protobuf or http/json — gRPC is
                           not supported for Cowork).
  3. `/healthz`          — liveness for cowork_doctor.py.
  4. `--summarize`       — smoke-test: sum api_request token fields from the
                           captured bodies to confirm telemetry is flowing.
  5. `--ingest`          — parse captured api_request/tool_result events into
                           per-session rows and write them into Token
                           Optimizer's trends.db (platform='cowork'), reusing
                           measure.py's own schema helpers so Cowork sessions
                           render in the same trends/dashboard as Claude Code.
     `--cost-view`       — read-only cross-agent spend view: total tokens and
                           USD grouped by host (claude-code / codex / cowork /
                           hermes / copilot) from the same trends store.

Ingestion notes:
  - Dedup key mirrors the hermes:/copilot: pattern: jsonl_path =
    "cowork:<session.id>", platform = "cowork".
  - Upsert (not INSERT OR IGNORE): OTel capture accumulates while a session
    runs, so re-ingest refreshes totals. Captured files are append-only, so
    totals are monotonic.
  - Only log EVENTS are parsed (api_request, tool_result). OTLP metrics
    counters carry the same token totals and would double-count.
  - Protobuf OTLP bodies are stored base64-raw, not decoded (decoding needs
    the otlp proto schema or a dependency); both summarize and ingest skip
    them and report the skip count.

Run the server on a host reachable from Cowork VMs/cloud, behind HTTPS
(cloud sessions cannot reach a laptop's localhost; the domain must be on
Cowork's allowlist):
    python3 to_collector.py --host 0.0.0.0 --port 4318

Then, on the machine where Token Optimizer lives:
    python3 to_collector.py --ingest
    python3 to_collector.py --cost-view --days 30
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

MAX_BODY = 5 * 1024 * 1024  # cap a single POST at 5MB; fail-open beyond


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class CollectorHandler(BaseHTTPRequestHandler):
    data_dir: Path  # set by serve()

    def log_message(self, fmt: str, *args: Any) -> None:  # quiet by default
        pass

    def _reply(self, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        if self.path.rstrip("/") in ("", "/healthz"):
            self._reply(200, {"ok": True, "service": "to-cowork-collector", "time": _now()})
        else:
            self._reply(404, {"ok": False})

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        length = min(int(self.headers.get("Content-Length", 0) or 0), MAX_BODY)
        body = self.rfile.read(length) if length else b""
        content_type = self.headers.get("Content-Type", "")
        record: dict[str, Any] = {
            "ts": _now(),
            "path": self.path,
            "content_type": content_type,
            "remote": self.client_address[0],
            "headers": {k: v for k, v in self.headers.items() if k.lower().startswith("x-to-")},
        }
        try:
            record["body"] = json.loads(body.decode("utf-8"))
            kind = "json"
        except (UnicodeDecodeError, json.JSONDecodeError):
            try:
                record["body_text"] = body.decode("utf-8")
                kind = "text"
            except UnicodeDecodeError:
                record["body_b64"] = base64.b64encode(body).decode("ascii")
                kind = "binary"
        record["kind"] = kind

        if self.path.startswith("/probe"):
            out = self.data_dir / "probe.jsonl"
        elif self.path.startswith("/v1/"):
            out = self.data_dir / f"otlp-{self.path.split('/')[2]}.jsonl"
        else:
            out = self.data_dir / "other.jsonl"
        try:
            with out.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
        except OSError as exc:
            self._reply(500, {"ok": False, "error": str(exc)})
            return
        self._reply(200, {"ok": True})


def _walk(node: Any):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def _attr_map(attrs: Any) -> dict[str, Any]:
    """Flatten an OTLP attributes list [{key, value:{...Value}}] to a dict."""
    out: dict[str, Any] = {}
    if isinstance(attrs, list):
        for a in attrs:
            if isinstance(a, dict) and "key" in a:
                value = a.get("value")
                if isinstance(value, dict):
                    value = next(iter(value.values()), None)
                out[a["key"]] = value
    return out


def _iter_events(data_dir: Path):
    """Yield (merged_event_dict, receipt_ts_iso) for every dict node in the
    captured OTLP log bodies, with OTLP attribute lists flattened in. Tolerant
    of both flat events and nested resourceLogs/scopeLogs/logRecords shapes.
    Counts (but skips) undecodable protobuf records via the returned tally."""
    tally = {"binary_skipped": 0, "files": []}

    def gen():
        for path in sorted(data_dir.glob("otlp-logs*.jsonl")):
            tally["files"].append(path.name)
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("kind") == "binary":
                    tally["binary_skipped"] += 1
                    continue
                receipt_ts = record.get("ts")
                for node in _walk(record.get("body")):
                    attrs = _attr_map(node.get("attributes"))
                    merged = {**{k: v for k, v in node.items() if not isinstance(v, (dict, list))}, **attrs}
                    yield merged, receipt_ts

    return gen(), tally


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _event_time(merged: dict[str, Any], receipt_ts: Any) -> datetime | None:
    """Best-effort event timestamp: event.timestamp attr (ISO) →
    timeUnixNano/observedTimeUnixNano → collector receipt time."""
    iso = merged.get("event.timestamp") or merged.get("timestamp")
    if iso:
        try:
            return datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        except ValueError:
            pass
    for key in ("timeUnixNano", "time_unix_nano", "observedTimeUnixNano", "observed_time_unix_nano"):
        raw = merged.get(key)
        if raw:
            try:
                nanos = int(raw)
                if nanos > 0:
                    return datetime.fromtimestamp(nanos / 1e9, tz=timezone.utc)
            except (TypeError, ValueError, OSError, OverflowError):
                pass
    if receipt_ts:
        try:
            return datetime.fromisoformat(str(receipt_ts).replace("Z", "+00:00"))
        except ValueError:
            pass
    return None


def parse_cowork_sessions(data_dir: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Group captured api_request/tool_result events per session.id.

    Returns ({session_id: session_dict}, stats). Events without a session.id
    cannot be keyed into trends and are counted in stats["no_session_id"].
    """
    events, tally = _iter_events(data_dir)
    sessions: dict[str, dict[str, Any]] = {}
    stats = {"api_request_events": 0, "tool_result_events": 0, "no_session_id": 0}

    for merged, receipt_ts in events:
        name = str(merged.get("event.name") or merged.get("name") or "")
        is_api = "api_request" in name
        is_tool = "tool_result" in name
        if not (is_api or is_tool):
            continue
        sid = merged.get("session.id") or merged.get("session_id")
        if not sid:
            stats["no_session_id"] += 1
            continue
        sid = str(sid)
        s = sessions.setdefault(sid, {
            "session_id": sid,
            "api_calls": 0,
            "breakdown": {},   # model -> {fresh_input, cache_read, cache_create, output}
            "tool_calls": {},  # tool_name -> count
            "model_events": {},  # model -> api_request event count
            "reported_cost_usd": 0.0,
            "first_ts": None,
            "last_ts": None,
        })
        ts = _event_time(merged, receipt_ts)
        if ts is not None:
            if s["first_ts"] is None or ts < s["first_ts"]:
                s["first_ts"] = ts
            if s["last_ts"] is None or ts > s["last_ts"]:
                s["last_ts"] = ts
        if is_api:
            stats["api_request_events"] += 1
            s["api_calls"] += 1
            model = str(merged.get("model") or "unknown")
            bd = s["breakdown"].setdefault(
                model, {"fresh_input": 0, "cache_read": 0, "cache_create": 0, "output": 0}
            )
            s["model_events"][model] = s["model_events"].get(model, 0) + 1
            bd["fresh_input"] += _int(merged.get("input_tokens"))
            bd["output"] += _int(merged.get("output_tokens"))
            bd["cache_read"] += _int(merged.get("cache_read_tokens"))
            bd["cache_create"] += _int(merged.get("cache_creation_tokens"))
            try:
                s["reported_cost_usd"] += float(merged.get("cost_usd") or 0)
            except (TypeError, ValueError):
                pass
        else:
            stats["tool_result_events"] += 1
            tool = str(merged.get("tool_name") or merged.get("name") or "unknown")
            s["tool_calls"][tool] = s["tool_calls"].get(tool, 0) + 1

    stats.update(tally)
    return sessions, stats


def summarize(data_dir: Path) -> dict[str, Any]:
    """Smoke-test summary: pull api_request token fields out of captured
    OTLP JSON bodies. Protobuf (binary) records are counted but not decoded."""
    sessions, stats = parse_cowork_sessions(data_dir)
    totals = {"api_request_events": stats["api_request_events"], "input_tokens": 0,
              "output_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0}
    models: dict[str, int] = {}  # model -> api_request event count
    for s in sessions.values():
        for model, bd in s["breakdown"].items():
            totals["input_tokens"] += bd["fresh_input"]
            totals["output_tokens"] += bd["output"]
            totals["cache_read_tokens"] += bd["cache_read"]
            totals["cache_creation_tokens"] += bd["cache_create"]
            models[model] = models.get(model, 0) + s["model_events"].get(model, 0)
    probe = data_dir / "probe.jsonl"
    probe_posts = sum(1 for _ in probe.open(encoding="utf-8")) if probe.exists() else 0
    return {"data_dir": str(data_dir), "otlp_files": stats.get("files", []),
            "probe_posts": probe_posts,
            "binary_records_skipped": stats.get("binary_skipped", 0),
            "events_without_session_id": stats["no_session_id"],
            "totals": totals, "models": models, "sessions": len(sessions)}


# ---------------------------------------------------------------------------
# trends.db ingestion — reuses measure.py's schema helpers (no second schema)
# ---------------------------------------------------------------------------

def _measure_candidates() -> list[Path]:
    """Where measure.py can live, in preference order."""
    cands: list[Path] = []
    env = os.environ.get("TOKEN_OPTIMIZER_MEASURE_PATH")
    if env:
        cands.append(Path(env).expanduser())
    # Repo layout: <repo>/cowork/collector/to_collector.py → <repo>/skills/...
    here = Path(__file__).resolve()
    cands.append(here.parents[2] / "skills" / "token-optimizer" / "scripts" / "measure.py")
    # Installed-plugin layouts (marketplace + custom-plugin upload)
    plugins = Path.home() / ".claude" / "plugins"
    for pattern in (
        "*/skills/token-optimizer/scripts/measure.py",
        "*/*/skills/token-optimizer/scripts/measure.py",
        "*/*/*/skills/token-optimizer/scripts/measure.py",
    ):
        cands.extend(sorted(plugins.glob(pattern)))
    return cands


def _load_measure(measure_path: str | None):
    """Import measure.py as a module, or return (None, tried_paths)."""
    cands = [Path(measure_path).expanduser()] if measure_path else _measure_candidates()
    for cand in cands:
        if not cand.is_file():
            continue
        # measure.py imports siblings (hook_io, codex_session, ...) bare, so its
        # directory must be importable.
        script_dir = str(cand.parent)
        added = script_dir not in sys.path
        if added:
            sys.path.insert(0, script_dir)
        try:
            spec = importlib.util.spec_from_file_location("to_measure", str(cand))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod, cands
        except Exception as exc:  # noqa: BLE001 - report and try the next candidate
            print(f"[to-collector] failed to import {cand}: {exc}", file=sys.stderr)
            if added:
                sys.path.remove(script_dir)
    return None, cands


def ingest(data_dir: Path, measure_path: str | None = None, db_override: str | None = None,
           quiet: bool = False) -> int:
    """Write parsed Cowork sessions into TO's trends.db via measure.py helpers.

    Upserts one session_log row per Cowork session (platform='cowork',
    jsonl_path='cowork:<session.id>'), then rebuilds the daily aggregate
    tables. Returns a process exit code.
    """
    measure, tried = _load_measure(measure_path)
    if measure is None:
        print("[to-collector] measure.py not found — cannot ingest without the "
              "canonical trends schema. Tried:", file=sys.stderr)
        for c in tried:
            print(f"  - {c}", file=sys.stderr)
        print("Set TOKEN_OPTIMIZER_MEASURE_PATH or pass --measure-path.", file=sys.stderr)
        return 1

    if db_override:
        # Test hook: point measure's module constants at a scratch DB before
        # _init_trends_db() touches the real one.
        db = Path(db_override).expanduser()
        measure.TRENDS_DB = db
        measure.SNAPSHOT_DIR = db.parent

    sessions, stats = parse_cowork_sessions(data_dir)
    if not sessions:
        if not quiet:
            print(f"[to-collector] no Cowork sessions found in {data_dir} "
                  f"(binary records skipped: {stats.get('binary_skipped', 0)}, "
                  f"events without session.id: {stats['no_session_id']}). "
                  "If the org exports metrics-only OTel, enable log events — "
                  "ingestion reads api_request events.")
        return 0

    conn = measure._init_trends_db()
    written = 0
    try:
        for sid, s in sorted(sessions.items()):
            bd = s["breakdown"]
            fresh = sum(m["fresh_input"] for m in bd.values())
            cache_read = sum(m["cache_read"] for m in bd.values())
            cache_create = sum(m["cache_create"] for m in bd.values())
            output = sum(m["output"] for m in bd.values())
            full_input = fresh + cache_read + cache_create
            cache_hit_rate = (cache_read / full_input) if full_input > 0 else 0.0
            # Billable-token model mix, same formula collect_sessions uses
            model_usage = {m: v["fresh_input"] + v["cache_create"] + v["output"]
                           for m, v in bd.items()}
            first, last = s["first_ts"], s["last_ts"]
            date = (first or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
            duration_minutes = max(0.0, (last - first).total_seconds() / 60) if first and last else 0.0
            # Event-reported cost is exact; derived cost prices cache_create at
            # the 5m rate (no TTL split in OTel) and slightly understates Cowork's
            # 1h-cache writes.
            if s["reported_cost_usd"] > 0:
                cost_usd, cost_source = s["reported_cost_usd"], "otel_reported"
            else:
                cost_usd, cost_source = measure._cost_from_model_breakdown(bd), "otel_derived"

            cur = conn.execute(
                """INSERT INTO session_log
                     (jsonl_path, date, project, duration_minutes, input_tokens,
                      output_tokens, message_count, api_calls, cache_hit_rate,
                      cache_create_1h_tokens, cache_create_5m_tokens, cache_ttl_scanned,
                      skills_json, subagents_json, tool_calls_json, model_usage_json,
                      all_model_usage_json, model_usage_breakdown_json, version, slug,
                      topic, collected_at, stale_waste_tokens, session_uuid,
                      is_sidechain, cost_usd, cost_source, platform)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, '{}', '{}', ?, ?, ?, ?,
                           NULL, ?, NULL, ?, 0, ?, 0, ?, ?, 'cowork')
                   ON CONFLICT(jsonl_path) DO UPDATE SET
                     date = excluded.date,
                     duration_minutes = excluded.duration_minutes,
                     input_tokens = excluded.input_tokens,
                     output_tokens = excluded.output_tokens,
                     message_count = excluded.message_count,
                     api_calls = excluded.api_calls,
                     cache_hit_rate = excluded.cache_hit_rate,
                     tool_calls_json = excluded.tool_calls_json,
                     model_usage_json = excluded.model_usage_json,
                     all_model_usage_json = excluded.all_model_usage_json,
                     model_usage_breakdown_json = excluded.model_usage_breakdown_json,
                     collected_at = excluded.collected_at,
                     cost_usd = excluded.cost_usd,
                     cost_source = excluded.cost_source""",
                (
                    f"cowork:{sid}", date, "cowork", duration_minutes, full_input,
                    output, s["api_calls"], s["api_calls"], cache_hit_rate,
                    json.dumps(s["tool_calls"]), json.dumps(model_usage),
                    json.dumps(model_usage), json.dumps(bd),
                    f"cowork-{sid[:8]}", datetime.now().isoformat(), sid,
                    round(cost_usd, 6), cost_source,
                ),
            )
            if cur.rowcount > 0:
                written += 1
        if written > 0:
            measure._rebuild_aggregate_tables(conn)
        conn.commit()
        # Read back: never report a DB write without confirming the rows exist.
        check = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0), "
            "COALESCE(SUM(cost_usd),0) FROM session_log WHERE platform='cowork'"
        ).fetchone()
    finally:
        conn.close()

    if not quiet:
        print(f"[to-collector] upserted {written} Cowork session(s) into {measure.TRENDS_DB}")
        print(f"[to-collector] verified in DB: {check[0]} cowork rows, "
              f"input={check[1]:,} output={check[2]:,} cost=${check[3]:.4f}")
        skipped_bits = []
        if stats.get("binary_skipped"):
            skipped_bits.append(f"{stats['binary_skipped']} protobuf records (undecoded)")
        if stats["no_session_id"]:
            skipped_bits.append(f"{stats['no_session_id']} events without session.id")
        if skipped_bits:
            print(f"[to-collector] skipped: {', '.join(skipped_bits)}")
    return 0 if check[0] >= written else 1


# ---------------------------------------------------------------------------
# Cross-agent cost view
# ---------------------------------------------------------------------------

_HOST_LABELS = {"claude": "claude-code", "codex": "codex", "cowork": "cowork",
                "hermes": "hermes", "copilot": "copilot"}


def _row_host(platform: str | None, jsonl_path: str | None) -> str:
    """Same inference _init_trends_db's platform backfill uses, for legacy
    NULL-platform rows."""
    if platform:
        return _HOST_LABELS.get(platform, platform)
    p = jsonl_path or ""
    if "/.codex/" in p:
        return "codex"
    for prefix in ("hermes", "copilot", "cowork"):
        if p.startswith(prefix + ":"):
            return prefix
    return "claude-code"


def cost_view(measure_path: str | None = None, db_override: str | None = None,
              days: int = 30, as_json: bool = False) -> int:
    """Print total tokens/cost grouped by host from the trends store."""
    measure, _ = _load_measure(measure_path)
    if db_override:
        db = Path(db_override).expanduser()
    elif measure is not None:
        db = measure.TRENDS_DB
    else:
        db = Path.home() / ".claude" / "token-optimizer" / "trends.db"
    if not Path(db).exists():
        print(f"[to-collector] trends DB not found: {db}", file=sys.stderr)
        return 1

    cutoff = None
    if days > 0:
        cutoff = datetime.fromtimestamp(time.time() - days * 86400).strftime("%Y-%m-%d")

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        query = ("SELECT platform, jsonl_path, input_tokens, output_tokens, cost_usd, "
                 "model_usage_breakdown_json FROM session_log")
        params: tuple = ()
        if cutoff:
            query += " WHERE date >= ?"
            params = (cutoff,)
        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()

    hosts: dict[str, dict[str, Any]] = {}
    underived = 0
    for platform, jsonl_path, inp, out, cost_usd, bd_json in rows:
        host = _row_host(platform, jsonl_path)
        h = hosts.setdefault(host, {"sessions": 0, "input_tokens": 0, "output_tokens": 0,
                                    "cost_usd": 0.0})
        h["sessions"] += 1
        h["input_tokens"] += int(inp or 0)
        h["output_tokens"] += int(out or 0)
        if cost_usd is not None:
            h["cost_usd"] += float(cost_usd)
        elif measure is not None:
            try:
                bd = json.loads(bd_json) if bd_json else {}
            except (json.JSONDecodeError, TypeError):
                bd = {}
            h["cost_usd"] += measure._cost_from_model_breakdown(bd) if isinstance(bd, dict) else 0.0
        else:
            underived += 1

    result = {"db": str(db), "period_days": days if days > 0 else "all",
              "hosts": {k: {**v, "cost_usd": round(v["cost_usd"], 2)}
                        for k, v in sorted(hosts.items(), key=lambda kv: -kv[1]["cost_usd"])},
              "total_cost_usd": round(sum(h["cost_usd"] for h in hosts.values()), 2),
              "sessions_without_cost": underived}
    if as_json:
        print(json.dumps(result, indent=2))
        return 0

    label = f"last {days} days" if days > 0 else "all time"
    print(f"Cross-agent spend ({label}) — {db}")
    print(f"{'host':<12} {'sessions':>9} {'input tok':>14} {'output tok':>12} {'cost USD':>10}")
    for host, h in result["hosts"].items():
        print(f"{host:<12} {h['sessions']:>9,} {h['input_tokens']:>14,} "
              f"{h['output_tokens']:>12,} {h['cost_usd']:>10,.2f}")
    print(f"{'TOTAL':<12} {sum(h['sessions'] for h in hosts.values()):>9,} "
          f"{sum(h['input_tokens'] for h in hosts.values()):>14,} "
          f"{sum(h['output_tokens'] for h in hosts.values()):>12,} "
          f"{result['total_cost_usd']:>10,.2f}")
    if underived:
        print(f"note: {underived} session(s) have no stored cost and measure.py was "
              "not importable — their cost is excluded.")
    return 0


def serve(host: str, port: int, data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    CollectorHandler.data_dir = data_dir
    server = ThreadingHTTPServer((host, port), CollectorHandler)
    print(f"[to-collector] listening on http://{host}:{port} -> {data_dir}")
    print("[to-collector] endpoints: POST /probe, POST /v1/{logs,traces,metrics}, GET /healthz")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[to-collector] stopped")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="TO Cowork telemetry collector (probe phone-home + OTLP/HTTP + trends ingestion).")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4318)
    parser.add_argument("--data-dir", default=str(Path.home() / ".token-optimizer" / "cowork"))
    parser.add_argument("--summarize", action="store_true",
                        help="Summarize captured telemetry instead of serving")
    parser.add_argument("--ingest", action="store_true",
                        help="Ingest captured telemetry into TO's trends.db (platform=cowork)")
    parser.add_argument("--cost-view", action="store_true",
                        help="Print tokens/cost grouped by host (claude-code/codex/cowork/...) from trends.db")
    parser.add_argument("--days", type=int, default=30,
                        help="Window for --cost-view (0 = all time; default 30)")
    parser.add_argument("--json", action="store_true", help="JSON output for --cost-view")
    parser.add_argument("--measure-path", default=None,
                        help="Explicit path to measure.py (else env TOKEN_OPTIMIZER_MEASURE_PATH, repo layout, installed plugin)")
    parser.add_argument("--db", default=None,
                        help="Override trends.db path (testing; default is measure.py's TRENDS_DB)")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    data_dir = Path(args.data_dir).expanduser()
    if args.summarize:
        print(json.dumps(summarize(data_dir), indent=2))
        return 0
    if args.ingest:
        return ingest(data_dir, measure_path=args.measure_path, db_override=args.db,
                      quiet=args.quiet)
    if args.cost_view:
        return cost_view(measure_path=args.measure_path, db_override=args.db,
                         days=args.days, as_json=args.json)
    serve(args.host, args.port, data_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
