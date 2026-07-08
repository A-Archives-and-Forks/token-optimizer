---
description: Check running Claude Code or Codex sessions, find zombies, offer to clean up safely
---

# Session Health Check

Run a session health check and help the user manage running sessions safely.

## Steps

1. Resolve measure.py path:
```bash
RUNTIME="${TOKEN_OPTIMIZER_RUNTIME:-}"
if [ -z "$RUNTIME" ]; then
  # Env signals are authoritative and checked before directory heuristics: a host
  # with BOTH ~/.codex and ~/.config/opencode (running OpenCode) must resolve to
  # opencode, not codex, so the tool never reaches into ~/.claude (issue #57).
  if [ -n "$CLAUDE_PLUGIN_ROOT" ] || [ -n "$CLAUDE_PLUGIN_DATA" ]; then
    RUNTIME="claude"
  elif [ -n "$OPENCODE" ] || [ -n "$OPENCODE_BIN" ] || [ -n "$OPENCODE_CONFIG_DIR" ] || [ -n "$OPENCODE_CONFIG" ]; then
    RUNTIME="opencode"
  elif [ -n "$CODEX_HOME" ]; then
    RUNTIME="codex"
  elif [ -d "$HOME/.config/opencode" ] && [ ! -d "$HOME/.codex" ]; then
    RUNTIME="opencode"
  elif [ -d "$HOME/.codex" ]; then
    RUNTIME="codex"
  else
    RUNTIME="claude"
  fi
fi
# Resolve measure.py to the NEWEST installed copy across channels so a stale
# plugin-cache copy never shadows a fresh install (issue #57). find -L follows the
# install.sh symlink under ~/.claude/skills; cd -P resolves it before reading each
# copy's plugin.json for its version. find (not bare globs) never errors under zsh.
MEASURE_PY=""; _best_ver=""
while IFS= read -r _cand; do
  [ -f "$_cand" ] || continue
  _root="$(cd -P -- "$(dirname -- "$_cand")/../../.." 2>/dev/null && pwd)"
  _ver="$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$_root/.claude-plugin/plugin.json" 2>/dev/null | head -1)"
  [ -n "$_ver" ] || _ver="0.0.0"
  if [ -z "$_best_ver" ] || [ "$(printf '%s\n%s\n' "$_ver" "$_best_ver" | sort -t. -k1,1n -k2,2n -k3,3n -k4,4n | tail -n1)" = "$_ver" ]; then
    _best_ver="$_ver"; MEASURE_PY="$_cand"
  fi
done <<EOF
$(find -L "$HOME/.claude/skills" "$HOME/.claude/plugins/cache" "$HOME/.claude/token-optimizer" "$HOME/.codex/skills" "$HOME/.codex/plugins/cache" "$HOME/.config/opencode/plugins" -type f -name measure.py -path '*token-optimizer*/scripts/measure.py' 2>/dev/null)
EOF
if [ -z "$MEASURE_PY" ]; then echo "[Error] measure.py not found. Is Token Optimizer installed?"; exit 1; fi
export TOKEN_OPTIMIZER_RUNTIME="$RUNTIME"
```

2. Run (use the resolved `$RUNTIME` — never hardcode a runtime; under OpenCode this
   keeps the session scan scoped to OpenCode and never reaches into `~/.claude`):
   - Claude Code plugin: `bash "$CLAUDE_PLUGIN_ROOT/hooks/python-launcher.sh" $MEASURE_PY health`
   - Codex / OpenCode / standalone: `TOKEN_OPTIMIZER_RUNTIME="$RUNTIME" python3 "$MEASURE_PY" health`

3. Present results clearly. For each session show: PID, elapsed time, version, and flags (STALE >24h, ZOMBIE >48h, OUTDATED, HEADLESS, TERMINAL).

4. If ANY sessions are flagged STALE or ZOMBIE, ask the user:
   "I found N session(s) that look stale. Want me to show details so you can decide which to terminate?"

5. **CRITICAL SAFETY RULES — follow these exactly:**
   - NEVER auto-kill anything. Always ask first and get explicit confirmation.
   - HEADLESS sessions might be intentional background processes (cron agents, heartbeat monitors, scheduled tasks). Always warn: "This session is headless, it might be a background agent running on purpose. Are you sure you want to terminate it?"
   - Let the user pick specific PIDs to terminate, or offer "terminate all ZOMBIE-flagged sessions" as a batch option.
   - Always run a dry-run first to preview what would be terminated, then ask for confirmation before running without `--dry-run`.
   - Claude Code plugin dry-run: `bash "$CLAUDE_PLUGIN_ROOT/hooks/python-launcher.sh" $MEASURE_PY kill-stale --dry-run`
   - Codex / OpenCode / standalone dry-run: `TOKEN_OPTIMIZER_RUNTIME="$RUNTIME" python3 "$MEASURE_PY" kill-stale --dry-run`
   - If the user says "kill all" or similar, still show the dry-run preview and confirm. No silent kills.

6. If no stale or zombie sessions found, say: "All sessions look healthy. Your oldest is Xh old."
