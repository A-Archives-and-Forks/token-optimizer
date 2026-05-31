// Owns the two status-bar items and renders a Snapshot into them.
import * as vscode from 'vscode';
import { Snapshot } from './types';
import { primaryItemText, secondaryItemText, buildTooltip } from './format';

// Clicking a status bar item opens the expanded panel (not the full browser
// dashboard — that stays a tooltip/panel link).
export const STATUS_PANEL_COMMAND = 'tokenOptimizer.showStatus';

const ERROR_BG = new vscode.ThemeColor('statusBarItem.errorBackground');
const WARNING_BG = new vscode.ThemeColor('statusBarItem.warningBackground');

export class StatusBar {
  private primary: vscode.StatusBarItem;
  private secondary: vscode.StatusBarItem;
  private lastPrimaryText = '';
  private lastSecondaryText = '';
  private lastTooltipStr = '';
  private tooltip: vscode.MarkdownString | undefined;
  private disposed = false;

  constructor() {
    this.primary = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
    this.secondary = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 99);
    this.primary.command = STATUS_PANEL_COMMAND;
    this.secondary.command = STATUS_PANEL_COMMAND;
    this.primary.name = 'Token Optimizer';
    this.secondary.name = 'Token Optimizer Usage';
  }

  render(snap: Snapshot, liveUsageOn: boolean): void {
    if (this.disposed) return; // an in-flight render may resume mid-disposal
    // Rebuild the tooltip only when its content changed (it carries live duration,
    // so it does change during an active session — but not when idle).
    const tooltipStr = buildTooltip(snap, { liveUsageOn, nowMs: Date.now() });
    if (tooltipStr !== this.lastTooltipStr || !this.tooltip) {
      const md = new vscode.MarkdownString(tooltipStr);
      md.isTrusted = true; // enable command: links in the tooltip
      md.supportThemeIcons = true;
      this.tooltip = md;
      this.lastTooltipStr = tooltipStr;
    }

    const primaryText = primaryItemText(snap);
    this.primary.tooltip = this.tooltip;
    this.primary.backgroundColor = this.criticalBg(snap);
    if (primaryText !== this.lastPrimaryText) {
      this.primary.text = primaryText;
      this.lastPrimaryText = primaryText;
    }
    this.primary.show();

    const secText = secondaryItemText(snap);
    if (secText) {
      this.secondary.tooltip = this.tooltip;
      if (secText !== this.lastSecondaryText) {
        this.secondary.text = secText;
        this.lastSecondaryText = secText;
      }
      this.secondary.show();
    } else {
      this.secondary.hide();
      this.lastSecondaryText = '';
    }
  }

  // Status bar items only support warning/error theme backgrounds. Thresholds
  // mirror the terminal status line (statusline.js): fill >=80% or ContextQ <50
  // or 5h >=90% are the alarming (red) states; milder pressure is yellow.
  private criticalBg(snap: Snapshot): vscode.ThemeColor | undefined {
    const fiveHourCritical =
      snap.rateLimits?.fiveHour != null && snap.rateLimits.fiveHour.usedPercentage >= 90;
    const fillCritical = snap.fillPct != null && snap.fillPct >= 80;
    const qualityCritical = snap.contextQ != null && snap.contextQ.score < 50;
    if (snap.fillWarning?.level === 'CRITICAL' || fiveHourCritical || fillCritical || qualityCritical) {
      return ERROR_BG;
    }
    const fillWarn = snap.fillPct != null && snap.fillPct >= 70;
    const qualityWarn = snap.contextQ != null && snap.contextQ.score < 70;
    if (snap.fillWarning?.level === 'WARNING' || fillWarn || qualityWarn) {
      return WARNING_BG;
    }
    return undefined;
  }

  dispose(): void {
    this.disposed = true;
    this.primary.dispose();
    this.secondary.dispose();
  }
}
