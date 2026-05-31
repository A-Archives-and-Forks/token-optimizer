// An expanded, theme-matched webview that mirrors the status-line data in a
// readable panel. Opened by clicking the status bar. Live-updates: the extension
// posts a PanelModel on every refresh and the webview re-renders. Buttons post
// messages back so the panel can drive the same commands as the tooltip links.
import * as crypto from 'crypto';
import * as vscode from 'vscode';
import { PanelModel } from './format';

export type PanelAction = 'enableLiveUsage' | 'disableLiveUsage' | 'openDashboard';

const VALID_ACTIONS = new Set<PanelAction>(['enableLiveUsage', 'disableLiveUsage', 'openDashboard']);

export class StatusPanel {
  private panel: vscode.WebviewPanel | undefined;
  private last: PanelModel | undefined;

  constructor(private onAction: (action: PanelAction) => void) {}

  // Create or reveal the panel, then paint the latest model.
  show(): void {
    if (this.panel) {
      this.panel.reveal(vscode.ViewColumn.Beside, true);
      if (this.last) this.update(this.last);
      return;
    }
    this.panel = vscode.window.createWebviewPanel(
      'tokenOptimizerStatus',
      'Token Optimizer',
      { viewColumn: vscode.ViewColumn.Beside, preserveFocus: true },
      { enableScripts: true, retainContextWhenHidden: false }
    );
    this.panel.webview.html = this.html();
    this.panel.webview.onDidReceiveMessage((m: { action?: string }) => {
      // Runtime allowlist — the TS type is erased, so don't let a compromised
      // webview drive an arbitrary tokenOptimizer.* command.
      if (m && typeof m.action === 'string' && VALID_ACTIONS.has(m.action as PanelAction)) {
        this.onAction(m.action as PanelAction);
      }
    });
    this.panel.onDidDispose(() => {
      this.panel = undefined;
    });
    if (this.last) this.update(this.last);
  }

  // Push fresh data to the panel if it's open (cheap no-op otherwise).
  update(model: PanelModel): void {
    this.last = model;
    void this.panel?.webview.postMessage(model);
  }

  dispose(): void {
    this.panel?.dispose();
    this.panel = undefined;
  }

