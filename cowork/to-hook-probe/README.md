# to-hook-probe

Tiny diagnostic plugin that proves which hook events actually fire in a
Claude Cowork session (Imri's method: install properly via the org admin
plugin console, then look for the evidence).

It registers a hook on 13 events (`SessionStart`, `UserPromptSubmit`,
`PreToolUse`, `PostToolUse`, `Stop`, `SessionEnd`, `PreCompact`,
`PostCompact`, `StopFailure`, `SubagentStart`, `SubagentStop`,
`Notification`, `CwdChanged`). Each fire:

1. appends `"<time> <event>"` to `~/.to-hook-probe/fired.log` (and
   `/tmp/to-hook-probe/fired.log`),
2. writes a per-event dump: env (credential-shaped values redacted), cwd,
   uname, hook stdin payload,
3. optionally POSTs the dump to `$TO_PROBE_URL/probe` — the only evidence
   channel for **cloud** sessions, which write no local disk.

Everything is fail-open: bash-only (no python dependency inside the VM),
5s timeouts, always exits 0.

## Run the experiment

1. (Optional, needed for cloud) `cp probe.env.example probe.env`, set
   `TO_PROBE_URL` to your collector (see `../collector/to_collector.py`),
   and add that domain to Cowork's domain allowlist.
2. Package: `bash install.sh --cowork` from the repo root builds
   `dist/cowork/to-hook-probe-<version>.zip` alongside the main plugin.
3. Push via the Anthropic **org admin plugin console** (Settings →
   Plugins) as *installed by default* for your test group. Do NOT install
   by editing local files — local injection is the path already proven
   dead; the org push is the one that works.
4. Run one Cowork session (local first, then cloud): give it a couple of
   prompts, run a tool, let it stop.
5. Collect evidence:
   - In-session, ask: *"Run `cat ~/.to-hook-probe/fired.log` and show me
     the output."*
   - On the host (local VM sessions): `python3
     skills/token-optimizer/scripts/cowork_doctor.py` reads the ledger and
     prints the fired-event matrix.
   - On the collector (cloud sessions): check `probe.jsonl` for POSTs.

## Reading the result

- **Ledger has SessionStart/UserPromptSubmit/PreToolUse/Stop** → the v4
  event set is confirmed; ship the main Cowork plugin as packaged.
- **Ledger has more events** → widen `COWORK_EVENTS` in
  `cowork_install.py` accordingly.
- **No ledger, but collector got POSTs** → hooks fire but the session disk
  is not where you looked (expected for cloud).
- **Nothing anywhere** → the install path is wrong (not org-pushed), the
  build doesn't fire hooks, or the allowlist blocked the POST *and* disk
  wasn't readable. Check `cowork_doctor.py` output and the org console
  state before concluding hooks are dead. One more suspect: a build that
  strictly validates event names could reject the whole hooks.json over
  the exotic entries (`CwdChanged`, `StopFailure`, …) — retry with
  hooks.json trimmed to just SessionStart/UserPromptSubmit/PreToolUse/Stop
  before giving up.
