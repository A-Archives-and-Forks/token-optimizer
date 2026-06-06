/**
 * Realized savings engine for OpenCode (the "$11/session -> $3/session" delta).
 *
 * Parity with the Claude Code / Codex / OpenClaw before/after methodology:
 * freeze an early-usage baseline, compare it to current usage, report the
 * per-session cost delta. OpenCode already persists every session to trends.db
 * (session_log), so this reads that durable store directly -- no extra
 * persistence, and the baseline is recomputed deterministically from the fixed
 * historical window (no freeze needed; the early window never changes once past).
 *
 * MULTI-MODEL CORRECTNESS: OpenCode runs many models with very different pricing.
 * Each session_log row already carries cost_usd computed at ITS OWN model when
 * recorded, so the per-session mean is exact regardless of model mix -- no
 * blended-average-vector approximation. The waterfall uses the era's effective
 * $/token (real cost / real tokens), which encodes the mix, and telescopes
 * exactly to the headline delta.
 *
 * Distinct from forward-looking waste detection: this measures what you HAVE
 * saved, against an early baseline, using your actual recorded spend.
 */
import { TrendsStore } from "./storage/trends.js";

// Constants mirror the other platforms' baseline tunables.
const BASELINE_ONBOARDING_DAYS = 1;
const BASELINE_EARLY_WINDOW_DAYS = 30;
const BASELINE_MIN_STABLE_SESSIONS = 30;
const AFTER_MIN_SESSIONS = 10;
const WINSOR_PCT = 0.99;
const WINSOR_MIN_SAMPLE = 10;
const DAY_MS = 86_400_000;

interface SessionRec {
  ts: number; // epoch ms
  model: string;
  tokens: number; // total tokens
  cost: number; // stored cost_usd (priced at its own model when recorded)
}

function num(v: unknown): number {
  const n = typeof v === "number" ? v : parseFloat(String(v));
  return Number.isFinite(n) ? n : 0;
}

function toRec(row: Record<string, unknown>): SessionRec {
  const ts =
    num(row.created_at) > 0
      ? num(row.created_at) * 1000
      : Date.parse(String(row.date ?? "")) || 0;
  return {
    ts,
    model: String(row.model ?? "unknown"),
    tokens:
      num(row.tokens_input) +
      num(row.tokens_output) +
      num(row.tokens_cache_read) +
      num(row.tokens_cache_write),
    cost: num(row.cost_usd),
  };
}

export interface SavingsBreakdownItem {
  key: string;
  label: string;
  monthlyUsd: number;
}

export interface RealizedSavings {
  ready: boolean;
  status: string;
  monthlySavingsUsd: number;
  savingsPerSession: number;
  beforeCostPerSession: number;
  afterCostPerSession: number;
  sessionsPerMonth: number;
  beforeMixLabel: string;
  afterMixLabel: string;
  cumulativeSavedUsd: number;
  installDate: string | null;
  breakdown: SavingsBreakdownItem[];
}

interface EraStats {
  n: number;
  costPerSession: number; // winsorized mean cost
  meanTokens: number; // winsorized mean total tokens
  effRate: number; // effective $/token (cost / tokens)
  shares: Record<string, number>; // model token shares (display label)
}

function mixLabel(shares: Record<string, number>): string {
  const top = Object.entries(shares).sort((a, b) => b[1] - a[1])[0];
  return top ? `${Math.round(top[1] * 100)}% ${top[0]}` : "n/a";
}

/** Winsorize the top 1% of sessions by cost (scale cost AND tokens together so
 *  the effective rate stays consistent), then aggregate. */
function computeEra(recs: SessionRec[]): EraStats {
  const n = recs.length;
  if (n === 0) return { n: 0, costPerSession: 0, meanTokens: 0, effRate: 0, shares: {} };

  let cap = Infinity;
  if (n >= WINSOR_MIN_SAMPLE) {
    const costs = recs.map((r) => r.cost).sort((a, b) => a - b);
    // floor (not round): round((n-1)*0.99) == n-1 for n<=51, making the cap the
    // max element and winsorization a no-op for every minimum-sample window.
    // Clamp to <= n-2 so the single largest session is always above the cap.
    cap = costs[Math.min(n - 2, Math.floor((n - 1) * WINSOR_PCT))];
  }

  let costSum = 0;
  let tokSum = 0;
  const byModel: Record<string, number> = {};
  let modelTotal = 0;
  for (const r of recs) {
    const scale = r.cost > cap && r.cost > 0 ? cap / r.cost : 1;
    costSum += r.cost * scale;
    tokSum += r.tokens * scale;
    byModel[r.model] = (byModel[r.model] ?? 0) + r.tokens;
    modelTotal += r.tokens;
  }
  const shares: Record<string, number> = {};
  if (modelTotal > 0) {
    for (const [m, t] of Object.entries(byModel)) shares[m] = t / modelTotal;
  }
  return {
    n,
    costPerSession: costSum / n,
    meanTokens: tokSum / n,
    effRate: tokSum > 0 ? costSum / tokSum : 0,
    shares,
  };
}

