# Uninstall

Token Optimizer is additive and reversible. Every runtime has a clean
uninstall that removes only what we installed. Your own hooks, config keys,
and plugin entries are never clobbered. Session data and trends are left in
place by design (they're yours); each section below names the exact command
to ALSO purge that data if you want a full wipe.

## Claude Code

**Plugin install** (the recommended path):

```
/plugin uninstall token-optimizer@alexgreensh-token-optimizer
```

That removes the plugin and its hooks. To also drop the marketplace:

```
/plugin marketplace remove alexgreensh-token-optimizer
```

**Script install** (`bash install.sh`), undo each opt-in component you
enabled. Each `--uninstall` removes ONLY Token Optimizer's own entries and
leaves your other hooks intact:

```bash
python3 ~/.claude/skills/token-optimizer/scripts/measure.py setup-smart-compact --uninstall
python3 ~/.claude/skills/token-optimizer/scripts/measure.py setup-quality-bar --uninstall
python3 ~/.claude/skills/token-optimizer/scripts/measure.py setup-daemon --uninstall
python3 ~/.claude/skills/token-optimizer/scripts/measure.py setup-coach-injection --uninstall
python3 ~/.claude/skills/token-optimizer/scripts/measure.py setup-hook --uninstall
```

Then remove the skill tree and tracking data (optional, full wipe):

```bash
rm -rf ~/.claude/token-optimizer          # the install dir (script install)
rm -rf ~/.claude/skills/token-optimizer   # the skill tree
rm -rf ~/.claude/_backups/token-optimizer # backups written on hook changes
rm -f  ~/.claude/.settings.lock           # advisory lock file
```

## Codex

```bash
TOKEN_OPTIMIZER_RUNTIME=codex python3 skills/token-optimizer/scripts/measure.py codex-install --uninstall
```

This strips Token Optimizer hook groups from `~/.codex/hooks.json`, removes
the `# BEGIN/END token-optimizer compact prompt` block and the prompt file
from `~/.codex/config.toml`, and removes the `# BEGIN/END token-optimizer
status line` `[tui]` block (uncommenting any `status_line`/`terminal_title`
settings Token Optimizer commented out on a `--force` install). User-authored
config keys are never touched. Add `--dry-run` to preview.

Then remove the marketplace plugin via the Codex TUI (`/plugins`) or:

```bash
codex plugin marketplace remove alexgreensh/token-optimizer
```

Optional full wipe of Codex session/trends data:

```bash
rm -rf ~/.codex/token-optimizer
```

See [`docs/codex.md`](codex.md).

## GitHub Copilot

```bash
TOKEN_OPTIMIZER_RUNTIME=copilot python3 skills/token-optimizer/scripts/measure.py copilot-uninstall
```

Removes only the Token Optimizer hook entry from
`~/.copilot/hooks/token-optimizer.json`. **Copilot session data
(`~/.copilot/session-state/`, `~/.copilot/token-optimizer/`) is left in
place by design.** Token Optimizer reads Copilot's session logs but never
moves or owns them. To purge Token Optimizer's own data too:

```bash
rm -rf ~/.copilot/token-optimizer
```

For VS Code Copilot per-request cost tracking, disable the two
`github.copilot.chat.agentDebugLog` settings in VS Code. See
[`docs/copilot.md`](copilot.md).

## OpenCode

```bash
bash install.sh --opencode --uninstall
```

Removes `~/.config/opencode/plugins/token-optimizer.js` and reverts the
`token-optimizer-opencode` entry from `opencode.json`'s `plugin` array (if
present). Other plugin entries are left intact. Add `--dry-run` to preview.
The `~/.claude/skills` tree is owned by the standard installer; run
`bash install.sh` (no flag) to manage it. See
[`opencode/README.md`](../opencode/README.md).

## OpenClaw

OpenClaw uses its native plugin manager (no repo-side removal script):

```bash
openclaw plugins uninstall token-optimizer-openclaw --dry-run   # preview
openclaw plugins uninstall token-optimizer-openclaw             # remove
```

Optional full wipe of OpenClaw session/trends data:

```bash
rm -rf "${OPENCLAW_STATE_DIR:-$HOME/.local/state/openclaw}/token-optimizer"
```

See [`openclaw/README.md`](../openclaw/README.md).

## Hermes

```bash
token-optimizer/install.sh --hermes --uninstall
```

Then remove `- token-optimizer` from `plugins.enabled` in your Hermes config
so Hermes does not log a missing-plugin warning on the next start. See
[`hermes/README.md`](../hermes/README.md).

## VS Code

The Token Optimizer status-bar extension is a standard VS Code extension.
Remove it from the Extensions UI (`Cmd/Ctrl-Shift-X` → search "Token
Optimizer" → Uninstall), or:

```bash
code --uninstall-extension alexgreensh.token-optimizer-statusline
```

The companion skill tree (`~/.claude/skills/token-optimizer`) is owned by the
Claude Code installer. Remove it via the Claude Code uninstall steps above
if you want a full wipe.
