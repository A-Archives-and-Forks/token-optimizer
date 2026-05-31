# Token Optimizer for VS Code

See your Claude Code **context health, efficiency, and usage limits** right in the
VS Code status bar — no integrated terminal required.

Most people who live in the Claude Code extension panel never see the terminal status
line, so they fly blind on context pressure and usage limits. This companion brings the
full Token Optimizer status line into VS Code's own UI.

## What it shows

Two status-bar items, plus a rich hover tooltip:

- **Context fill %** with a bar
- **ContextQ** — resource health grade + score
- **Eff** — session efficiency grade + score
- **Warnings** — fill / tool-fatigue / regime-change
- **Compactions** + estimated context loss
- **Session duration** and **active subagents**
- **5-hour and 7-day usage limits** with reset times

Click either item to open the Token Optimizer dashboard.

## How it gets the data

It reads the files Token Optimizer already writes to `~/.claude/token-optimizer/`
(quality scores from the `UserPromptSubmit` hook, context fill and rate limits from the
status line) and tails the session transcript as a fallback. Pure file-watching, zero
network — with one optional exception below.

When you click to open the dashboard, the extension first probes a local Token Optimizer
daemon at `http://localhost:24842` and falls back to opening the generated HTML file. That
localhost probe is the only non-OAuth network activity, and it fires only on an explicit click.

## Live Usage (optional, off by default)

The authoritative 5-hour / 7-day numbers reach the status line only. When no terminal has
rendered recently, the sidecar value can go stale. Turn on **Live Usage** and the extension
reads your existing Claude subscription login (the same one Claude Code uses for `/usage`)
to fetch the always-fresh number.

- **Zero token cost.** It's a status lookup, not a Claude message. Never a billed API call.
- **One click to enable.** The tooltip shows an "Enable live usage" link whenever the number
  is stale, or run **Token Optimizer: Enable Live Usage** from the Command Palette.

## Install

**From VSIX (GitHub release):**

```
code --install-extension token-optimizer-statusline-<version>.vsix
```

or in VS Code: Extensions → `...` → **Install from VSIX**.

Works in VS Code, Cursor, and Windsurf.

## Requires

[Token Optimizer](https://github.com/alexgreensh/token-optimizer) installed in Claude Code
(it writes the data this extension reads).
