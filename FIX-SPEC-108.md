# Fix spec: issue #108 — Codex goal reports mix historical subagents + miss worker goals

Base: origin/main @ v5.11.74. Branch: fix/108-codex-goal-scoping.
Reporter: 0x4d-sh. Codex goals orchestrated by Orca; outer coordinator launches workers as
independent Codex goal sessions; each worker objective carries `Parent goal <outer-thread-id>`.

## Root cause (verified)
`skills/token-optimizer/scripts/codex_state.py::subagent_costs()` (~L169) runs
`SELECT ... FROM thread_spawn_edges e LEFT JOIN threads t ... LIMIT _MAX_ROWS` with NO filter
by the current/active thread. So EVERY spawn edge in the DB is aggregated, including closed
and historical edges from older work. Result: a current goal reports old subagents, token
totals are cumulative/inflated, and `quality current` can select a stale session.
`goal_budgets()` (~L343) and the `quality current` selection in measure.py have the same
"not scoped to the active thread" problem.

## Required behavior (from the issue — implement all)
1. **Scope to the active thread/goal.** `subagent_costs()` and `goal_budgets()` must accept a
   current/root thread id and return only edges/goals belonging to the CURRENT goal subtree —
   i.e. the active thread and its transitive descendants via `thread_spawn_edges` — not every
   edge in the DB. Token totals reflect only the selected goal.
2. **No historical/closed leakage.** Closed or historical spawn edges from unrelated prior work
   must not appear in the current goal's report. (Keep leak-detection for edges that ARE in the
   current subtree.)
3. **Deterministic `quality current`.** The `current` selection must resolve to the active/current
   thread deterministically (by the resolved current thread id), NOT by most-recent-mtime guess
   that can pick an older session. Find how the current codex thread is resolved (grep for the
   current-thread/session resolver used on the Codex path) and key the selection off that.
4. **Optional parent-goal rollup.** When a worker objective contains an explicit
   `Parent goal <thread-id>` marker, allow aggregating those independent worker goals under the
   named coordinator thread — WITHOUT pulling unrelated historical sessions in. This is opt-in via
   the explicit marker only; absent a marker, no cross-goal aggregation happens.
5. **Auditable diagnostics.** Expose in the JSON output: the selected/resolved thread id, and
   every aggregation edge used (parent->child ids + status) so stale selection and token
   attribution are inspectable. Add under a clearly-named key (e.g. `selected_thread_id`,
   `aggregation_edges`).

## Non-negotiables
- Non-Codex runtimes unaffected: `_is_codex()` gate + `_empty_*()` returns stay; a non-Codex
  call path must behave exactly as today.
- Read-only DB access only (`_ro_connect`), keep the `_ALLOWED_TABLES` whitelist and `_MAX_ROWS`
  cap. No schema writes.
- Fail-open: any sqlite/OS error returns the empty/default shape, never raises into a hook or CLI.
- Backward-compatible signatures: if you add a `root_thread_id`/`current_thread_id` param, default
  it so existing callers still work; update callers (measure.py L4320 `codex_state.goal_budgets()`
  and the subagent_costs caller) to pass the resolved current thread.
- Descendant walk must be cycle-safe (a malformed edge graph can't infinite-loop; bound by
  `_MAX_ROWS`/visited-set).

## Tests (add to tests/)
- subagent_costs scoped to a root thread returns ONLY that subtree; historical/unrelated edges
  excluded; token total == sum of the subtree only.
- closed/historical edge from another root does NOT appear.
- `quality current` resolves to the active thread id, not an older-mtime session (mock two
  sessions; assert the active one is chosen).
- parent-goal rollup: a worker whose objective has `Parent goal <id>` groups under <id>; a worker
  WITHOUT the marker does not.
- JSON output contains `selected_thread_id` and `aggregation_edges`.
- cycle-safety: a self-referential / cyclic edge set terminates and fails open.
- non-Codex runtime: unchanged empty shape.

## Verify before finishing
`python3 -m py_compile` on edited files (both tree copies via scripts/sync-codex-marketplace-plugin.sh),
run the full pytest suite + new tests. Commit LOCALLY only. Do NOT push, tag, PR, or release.
