// Shared data shapes for the Token Optimizer companion.
// Kept free of any `vscode` import so every consumer is unit-testable off-host.

export interface RateWindow {
  usedPercentage: number;
  resetsAt: number | null; // unix epoch seconds
}

export type RateSource = 'statusline' | 'oauth';

export interface RateLimits {
  fiveHour: RateWindow | null;
  sevenDay: RateWindow | null;
  timestamp: number; // ms since epoch — when this data was captured
  source: RateSource;
}

export interface AgentInfo {
  model: string;
  description: string;
  elapsed: string | null; // e.g. "4m30s", computed at snapshot time
}

export interface ContextQ {
  score: number;
  grade: string;
  stale: boolean;
}

export interface Warning {
  level: 'WARNING' | 'CRITICAL';
  value: number; // fill pct or tool count, depending on warning
}

// A normalized, render-ready view of the active Claude Code session.
export interface Snapshot {
  hasData: boolean;
  model: string | null;
  effort: string | null; // 'lo' | 'med' | 'hi'
  fillPct: number | null;
  fillSource: 'live-fill' | 'quality' | 'jsonl' | null;
  contextQ: ContextQ | null;
  eff: { score: number; grade: string } | null;
  fillWarning: Warning | null;
  toolWarning: Warning | null;
  compactions: number | null;
  compactionLossPct: number | null;
  durationSec: number | null;
  agents: AgentInfo[];
  rateLimits: RateLimits | null;
  rateLimitsStale: boolean; // true when shown limits are older than the stale threshold
  scoped: boolean; // true when resolved via the window's workspace folder (vs global fallback)
}

export function emptySnapshot(): Snapshot {
  return {
    hasData: false,
    model: null,
    effort: null,
    fillPct: null,
    fillSource: null,
    contextQ: null,
    eff: null,
    fillWarning: null,
    toolWarning: null,
    compactions: null,
    compactionLossPct: null,
    durationSec: null,
    agents: [],
    rateLimits: null,
    rateLimitsStale: false,
    scoped: false,
  };
}
