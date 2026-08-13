#!/usr/bin/env python3
"""TO Cowork telemetry collector — STUB, first cut.

One process, three jobs:
  1. `/probe`            — receives to-hook-probe phone-home POSTs (the only
                           evidence channel for cloud sessions).
  2. `/v1/{logs,traces,metrics}` — receives Cowork's OTel OTLP/HTTP export
                           (org-admin points the endpoint here; Team/
                           Enterprise; http/protobuf or http/json — gRPC is
                           not supported for Cowork).
  3. `/healthz`          — liveness for cowork_doctor.py.

Everything is appended raw to JSONL under --data-dir (default
~/.token-optimizer/cowork). `--summarize` then extracts the api_request
token fields Cowork emits (input_tokens, output_tokens, cache_read_tokens,
cache_creation_tokens, model, session.id — see claude.com/docs/cowork/
monitoring) from whatever JSON bodies landed, as a smoke test that real
telemetry is flowing.

STUB boundary (deliberate): ingestion into trends.db / the dashboard is
NOT wired yet. The path is: map api_request -> the per-session usage rows
measure.py `collect` builds from transcripts, keyed by session.id, then let
the existing dashboard render it. That mapping needs real captured events
to build against — run this stub during the live verification session
first. Protobuf OTLP bodies are stored base64-raw, not decoded (decoding
needs the otlp proto schema or a dependency; the summarizer skips them).

Run on a host reachable from Cowork VMs/cloud, behind HTTPS (cloud sessions
will not be able to reach a laptop's localhost; the domain must be on
Cowork's allowlist):
    python3 to_collector.py --host 0.0.0.0 --port 4318
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
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


def summarize(data_dir: Path) -> dict[str, Any]:
    """Smoke-test summary: pull api_request token fields out of captured
    OTLP JSON bodies. Tolerant of both flat events and OTLP log-record
    shapes; protobuf (binary) records are counted but not decoded."""
    totals = {"api_request_events": 0, "input_tokens": 0, "output_tokens": 0,
              "cache_read_tokens": 0, "cache_creation_tokens": 0}
    models: dict[str, int] = {}
    sessions: set[str] = set()
    binary_skipped = 0
    files = sorted(data_dir.glob("otlp-*.jsonl"))
    for path in files:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("kind") == "binary":
                binary_skipped += 1
                continue
            for node in _walk(record.get("body")):
                attrs = _attr_map(node.get("attributes"))
                merged = {**{k: v for k, v in node.items() if not isinstance(v, (dict, list))}, **attrs}
                name = merged.get("event.name") or merged.get("name") or ""
                if "api_request" not in str(name):
                    continue
                totals["api_request_events"] += 1
                for field in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens"):
                    try:
                        totals[field] += int(merged.get(field) or 0)
                    except (TypeError, ValueError):
                        pass
                model = str(merged.get("model") or "unknown")
                models[model] = models.get(model, 0) + 1
                sid = merged.get("session.id") or merged.get("session_id")
                if sid:
                    sessions.add(str(sid))
    probe = data_dir / "probe.jsonl"
    probe_posts = sum(1 for _ in probe.open(encoding="utf-8")) if probe.exists() else 0
    return {"data_dir": str(data_dir), "otlp_files": [f.name for f in files],
            "probe_posts": probe_posts, "binary_records_skipped": binary_skipped,
            "totals": totals, "models": models, "sessions": len(sessions)}


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
    parser = argparse.ArgumentParser(description="TO Cowork telemetry collector stub (probe phone-home + OTLP/HTTP).")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4318)
    parser.add_argument("--data-dir", default=str(Path.home() / ".token-optimizer" / "cowork"))
    parser.add_argument("--summarize", action="store_true", help="Summarize captured telemetry instead of serving")
    args = parser.parse_args(argv)
    data_dir = Path(args.data_dir).expanduser()
    if args.summarize:
        print(json.dumps(summarize(data_dir), indent=2))
        return 0
    serve(args.host, args.port, data_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
