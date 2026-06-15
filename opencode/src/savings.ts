/**
 * Realized savings engine for OpenCode — the CURRENT-VOLUME COUNTERFACTUAL.
 *
 * Parity with measure.py `_estimate_before_after_savings` and the OpenClaw port.
 * Takes the user's CURRENT window billed volume, holds it constant, and prices
 * it two ways:
 *   ACTUAL        = current model mix + current cache pattern (what it cost).
 *   COUNTERFACTUAL = same volume, priced at the PRE-TO baseline efficiency
 *                    (baseline model mix + baseline pool cache-hit). "The old way."
 * Transformation = counterfactual − actual. Volume is identical on both arms, so
 * the gap is pure efficiency (model routing + caching), never confounded by
 * workload growth — the flaw in the retired per-session era comparison.
 *
 * OpenCode persists every session to trends.db (session_log) with a pre-computed
 * cost_usd, but the counterfactual reprices the SAME volume at a DIFFERENT mix,
 * which the stored cost cannot supply. So we add a minimal per-class rate card
 * (pricing.ts) and price both arms from token volume directly.
 *
 * Three non-overlapping pools sum to the headline:
 *   1. Main routing + caching (billed session_log volume). NO winsorization /
 *      outlier drop — heavy sessions are real volume that genuinely cost more
 *      at the baseline mix; dropping them is a pure definitional undercount.
 *   2. Subagent (sidechain) routing — Claude-only. OpenCode has no Claude-style
 *      sidechains (no is_sidechain column, no subagent transcripts), so this
 *      pool is 0. DOCUMENTED GAP.
 *   3. Compression add-back — tokens TO removed from context (tool_archive,
 *      structure_map, resume_lean, checkpoint_restore, delta_read). Real volume
 *      the old way would have re-read. Directly-metered (savings_events),
 *      repriced to the baseline input mix. Disjoint from the billed pool.
 *
 * BASELINE = the frozen EARLY-window mix + pool cache-hit, used ONLY for
 * efficiency anchors (never volume). NO 95% Opus floor: OpenCode is a
 * non-Anthropic runtime, so the before-arm is priced at the user's OWN measured
 * baseline mix — never fabricated Opus they never ran.
 */
import { TrendsStore } from "./storage/trends.js";
import { price, price_cw, inputRatePerMTok, normalizeModelName, type ModelMix } from "./pricing.js";

// Constants mirror the other platforms' baseline tunables.
const BASELINE_ONBOARDING_DAYS = 1;
const BASELINE_EARLY_WINDOW_DAYS = 30;
const BASELINE_MIN_STABLE_SESSIONS = 30;
const AFTER_MIN_SESSIONS = 10;
const DAY_MS = 86_400_000;

interface SessionRec {
  ts: number; // epoch ms
  model: string; // normalized pricing key
  fi: number; // fresh input
  cr: number; // cache read
  cw: number; // cache write
  out: number; // output
  cost: number; // stored cost_usd (display / not used in counterfactual math)
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
  // Clamp every class >= 0 (corrupt-row protection, like measure.py's
  // _session_token_vector clamps). cache-write is a separate billed column in
  // session_log, kept distinct from fresh input.
  return {
    ts,
    model: normalizeModelName(String(row.model ?? "unknown")),
    fi: Math.max(0, num(row.tokens_input)),
    cr: Math.max(0, num(row.tokens_cache_read)),
    cw: Math.max(0, num(row.tokens_cache_write)),
    out: Math.max(0, num(row.tokens_output)),
    cost: num(row.cost_usd),
  };
}

