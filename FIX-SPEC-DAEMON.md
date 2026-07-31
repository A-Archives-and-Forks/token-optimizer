# Fix spec: dashboard daemon must survive a plugin update

Base: release/5.11.75-batch. Branch: fix/daemon-survives-update.
HIGH-RISK: touches daemon lifecycle that EVERY user runs. Be conservative and fail-open.

## Symptom (reproduced on a real machine)
After a routine `/plugin` update + `/reload-plugins`, the dashboard daemon dies and its
launchd plist (`~/Library/LaunchAgents/com.token-optimizer.dashboard.plist`) is GONE, and it
does NOT come back during the session. `http://localhost:24842/token-optimizer` then refuses
connections until a manual `setup-daemon`. This has happened repeatedly.

## Likely mechanism (INVESTIGATE and confirm before fixing)
A plugin update runs a reconcile/cleanup that can sweep daemon plists across
`token-optimizer-*` identities. Investigate these and find where the ACTIVE, healthy daemon's
plist gets removed during an UPDATE (as opposed to an explicit uninstall):
- `install_reconcile.reconcile_uninstall(..., remove=True)` — it already tags entries
  "(active, kept)" vs "(stale)"; verify the ACTIVE daemon identity is correctly classified as
  active during an update and never removed. A misclassification (e.g. the marketplace path
  moved during update so the active identity looks stale) is a prime suspect.
- `_sweep_identity_daemon_files`, `_daemon_identity_snapshot_dirs(this_install_only)`,
  `_uninstall_launchd_daemon(this_install_only=False)` — the sweep-all path.
- Whatever the plugin runs on update/migration/SessionStart that could reach the above.
- The mid-session revive throttle (`_daemon_pulse_revive_seconds`, default 300s) and the
  SessionStart-only self-heal: even once the plist is gone, recovery is too slow/scoped.

## Required behavior
1. **A routine plugin update MUST NOT remove the active daemon's plist / kill the running
   daemon.** Only an explicit `setup-daemon --uninstall` (or a genuinely stale/dead identity)
   may remove a plist. If reconcile classifies the active identity as stale during an update,
   fix the classification so the live daemon is always protected.
2. **Fast recovery if the plist IS missing.** When a live session detects the daemon dead AND
   the plist absent (a clear "something removed it" state), the detached `daemon-revive`
   reinstall must run promptly — bypass or shorten the 300s revive throttle for the
   plist-absent case specifically (a missing plist is not the transient "port not up yet" case
   the throttle is meant to bound). Keep the throttle for the ordinary dead-but-installed case.

## Non-negotiables
- `setup-daemon --uninstall` must STILL fully remove the daemon + plist (do not break uninstall).
- The reclaim/sweep of genuinely stale/dead OTHER identities must still work.
- Fail-open: never raise into a hook or block a session; all new paths try/except to a no-op.
- Cross-platform: launchd (macOS) + Task Scheduler (Windows) symmetry preserved.
- Additive/surgical; do not rewrite the daemon lifecycle.

## Tests (add to tests/)
- reconcile during an update with a HEALTHY active daemon → the active plist is NOT removed
  (classified active/kept); a genuinely stale identity → still removed.
- plist-absent + dead daemon in-session → revive is dispatched promptly (throttle bypassed for
  the plist-absent case); dead-but-plist-present → throttle still applies.
- `setup-daemon --uninstall` still removes everything.
- fail-open: an error in the new protection path does not raise.

## Verification (IMPORTANT — cannot fully verify in a worktree)
The launchd kill/revive can only be truly verified on the human's real machine, not here.
Do the unit tests + py_compile + full pytest. The human (Claude, the orchestrator) will do the
REAL test: install the daemon with this code, simulate a plugin-update reconcile, and confirm
the active daemon + plist SURVIVE, then confirm a manually-removed plist self-revives fast.
Apply to BOTH script trees via scripts/sync-codex-marketplace-plugin.sh. Commit LOCALLY only;
do NOT push/tag/PR/release.
