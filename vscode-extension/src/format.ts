// Pure presentation: Snapshot -> display strings. No `vscode` import, so the
// exact rendered text is unit-testable. statusBar.ts wraps the tooltip string
// into a MarkdownString and applies status-bar colors.
import { Snapshot } from './types';

export interface RenderOptions {
  liveUsageOn: boolean;
}

// ---- small helpers ----

export function fillBar(pct: number | null, width = 10): string {
  if (pct == null || !Number.isFinite(pct)) return '─'.repeat(width);
  // Match the terminal status line's decile flooring (statusline.js) exactly,
  // so the bar shows the same number of blocks in both surfaces.
  const filled = Math.max(0, Math.min(width, Math.floor((pct / 100) * width)));
  return '█'.repeat(filled) + '░'.repeat(width - filled);
}

// Escape characters that would break out of a markdown table cell or, worse,
// inject a clickable command: link into the isTrusted tooltip. Applied to every
// value that originates from cache files (model, effort, agent fields).
export function escapeMd(s: string): string {
  return s.replace(/[\\`*_[\]()<>|#]/g, '\\$&').replace(/\r?\n/g, ' ');
}

export function formatResetTime(epochSec: number | null): string {
  if (epochSec == null || epochSec <= 0) return '';
  const d = new Date(epochSec * 1000);
  if (isNaN(d.getTime())) return '';
  let h = d.getHours();
  const m = d.getMinutes();
  const ap = h >= 12 ? 'pm' : 'am';
  h = h % 12;
  if (h === 0) h = 12;
  return `${h}:${String(m).padStart(2, '0')}${ap}`;
}

export function formatDuration(sec: number | null): string {
  if (sec == null || !Number.isFinite(sec) || sec <= 0) return '';
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  return h > 0 ? `${h}h${m}m` : `${m}m`;
}

// Codicon reflecting health band, used in the status bar text + tooltip.
export function healthIcon(score: number | null): string {
  if (score == null) return '$(circle-outline)';
  if (score >= 85) return '$(pass-filled)';
  if (score >= 75) return '$(info)';
  if (score >= 50) return '$(warning)';
  return '$(error)';
}

// ---- status bar item texts (two adjacent items) ----

export function primaryItemText(s: Snapshot): string {
  const fill = s.fillPct != null ? `${s.fillPct}%` : '--%';
  if (!s.contextQ) return `$(pulse) ${fill}`;
  return `$(pulse) ${fill}  ${healthIcon(s.contextQ.score)} ${s.contextQ.grade}`;
}

export function secondaryItemText(s: Snapshot): string {
  const parts: string[] = [];
  if (s.eff) parts.push(`Eff ${s.eff.grade}`);
  const five = s.rateLimits?.fiveHour;
  if (five) {
    const tag = s.rateLimitsStale ? ' est' : '';
    parts.push(`5h ${Math.round(five.usedPercentage)}%${tag}`);
  }
  if (parts.length === 0) return '';
  return `$(dashboard) ${parts.join('  ')}`;
}

// ---- rich hover tooltip (markdown) ----

const ENABLE_CMD = 'command:tokenOptimizer.enableLiveUsage';
const DISABLE_CMD = 'command:tokenOptimizer.disableLiveUsage';
const DASHBOARD_CMD = 'command:tokenOptimizer.openDashboard';

export function buildTooltip(s: Snapshot, opts: RenderOptions): string {
  const lines: string[] = [];
  const title = s.model
    ? `**Token Optimizer** · ${escapeMd(s.model)}${s.effort ? ` · ${escapeMd(s.effort)}` : ''}`
    : '**Token Optimizer**';
  lines.push(title);
  lines.push('');

  if (!s.hasData) {
    lines.push('_No active Claude Code session detected yet._');
    lines.push('');
    lines.push(`[Open dashboard](${DASHBOARD_CMD})`);
    return lines.join('\n');
  }

  lines.push('| | |');
  lines.push('|---|---|');

  if (s.fillPct != null) {
    const src = s.fillSource === 'jsonl' ? ' _(panel)_' : '';
    lines.push(`| Context | \`${fillBar(s.fillPct)}\` ${s.fillPct}%${src} |`);
  }
  if (s.contextQ) {
    lines.push(`| ContextQ | ${s.contextQ.grade} (${s.contextQ.score})${s.contextQ.stale ? ' ~stale' : ''} |`);
  }
  if (s.eff) {
    lines.push(`| Efficiency | ${s.eff.grade} (${s.eff.score}) |`);
  }

  const warn = warningText(s);
  if (warn) lines.push(`| Warnings | ${warn} |`);

  if (s.compactions != null) {
    if (s.compactions > 0) {
      const loss = s.compactionLossPct != null ? ` (~${s.compactionLossPct}% lost)` : '';
      lines.push(`| Compactions | ${s.compactions}${loss} |`);
    } else {
      lines.push(`| Compactions | 0 |`);
    }
  }

  const dur = formatDuration(s.durationSec);
  if (dur) lines.push(`| Duration | ${dur} |`);

  if (s.agents.length > 0) {
    const agentStr = s.agents
      .map((a) => {
        const base = `${escapeMd(a.model)}:${escapeMd(a.description)}`.trim();
        return a.elapsed ? `${base} (${a.elapsed})` : base;
      })
      .join(', ');
    lines.push(`| Agents | ${agentStr} |`);
  }

  // Usage limits
  const five = s.rateLimits?.fiveHour;
  const seven = s.rateLimits?.sevenDay;
  if (five) {
    const reset = formatResetTime(five.resetsAt);
    const resetStr = reset ? ` · resets ${reset}` : '';
    const estStr = s.rateLimitsStale ? ' _(est)_' : '';
    lines.push(`| 5h limit | ${Math.round(five.usedPercentage)}%${resetStr}${estStr} |`);
  }
  if (seven) {
    const reset = formatResetTime(seven.resetsAt);
    const resetStr = reset ? ` · resets ${reset}` : '';
    lines.push(`| 7d limit | ${Math.round(seven.usedPercentage)}%${resetStr} |`);
  }

  lines.push('');

  // Live usage toggle — always visible, one-click. Nudge harder when the only
  // available number is stale/estimated.
  if (opts.liveUsageOn) {
    lines.push(`Live usage: **on** · [turn off](${DISABLE_CMD})`);
  } else if (s.rateLimitsStale || !five) {
    lines.push(`⚠️ Usage limit may be stale. [**Enable live usage**](${ENABLE_CMD}) for the always-fresh number (zero token cost).`);
  } else {
    lines.push(`Live usage: off · [enable](${ENABLE_CMD})`);
  }

  lines.push('');
  lines.push(`[Open dashboard](${DASHBOARD_CMD})`);
  return lines.join('\n');
}

function warningText(s: Snapshot): string | null {
  const parts: string[] = [];
  if (s.fillWarning) {
    const bang = s.fillWarning.level === 'CRITICAL' ? '!' : '';
    parts.push(`Fill ${s.fillWarning.value}%${bang}`);
  } else if (s.regimeChangeFillPct != null) {
    parts.push(`Regime ${s.regimeChangeFillPct}%`);
  }
  if (s.toolWarning) {
    const bang = s.toolWarning.level === 'CRITICAL' ? '!' : '';
    parts.push(`Tools ${s.toolWarning.value}${bang}`);
  }
  return parts.length ? parts.join(', ') : null;
}
