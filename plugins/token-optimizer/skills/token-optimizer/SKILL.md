---
name: token-optimizer
description: Find the ghost tokens. Audit Claude Code or Codex setup, see where context goes, fix it. Use when context feels tight.
effort: high
---

# Token Optimizer

Audits a Claude Code or Codex setup, identifies context window waste, implements fixes, and measures savings.

**Target**: 5-15% context recovery through config cleanup, up to 25%+ with autocompact management.

---

## Codex Runtime

If `TOKEN_OPTIMIZER_RUNTIME=codex` or Codex environment is detected, read `references/codex-workflow.md` and follow its chat-first workflow instead of the Claude Code phases below.

---

## OpenCode Runtime

OpenCode loads `~/.claude/skills` by default, so it can invoke this skill even though you are working in OpenCode, not Claude Code. If OpenCode is detected — `TOKEN_OPTIMIZER_RUNTIME=opencode`, any `OPENCODE_*` environment variable, or you are running inside OpenCode — **STOP** and read `references/opencode-workflow.md`. Do **not** run the Claude Code phases below: they scan and modify `~/.claude`, which is the wrong target when the user is in OpenCode (issue #57).

Quick check before Phase 0:
```bash
python3 "$MEASURE_PY" report 2>/dev/null | head -1
# If it prints "Token Optimizer — OpenCode runtime detected.", follow references/opencode-workflow.md and stop.
```

---

## Phase 0: Initialize (Claude Code)

Resolve measure.py path:
```bash
MEASURE_PY=""
for f in "$HOME/.claude/skills/token-optimizer/scripts/measure.py" \
         "$HOME/.claude/plugins/cache"/*/token-optimizer/*/skills/token-optimizer/scripts/measure.py; do
  [ -f "$f" ] && MEASURE_PY="$f" && break
done
[ -z "$MEASURE_PY" ] && { echo "[Error] measure.py not found."; exit 1; }
```

Read `references/phase0-setup.md` for the full setup sequence: context window detection, pre-check, backup, coordination folder, hook checks, daemon setup, and smart compaction.

---

## Phase 0.5: Keep-Warm Consent (first run only, Claude Code)

Keep-Warm is opt-in and pays off only for API-key-billed Claude Code sessions. Ask once:

```bash
python3 "$MEASURE_PY" keepwarm-consent-status   # JSON: {billing_mode, consent, should_ask}
```

If `should_ask` is `false`, skip this phase silently (subscription users are never asked; declined/enabled users keep their choice). If `should_ask` is `true`, first compute the user's own projection, then present the pitch:

```bash
python3 "$MEASURE_PY" keepwarm-backfill --json --no-fence   # read modes."probe-only".net_usd
```

Read `net_usd` under `modes."probe-only"`. If it is a positive number, include it as the projection. If backfill errors, returns nothing, or `net_usd <= 0`, drop the dollar sentence entirely (do not invent a number) and use the no-data wording below.

> **Keep your prompt cache warm automatically?** When a Claude Code session pauses past its 1h cache window and resumes, the whole prefix is re-written at up to 2x input. Keep-Warm pings the cache just before expiry (~0.1x of the prefix, max 2 pings per pause) so a resume stays warm. A history-replay projection from your own last 30 days nets ~$<net_usd>/30d at the conservative probe-only setting. A tripwire auto-disables it if pings ever stop paying for themselves, and you can turn it off any time. Enable it?

No-data wording (when backfill yields no positive projection): drop the projection sentence and say "Your savings depend on your own pause-and-resume pattern; the dashboard will show your number once pings have fired."

Then record the answer (do this exactly once). **Record the yes/no FIRST**, so an interrupted run never strands an "asked" marker with no recorded answer:

```bash
# yes:
python3 "$MEASURE_PY" keepwarm-enable
# no:
python3 "$MEASURE_PY" keepwarm-disable
```

`keepwarm-enable` and `keepwarm-disable` are terminal states, so they already satisfy `should_ask`. Only if the user defers or ignores the question (records neither) run the shown-marker so they are not re-asked next run:

```bash
python3 "$MEASURE_PY" keepwarm-consent-asked          # mark shown (sticky); use ONLY when no enable/disable was recorded
```

`keepwarm-enable` records consent and installs the scheduler (macOS); on other OSes the scheduler is pending, so it is watchdog-only. It refuses on subscription with an honest message. To confirm it is armed:

```bash
python3 "$MEASURE_PY" keepwarm-scheduler status      # JSON: installed/loaded state (macOS)
python3 "$MEASURE_PY" keepwarm-tick --dry-run        # JSON: what the next tick would decide
```

---

## Phase 1: Quick Audit (Parallel Agents)

Read `references/agent-prompts.md` for all prompt templates.

Dispatch 6 agents in parallel:

| Agent | Output File | Model | Task |
|-------|-------------|-------|------|
| CLAUDE.md Auditor | `audit/claudemd.md` | sonnet | Size, duplication, tiered content, cache structure |
| MEMORY.md Auditor | `audit/memorymd.md` | sonnet | Size, overlap with CLAUDE.md |
| Skills Auditor | `audit/skills.md` | sonnet | Count, frontmatter overhead, duplicates |
| MCP Auditor | `audit/mcp.md` | sonnet | Deferred tools, broken/unused servers |
| Commands Auditor | `audit/commands.md` | haiku | Count, menu overhead |
| Settings & Advanced | `audit/advanced.md` | sonnet | Hooks, rules, settings, @imports, caching |

Pass `COORD_PATH` to each. Wait for all to complete. If any output file is missing, note the gap and proceed.

---

## Phase 2: Analysis

Read the **Synthesis Agent** prompt from `references/agent-prompts.md`. Dispatch with `model="opus"` (fallback: sonnet). It reads all audit files and writes `{COORD_PATH}/analysis/optimization-plan.md`. If missing, present raw audit files instead.

---

## Phase 3: Present Findings

Read `references/presentation-workflow.md` for the findings template, dashboard generation, and URL presentation logic. Generate the dashboard:
```bash
python3 $MEASURE_PY dashboard --coord-path $COORD_PATH
```
Wait for user decision before proceeding.

---

## Phase 4: Implementation

Read `references/implementation-playbook.md` for detailed steps. Available actions: 4A-4P covering CLAUDE.md, MEMORY.md, Skills, File Exclusion, MCP, Hooks, Cache, Rules, Settings, Descriptions, Compact Instructions, Model Routing, Smart Compaction, Quality Check, Version-Aware Optimizations, and Smart Routing. Templates in `examples/`. Always backup before changes. Present diffs for approval.

---

## Phase 5: Verification

Read the **Verification Agent** prompt from `references/agent-prompts.md`. Dispatch with `model="haiku"`. Re-measures everything and calculates savings. Present before/after comparison and behavioral next steps.

---

## Reference Files

| Context | Read |
|---------|------|
| Codex runtime | `references/codex-workflow.md` |
| Phase 0 setup details | `references/phase0-setup.md` |
| Phase 1-2 agent prompts | `references/agent-prompts.md`, `references/token-flow-architecture.md` |
| Phase 3 presentation | `references/presentation-workflow.md` |
| Phase 4 implementation | `references/implementation-playbook.md`, `examples/` |
| CLI commands | `references/cli-reference.md` |
| Phase 3 checklist | `references/optimization-checklist.md` |
| Error handling | `references/error-recovery.md` |

---

## Core Rules

- Quantify everything (X tokens, Y%)
- Create backups before any changes
- Ask user before implementing
- Never delete files, always archive outside the skills directory
- Check dependencies before archiving (skills, MCP, deny rules can break other tools)
- Warn about side effects before each change
- Prefer project-level deny rules over global
- Show before/after diffs
- Frame savings as context budget (% of window), not dollar amounts