/** Token-weighted model mix over a set of sessions (normalized pricing keys). */
function modelMix(recs: SessionRec[]): ModelMix {
  const byModel: Record<string, number> = {};
  let total = 0;
  for (const r of recs) {
    const t = r.fi + r.cr + r.cw + r.out;
    byModel[r.model] = (byModel[r.model] ?? 0) + t;
    total += t;
  }
  if (total <= 0) return {};
  const mix: ModelMix = {};
  for (const [m, t] of Object.entries(byModel)) mix[m] = t / total;
  return mix;
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

function mixLabel(mix: ModelMix): string {
  const top = Object.entries(mix).sort((a, b) => b[1] - a[1])[0];
  return top ? `${Math.round(top[1] * 100)}% ${top[0]}` : "n/a";
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
 * Compute realized savings via the current-volume counterfactual.
 *   `now`               injectable for testing.
 *   `rowsOverride`      bypasses the DB (raw session_log rows) for tests.
 *   `compressionOverride` injects the measured compression dollars when the DB
 *                       is bypassed (rowsOverride present); default 0.
 */
export function computeRealizedSavings(
  dataDir: string,
  days: number = 30,
  now: number = Date.now(),
  rowsOverride?: Array<Record<string, unknown>>,
  compressionOverride?: number,
): RealizedSavings {
  let rows: Array<Record<string, unknown>> = rowsOverride ?? [];
  let measuredCompression = compressionOverride ?? 0;

  if (!rowsOverride) {
    const store = new TrendsStore(dataDir);
    try {
      rows = store.getAllSessions();
      // Compression add-back (pool #3) reads metered savings_events for the window.
      measuredCompression = store.getCompressionSavings(days, now).totalCostSavedUsd;
    } catch {
      rows = [];
      measuredCompression = 0;
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

  // CURRENT window = recent sessions in lookback, strictly after the baseline
  // window (cohort separation). This is the EXACT volume held constant on both arms.
  const afterStart = Math.max(windowEnd, now - days * DAY_MS);
  const after = history.filter((r) => r.ts >= afterStart);

  // BASELINE efficiency anchors (mix + pool cache-hit), from the early window.
  const beforeMix = modelMix(before);

  if (after.length < AFTER_MIN_SESSIONS) {
    const r = NOT_READY(`building comparison (${after.length}/${AFTER_MIN_SESSIONS} recent sessions)`);
    r.installDate = installDate;
    r.beforeMixLabel = mixLabel(beforeMix);
    return r;
  }

  // Aggregate the CURRENT window billed token classes (SUM, not mean). NO
  // winsorization / outlier drop: a heavy session's tokens are real volume that
  // genuinely cost more at the baseline mix.
  let F = 0, CR = 0, CW = 0, O = 0;
  for (const r of after) { F += r.fi; CR += r.cr; CW += r.cw; O += r.out; }
  // Numeric safety: clamp the aggregated totals to finite, non-negative values
  // (mirrors measure.py's _session_token_vector clamps). Per-row reads are already
  // clamped in toRec, but a corrupt/overflowing row could still poison the SUM
  // (e.g. a row injected via rowsOverride bypassing the DB type system). A single
  // bad total must never NaN/negative the headline.
  const clampTotal = (x: number): number => (Number.isFinite(x) && x > 0 ? x : 0);
  F = clampTotal(F); CR = clampTotal(CR); CW = clampTotal(CW); O = clampTotal(O);
  const totalIn = F + CR + CW;
  if (totalIn <= 0) {
    const r = NOT_READY("no recent volume");
    r.installDate = installDate;
    r.beforeMixLabel = mixLabel(beforeMix);
    return r;
  }

  const afterMix = modelMix(after);

  // Pool cache-hit over FRESH+CACHE_READ (cache-write excluded), so caching is
  // redistributed over the pool, not over total_in — keeps the volume invariant
  // exact (cf_F + cf_CR + CW == total_in).
  const basePoolBefore = before.reduce((s, r) => s + r.fi + r.cr, 0);
  const baseHitRaw = basePoolBefore > 0
    ? before.reduce((s, r) => s + r.cr, 0) / basePoolBefore
    : null;
  const curPool = F + CR;
  const curHit = curPool > 0 ? CR / curPool : 0;
  // If the baseline pool is unusable, fall back to current hit (caching lever = 0).
  const baseHit = baseHitRaw ?? curHit;

  // MONTHLY SCALING: the token classes above are summed over the `days` window,
  // so every dollar figure derived from them is a WINDOW total, not a monthly one.
  // `days` is user-controllable (dashboard.ts clamps to [1,365]); at days=7 the raw
  // window sum is ~1/4 of the true monthly figure and at days=90 ~3x. Scale every
  // dollar OUTPUT by 30/days so the headline is monthly regardless of `days`,
  // mirroring openclaw/src/savings.ts (`monthlyScale = 30/max(1,days)`) and
  // measure.py's monthly conversion. `sessionsPerMonth` already annualizes; the
  // dollar aggregates did NOT — this closes that gap. At days=30, scale == 1, so
  // the locked days=30 worked example is byte-identical (no regression).
  const monthlyScale = 30 / Math.max(1, days);
  const m = (x: number): number => x * monthlyScale;

  // ACTUAL = current volume at the current mix: pool+output + cache-write (TTL is
  // unknown in OpenCode -> all writes treated as 5m, conservative). WINDOW figure.
  const actualWindow = price(F, CR, O, afterMix) + price_cw(CW, afterMix);
  if (actualWindow <= 0) {
    const r = NOT_READY("insufficient pricing data");
    r.installDate = installDate;
    r.beforeMixLabel = mixLabel(beforeMix);
    return r;
  }

  // COUNTERFACTUAL = same volume, caching redistributed over the F+CR pool at the
  // baseline pool-hit, pool+output priced at the baseline mix, AND cache-write
  // priced at the baseline mix (cache-write IS a routing lever). The invariant
  // cf_F + cf_CR + CW == total_in holds exactly (baseHit in [0,1] keeps cf_F >= 0).
  // WINDOW figure.
  const pool = totalIn - CW; // = F + CR
  const cfCR = baseHit * pool;
  const cfF = pool - cfCR;
  const counterfactualWindow = price(cfF, cfCR, O, beforeMix) + price_cw(CW, beforeMix);

  // COMPRESSION ADD-BACK (#3): the measured removed-token dollars repriced to the
  // baseline input mix (baseline_input_rate / current_input_rate). actual = 0 for
  // this pool (the tokens were never billed), so the whole repriced value is
  // transformation. Disjoint from the billed pool and the caching lever.
  // `measuredCompression` is already WINDOWED to `days` by getCompressionSavings
  // (cutoff = now - days*DAY), so it scales by the same 30/days as the billed arms.
  const inAfter = inputRatePerMTok(afterMix);
  const inBefore = inputRatePerMTok(beforeMix);
  const compReprice = inAfter > 0 ? inBefore / inAfter : 1;
  const compressionAddbackWindow = Math.max(0, measuredCompression * compReprice);

  // Monthly arms (scaled once, here).
  const actualMonthly = m(actualWindow);
  const counterfactualMonthly = m(counterfactualWindow);
  const compressionAddback = m(compressionAddbackWindow);

  // MAIN transformation = counterfactual − actual (clamped >= 0), monthly.
  const mainTransformation = Math.max(0, counterfactualMonthly - actualMonthly);

  // SUBAGENT pool (#2) = 0. OpenCode has NO Claude-style sidechains: no
  // is_sidechain column on session_log, no separate subagent transcripts to read.
  // This pool is a DOCUMENTED GAP — when OpenCode exposes delegated/subagent
  // sessions distinctly, port _subagent_pool_savings here.
  const subagentTransformation = 0;

  // Headline = main + subagent (0) + compression add-back (three disjoint pools), monthly.
  const transformation = mainTransformation + subagentTransformation + compressionAddback;

  const afterWindowDays = Math.max(1, (now - afterStart) / DAY_MS);
  const sessionsPerMonth = (after.length / afterWindowDays) * 30;

  // Per-session keys (dashboard reads them): the MONTHLY arms divided by the
  // current session count. before_* = "the old way", after_* = "now".
  const recentN = after.length;
  const beforeCps = counterfactualMonthly / recentN;
  const afterCps = actualMonthly / recentN;

  // Cumulative: per-session transformation across every post-baseline session.
  const allAfter = history.filter((r) => r.ts >= windowEnd);
  const cumulative = (beforeCps - afterCps) * allAfter.length;

  // --- Attribution breakdown: morph the counterfactual into the actual one lever
  // at a time so the UNROUNDED steps telescope to the headline. ---
  //   routing  = counterfactual − (cf footprint repriced at today's mix, incl CW)
  //   caching  = (cf footprint at today's mix) − actual
  // Routing carries the cache-write reprice (#2). Compression is the third lever.
  // Levers are computed on the WINDOW arms then scaled by m() so they telescope to
  // the (monthly) main transformation.
  let sRoute = 0, sCache = 0;
  if (mainTransformation > 0) {
    const vRouteWindow = price(cfF, cfCR, O, afterMix) + price_cw(CW, afterMix);
    sRoute = m(counterfactualWindow - vRouteWindow); // before->after mix (incl CW)
    sCache = m(vRouteWindow - actualWindow); // remaining gap = caching
  }

  const breakdown: SavingsBreakdownItem[] = [
    { key: "routing", label: "Smarter model routing (lighter mix)", monthlyUsd: sRoute },
    { key: "context_rereads", label: "Lighter sessions (better cache reuse)", monthlyUsd: sCache },
    { key: "subagent_routing", label: "Cheaper subagents (no sidechains on OpenCode)", monthlyUsd: subagentTransformation },
    { key: "context_compression", label: "Lighter context (fewer re-reads, metered)", monthlyUsd: compressionAddback },
  ]
    .filter((b) => b.key !== "subagent_routing" || b.monthlyUsd !== 0) // drop the always-0 sidechain lever
    .sort((a, b) => Math.abs(b.monthlyUsd) - Math.abs(a.monthlyUsd));

  return {
    ready: true,
    status: "ok",
    monthlySavingsUsd: transformation,
    savingsPerSession: beforeCps - afterCps,
    beforeCostPerSession: beforeCps,
    afterCostPerSession: afterCps,
    sessionsPerMonth,
    beforeMixLabel: mixLabel(beforeMix),
    afterMixLabel: mixLabel(afterMix),
    cumulativeSavedUsd: cumulative,
    installDate,
    breakdown,
  };
}
