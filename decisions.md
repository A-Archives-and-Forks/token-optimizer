# Cowork Full-Parity — Decisions (cowork-full-parity branch)

## Design Decisions
- **In-place parity, not a second plugin.** The main `token-optimizer` plugin becomes
  Cowork-native by adding the 3 run-once SessionStart features (ensure-health,
  quality-cache --force, compact-restore --new-session-only) into the MASTER
  `hooks/hooks.json` **UserPromptSubmit** group, each wrapped in a once-per-session guard.
  Rationale: SessionStart does not fire in Cowork; UserPromptSubmit does. The guard makes
  the UserPromptSubmit copies no-op in regular Claude Code (SessionStart already ran them
  and set the marker), so zero behavior change for existing users and no duplicated skills/
  tree. "Install the normal plugin in Cowork" = full parity.
- **Proven firing events (cloud Cowork, CC 2.1.231):** UserPromptSubmit, PreToolUse,
  PostToolUse, Stop, SubagentStop. `COWORK_EVENTS` corrected to the 4 that carry features
  (SubagentStop has no master hooks to ride — not fabricated).
- **build_cowork_hooks() stays a pure trim** (org-console ZIP path). Because master
  UserPromptSubmit already carries the run-once commands, no special remap/injection is
  needed in the packager.
- **Once-per-session guard** keyed on sanitized session_id, stored in the engine's existing
  per-session state dir (no new top-level location). Protects every existing CC user from
  double-fire — this is the one correctness-critical piece.

## Deviations
- COWORK_BUILD_PLAN.md item 1-2 described editing cowork_install.py to remap events. The
  remap moved UP into master hooks.json instead (cleaner, fixes both distribution paths at
  once). Packager change reduced to the COWORK_EVENTS constant.

## Tradeoffs
- Adding guarded commands to master UserPromptSubmit = 3 extra marker-check subprocess
  spawns on the first prompt of every regular Claude Code session. Negligible, fail-open.
  Accepted in exchange for a single source of truth and no repo bloat.

## Open Questions
- compaction-restore on native trigger stays degraded in Cowork (PreCompact/PostCompact/
  SessionStart:compact all dead). compact-capture on Stop still saves state; fresh-session
  compact-restore reads it back. Documented, not solved.
- Version bump + alignment (plugin.json / marketplace.json / README badge / tag) at package
  step; not pushed to main until torture-room green + review.
