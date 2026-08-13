# MISSING — Cowork adapter, first cut (2026-08-13)

Honest ledger: what this cut does not deliver, which v4 claims checked out
against the real code, and exactly what still needs a live org console +
Cowork session (hand-off to Alex).

## v4 claims vs. the real code (this repo, v5.11.88)

| v4 claim | Verified here? |
|---|---|
| TO is already a plugin in the shared format | **YES** — `.claude-plugin/plugin.json` + `hooks/hooks.json` + account-syncable `skills/`+`commands/` at repo root. |
| The claimed Cowork event set maps onto TO's existing hooks | **YES** — all four events exist in `hooks/hooks.json`, and every wired entrypoint (`ensure-health`, `quality-cache`, `compact-restore`, `prompt-continuity`, `verbosity-steer`, `checkpoint-trigger`, `compact-capture`, `session-end-flush`, `read_cache`, `bash_hook`, `refetch_guard`) resolves in `measure.py`/scripts (grep-verified). |
| Allowlist machinery active on this machine | **YES** — `dxt:allowlistEnabled` / `dxt:allowlistLastUpdated` / `dxt:allowlistCache` live in the desktop `config.json`, incl. a per-env key updated 2026-08-13. Currently `enabled=false` (not enforced) on this machine. |
| Local session tree readable | **YES** — `local-agent-mode-sessions/` present, 34 `audit.jsonl` files found by the doctor. |
| Hooks fire in Cowork via org-console push | **NOT verifiable from code** — this is the whole point of `to-hook-probe`. Source is first-hand (Imri) but our own evidence is still pending. |
| `bridge-state.json` marks cloud-vs-local | **NOT confirmed locally** — doctor found none on this machine; treat as unverified detail from the Pluto writeup. |

## Missing from this cut (build gaps, ranked)

1. **OTel → trends.db ingestion.** The collector captures and summarizes
   `api_request` events but does not write TO's `trends.db` or feed the
   dashboard. Needs a mapping from `api_request`/`tool_result` attrs to the
   per-session usage rows `measure.py collect` builds — build it against
   real captured events from the live session, not guessed shapes.
2. **`cowork_session.py` (on-disk reader).** The v3 keystone for local
   sessions — normalize `audit.jsonl` + session index into the shared
   schema (mirror `codex_session.py`). Bonus after the v4 correction, but
   it's what makes the dashboard/backfill work for local Cowork with zero
   org dependencies.
3. **`runtime_env`/`routing_advisor` have no `cowork` row.** Inside a
   Cowork VM the plugin will detect as plain `claude`. Fine for v1 (same
   engine paths), but model-routing advice uses the wrong ladder and
   telemetry won't be tagged `cowork`. Add detection (probe env dump will
   show the discriminating env var) + a routing table.
4. **Payload slimming.** The zip carries the full 5.7MB `scripts/` tree +
   7.7MB `assets/`. Works, but an org push syncs it to every seat —
   worth an `--slim` profile once we know what Cowork sessions actually use.
5. **`hooks/hooks.json` PostToolUse/PreCompact families.** Dropped
   pending probe evidence. If the probe shows them firing, the archive/
   context-intel/compact-instructions features come back for free by
   widening `COWORK_EVENTS`.
6. **Uninstall/rollback story.** Org console removal is assumed to
   account-unsync the plugin; no local cleanup path written (nothing is
   installed locally by design).
7. **measure.py `cowork-install`/`cowork-doctor` subcommands** (the way
   `codex-install`/`codex-doctor` are routed) — currently invoked as
   standalone scripts; wire into measure.py once the adapter stabilizes.
8. **No tests** for `cowork_install.py`/`cowork_doctor.py`/collector
   beyond the manual smoke run in this session.

## Needs a live org console + Cowork session (hand-off checklist)

`cowork_doctor.py` prints these as NEEDS-LIVE; in order:

1. **Org console mechanics** — where exactly plugins are registered
   (Settings → Plugins), whether it takes a **zip upload or a marketplace
   git source** (we built both: zips + the repo's `marketplace.json`), and
   the available/default/required semantics. Our packaging format is a
   best guess until the console accepts it.
2. **Push `to-hook-probe`, run one local session** → read
   `~/.to-hook-probe/fired.log` in-session. This proves (a) org-push
   installs plugins into sessions, (b) which events fire, (c) whether
   `CLAUDE_PLUGIN_ROOT` is set and bash/curl exist in the VM.
3. **Repeat in a cloud session** with `TO_PROBE_URL` baked and the
   collector domain allowlisted → collector `probe.jsonl` is the only
   evidence channel there. This is the automation-parity moment.
4. **Allowlist round-trip** — add the domain in the console, confirm
   `dxt:allowlistEnabled` flips/updates locally, confirm a non-allowlisted
   domain is actually blocked (negative test).
5. **OTel** — org-admin endpoint setting exists for Cowork, export
   reaches the collector, event shapes match the documented
   `api_request`/`tool_result` attrs (then build gap #1 against them).
6. **Full plugin push** — after the probe is green: push the main zip,
   run a session, `cowork_doctor.py` should show the fire matrix with all
   four events and checkpoints/quality-cache artifacts appearing.
7. **Env dump review** — from the probe's redacted env dump: the
   cloud-vs-local discriminator, session id var, python3 availability
   (gap #3's detection input).