  private html(): string {
    const nonce = makeNonce();
    const csp = `default-src 'none'; style-src 'nonce-${nonce}'; script-src 'nonce-${nonce}';`;
    return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="${csp}">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style nonce="${nonce}">
  :root { color-scheme: light dark; }
  body { font-family: var(--vscode-font-family); color: var(--vscode-foreground);
         background: var(--vscode-editor-background); padding: 18px 22px; font-size: 13px; }
  h1 { font-size: 15px; font-weight: 600; margin: 0 0 2px; }
  .sub { color: color-mix(in srgb, var(--vscode-foreground) 68%, transparent); font-size: 12px; margin-bottom: 18px; }
  .bar { font-family: var(--vscode-editor-font-family, monospace); letter-spacing: -1px; }
  /* A brighter grey than descriptionForeground (which reads too faint) — mixed
     from the theme foreground so it adapts to light/dark themes. */
  .muted { color: color-mix(in srgb, var(--vscode-foreground) 68%, transparent); }
  .row { display: flex; justify-content: space-between; align-items: baseline;
         padding: 7px 0; border-bottom: 1px solid var(--vscode-panel-border); }
  .row .k { color: color-mix(in srgb, var(--vscode-foreground) 68%, transparent); }
  .row .v { font-weight: 500; text-align: right; }
  .grade { font-weight: 600; }
  .g-S,.g-A { color: var(--vscode-charts-green, #4ec94e); }
  .g-B,.g-C { color: var(--vscode-charts-yellow, #d7b500); }
  .g-D,.g-F { color: var(--vscode-charts-red, #e35d5d); }
  .pending { color: color-mix(in srgb, var(--vscode-foreground) 60%, transparent); font-style: italic; }
  .warn { color: var(--vscode-charts-orange, #d18616); }
  .pill { display:inline-block; padding:1px 7px; border-radius:9px; font-size:11px;
          background: var(--vscode-badge-background); color: var(--vscode-badge-foreground); }
  .actions { margin-top: 18px; display: flex; gap: 8px; flex-wrap: wrap; }
  button { font-family: inherit; font-size: 12px; padding: 5px 11px; border: none; border-radius: 4px;
           cursor: pointer; background: var(--vscode-button-secondaryBackground, #3a3d41);
           color: var(--vscode-button-secondaryForeground, #fff); }
  button.primary { background: var(--vscode-button-background); color: var(--vscode-button-foreground); }
  button:hover { opacity: .9; }
  .empty { color: var(--vscode-descriptionForeground); margin-top: 24px; }
  .banner { background: var(--vscode-inputValidation-warningBackground, #5a4a1a);
            border: 1px solid var(--vscode-inputValidation-warningBorder, #b89500);
            color: var(--vscode-foreground); padding: 8px 11px; border-radius: 5px;
            font-size: 12px; margin-bottom: 14px; }
  .usagebar { height: 6px; border-radius: 3px; background: var(--vscode-panel-border); overflow:hidden; width:120px; display:inline-block; vertical-align:middle; margin-left:8px; }
  .usagefill { height: 100%; background: var(--vscode-charts-blue, #4e8ec9); }
</style>
</head>
<body>
<div id="app"><div class="empty">Waiting for an active Claude Code session…</div></div>
<script nonce="${nonce}">
  const vscode = acquireVsCodeApi();
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  const gradeClass = (g) => 'grade g-' + esc(g);
  function row(k, vHtml) { return '<div class="row"><span class="k">' + esc(k) + '</span><span class="v">' + vHtml + '</span></div>'; }
  function usage(pct) { return '<span class="usagebar"><span class="usagefill" style="width:' + Math.max(0,Math.min(100,pct)) + '%"></span></span>'; }
  function render(m) {
    const app = document.getElementById('app');
    if (!m || !m.hasData) { app.innerHTML = '<div class="empty">No active Claude Code session in this window yet.</div>'; return; }
    let h = '';
    h += '<h1>Token Optimizer</h1>';
    h += '<div class="sub">' + esc(m.model || 'Claude') + (m.effort ? ' · ' + esc(m.effort) : '') + '</div>';
    if (!m.scoped) h += '<div class="banner">⚠️ No folder open — showing the most recent session globally. Open a folder so this reflects this window\\'s session.</div>';
    if (m.fillPct != null) h += row('Context', '<span class="bar">' + esc(m.fillBar) + '</span> ' + m.fillPct + '%' + (m.fillSource === 'jsonl' ? ' <span class="pill">panel</span>' : ''));
    if (m.contextQ) h += row('ContextQ', '<span class="' + gradeClass(m.contextQ.grade) + '">' + esc(m.contextQ.grade) + ' (' + m.contextQ.score + ')</span>' + (m.contextQ.stale ? ' <span class="pending">~stale</span>' : ''));
    if (m.eff) h += row('Efficiency', '<span class="' + gradeClass(m.eff.grade) + '">' + esc(m.eff.grade) + ' (' + m.eff.score + ')</span>');
    if (m.qualityPending) h += row('ContextQ / Eff', '<span class="pending">warming up…</span>');
    if (m.warnings && m.warnings.length) h += row('Warnings', '<span class="warn">' + m.warnings.map(esc).join(', ') + '</span>');
    if (m.compactions) h += row('Compactions', m.compactions.count + (m.compactions.count > 0 && m.compactions.lossPct != null ? ' (~' + m.compactions.lossPct + '% lost)' : ''));
    if (m.duration) h += row('Duration', esc(m.duration));
    if (m.agents && m.agents.length) h += row('Agents', m.agents.map(esc).join('<br>'));
    if (m.fiveHour) h += row('5-hour limit', m.fiveHour.pct + '% ' + usage(m.fiveHour.pct) + '<br><span class="pending">resets ' + esc(m.fiveHour.reset) + (m.fiveHour.estimated ? ' (est)' : '') + '</span>');
    if (m.sevenDay) h += row('7-day limit', m.sevenDay.pct + '% ' + usage(m.sevenDay.pct) + '<br><span class="pending">resets ' + esc(m.sevenDay.reset) + '</span>');
    h += '<div class="actions">';
    h += m.liveUsageOn
      ? '<button data-act="disableLiveUsage">Live usage: on — turn off</button>'
      : '<button data-act="enableLiveUsage">Enable live usage</button>';
    h += '<button class="primary" data-act="openDashboard">Open full dashboard</button>';
    h += '</div>';
    app.innerHTML = h;
    for (const b of app.querySelectorAll('button[data-act]')) {
      b.addEventListener('click', () => vscode.postMessage({ action: b.getAttribute('data-act') }));
    }
  }
  window.addEventListener('message', (e) => render(e.data));
</script>
</body>
</html>`;
  }
}

function makeNonce(): string {
  return crypto.randomBytes(18).toString('base64').replace(/[^a-zA-Z0-9]/g, '');
}