const NOT_READY = (status: string): RealizedSavings => ({
  ready: false,
  status,
  monthlySavingsUsd: 0,
  savingsPerSession: 0,
  beforeCostPerSession: 0,
  afterCostPerSession: 0,
  sessionsPerMonth: 0,
  beforeMixLabel: "n/a",
  afterMixLabel: "n/a",
  cumulativeSavedUsd: 0,
  installDate: null,
  breakdown: [],
});

/**
 * Compute realized before/after savings from trends.db. `now` is injectable for
 * testing; `rowsOverride` lets tests bypass the DB entirely.
 */
export function computeRealizedSavings(
  dataDir: string,
  days: number = 30,
  now: number = Date.now(),
  rowsOverride?: Array<Record<string, unknown>>
): RealizedSavings {
  let rows: Array<Record<string, unknown>> = rowsOverride ?? [];
  if (!rowsOverride) {
    const store = new TrendsStore(dataDir);
    try {
      rows = store.getAllSessions();
    } catch {
      rows = [];
    } finally {
      store.close();
    }
  }

  const history = rows.map(toRec).filter((r) => r.ts > 0).sort((a, b) => a.ts - b.ts);
  if (history.length === 0) return NOT_READY("no sessions yet");

  const installTs = history[0].ts;
  const installDate = new Date(installTs).toISOString().slice(0, 10);
  const windowStart = installTs + BASELINE_ONBOARDING_DAYS * DAY_MS;
  const windowEnd = windowStart + BASELINE_EARLY_WINDOW_DAYS * DAY_MS;
  const before = history.filter((r) => r.ts >= windowStart && r.ts < windowEnd);

  if (before.length < BASELINE_MIN_STABLE_SESSIONS) {
    const r = NOT_READY(`building baseline (${before.length}/${BASELINE_MIN_STABLE_SESSIONS} early sessions)`);
    r.installDate = installDate;
    return r;
  }
  if (now < windowEnd) {
    const daysLeft = Math.ceil((windowEnd - now) / DAY_MS);
    const r = NOT_READY(`building baseline (${daysLeft}d of early window left)`);
    r.installDate = installDate;
    return r;
  }

  // After = recent sessions in lookback, strictly after the baseline window.
  const afterStart = Math.max(windowEnd, now - days * DAY_MS);
  const after = history.filter((r) => r.ts >= afterStart);
  const bs = computeEra(before);
  if (after.length < AFTER_MIN_SESSIONS) {
    const r = NOT_READY(`building comparison (${after.length}/${AFTER_MIN_SESSIONS} recent sessions)`);
    r.installDate = installDate;
    r.beforeCostPerSession = bs.costPerSession;
    r.beforeMixLabel = mixLabel(bs.shares);
    return r;
  }

  const as = computeEra(after);
  const perSession = bs.costPerSession - as.costPerSession;
  const afterWindowDays = Math.max(1, (now - afterStart) / DAY_MS);
  const sessionsPerMonth = (after.length / afterWindowDays) * 30;
  const monthly = perSession * sessionsPerMonth;

  const allAfter = history.filter((r) => r.ts >= windowEnd);
  const cumulative = perSession * allAfter.length;

  // 2-lever waterfall (telescopes exactly to perSession):
  //   routing/mix = (effRate_before - effRate_after) * meanTokens_before
  //   volume      = effRate_after * (meanTokens_before - meanTokens_after)
  const perMonth = (x: number) => x * sessionsPerMonth;
  const routing = (bs.effRate - as.effRate) * bs.meanTokens;
  const volume = as.effRate * (bs.meanTokens - as.meanTokens);
  const breakdown: SavingsBreakdownItem[] = [
    { key: "routing", label: "Model routing & pricing", monthlyUsd: perMonth(routing) },
    { key: "volume", label: "Token volume", monthlyUsd: perMonth(volume) },
  ];

  return {
    ready: true,
    status: "ok",
    monthlySavingsUsd: monthly,
    savingsPerSession: perSession,
    beforeCostPerSession: bs.costPerSession,
    afterCostPerSession: as.costPerSession,
    sessionsPerMonth,
    beforeMixLabel: mixLabel(bs.shares),
    afterMixLabel: mixLabel(as.shares),
    cumulativeSavedUsd: cumulative,
    installDate,
    breakdown,
  };
}
