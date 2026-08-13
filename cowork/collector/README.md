# Cowork telemetry collector

`to_collector.py` receives Claude Cowork's OpenTelemetry export (OTLP/HTTP) —
Cowork's only token/cost window, since cloud sessions have no local transcript —
and folds it into Token Optimizer's normal trends store so Cowork sessions show
up alongside Claude Code and Codex.

## Modes

| Mode | What it does |
|---|---|
| (serve, default) | HTTP endpoint: accepts the probe phone-home + OTLP/HTTP POSTs, appends to capture files. |
| `--summarize` | Print per-session `api_request` event counts from captured telemetry. |
| `--ingest` | Parse captured OTel into TO's `trends.db` (`platform=cowork`), reusing measure.py's own schema helpers — no second schema. Upserts, so re-running mid-session refreshes totals. |
| `--cost-view [--days N] [--json]` | Tokens + cost grouped by host (claude-code / codex / cowork). The cross-agent spend view. |

## Run

```bash
# 1. Serve (must be HTTPS-reachable from cloud VMs; a laptop localhost is not).
python3 cowork/collector/to_collector.py --host 0.0.0.0 --port 4318

# 2. Point Cowork's OTel export at it (Org settings -> Cowork; http/protobuf).
#    The collector domain must be on Cowork's domain allowlist.

# 3. After sessions land, fold into trends and read the cross-agent view:
python3 cowork/collector/to_collector.py --ingest
python3 cowork/collector/to_collector.py --cost-view --days 30
```

`--ingest` locates measure.py via `--measure-path`, `TOKEN_OPTIMIZER_MEASURE_PATH`,
a repo-relative path, or the installed-plugin glob; if none resolve it aborts
cleanly rather than writing a divergent store. stdlib-only, fail-open.
