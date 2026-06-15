/**
 * Realized savings engine for OpenClaw — CURRENT-VOLUME COUNTERFACTUAL.
 *
 * OpenClaw port of the locked methodology that ships on Claude Code (measure.py
 * `_estimate_before_after_savings`). Claude uses Python stdlib sqlite3 (free);
 * OpenClaw is a zero-dep Node plugin (engines >=18), so we persist to JSON under
 * ~/.openclaw/token-optimizer/ exactly like v5-features.json.
 *
 * THE HEADLINE = a current-volume counterfactual (a superset), NOT a per-session
 * early-vs-recent era comparison (that was RETIRED — confounded by volume growth).
 * We take the user's CURRENT `days`-window billed volume, hold it constant, and
 * price it two ways:
 *   ACTUAL         = current model mix + current cache pattern (what it really cost).
 *   COUNTERFACTUAL = same volume, priced at the frozen PRE-TO baseline efficiency
 *                    (baseline model mix + baseline pool cache-hit). "The old way."
 * Transformation = counterfactual - actual. Volume is identical on both arms, so
 * the gap is pure efficiency (model routing incl. cache-write + caching), never
 * confounded by workload growth.
 *
 * Three non-overlapping pools sum to the headline:
 *   1. Main routing + caching (billed session_log volume; NO winsorization/outlier
 *      drop in the headline path — deleting heavy sessions only suppresses real $).
 *   2. Subagent (sidechain) routing — Claude-only. OpenClaw has NO Claude-style
 *      sidechains, so this pool is 0 (documented gap; see headline assembly).
 *   3. Compression add-back — directly-metered tokens TO removed from context
 *      (tool_archive, structure_map, resume_lean, checkpoint_restore, delta_read),
 *      repriced at the baseline input mix. Disjoint from the billed pool.
 *
 * BASELINE MIX: OpenClaw users are non-Anthropic — they are ALWAYS priced at their
 * OWN measured/frozen mix. NO 95% Opus floor (never fabricate Opus they never ran).
 * The frozen baseline supplies efficiency anchors (mix shares + pool cache-hit) ONLY;
 * volume comes entirely from the current window.
 *
 * MULTI-MODEL CORRECTNESS: OpenClaw runs many models with very different per-token
 * pricing. We price each token class at a model MIX (share-weighted blend across the
 * priced models in that era), so a mix shift to lighter models is captured as the
 * routing lever rather than misattributed.
 *
 * Distinct from waste detectors: detectors emit FORWARD-LOOKING "you could save"
 * (monthlyWasteUsd). This measures BACKWARD-LOOKING "you have transformed".
 */
import * as fs from "fs";
import * as path from "path";
import { scanAllSessions } from "./session-parser";
import { calculateCost, normalizeModelName, getPricing } from "./pricing";
import type { AgentRun } from "./models";

// --- Constants (mirror measure.py baseline tunables) ------------------------
const BASELINE_ONBOARDING_DAYS = 1; // skip day 1 (learning-curve sessions)
const BASELINE_EARLY_WINDOW_DAYS = 30; // the "before" window after onboarding
const BASELINE_MIN_STABLE_SESSIONS = 30; // need this many before freezing
const AFTER_MIN_SESSIONS = 10; // need this many recent sessions to compare
// Winsorization is used ONLY when freezing the baseline's efficiency anchors
// (mix shares + pool cache-hit pattern); it never touches the headline VOLUME,
// which aggregates the current window with NO outlier drop (see methodology #1).
const WINSOR_PCT = 0.99; // cap the top 1% of sessions by cost (baseline anchors only)
const WINSOR_MIN_SAMPLE = 10; // below this, plain mean (cap is meaningless)
const BASELINE_VERSION = 2; // per-session pricing baseline (anchors: mix + cache-hit)
const DAY_MS = 86_400_000;
const PROXY_MODEL = "sonnet"; // price unpriced/unknown models at this rate card

// Estimated-tier savings categories: their magnitude is not directly metered, so
// they are EXCLUDED from the measured compression add-back (mirrors measure.py
// `_get_savings_summary` relocations of setup_optimization / mcp_cap / hint_followed).
const ESTIMATED_TIER_CATEGORIES = new Set<string>([
  "setup_optimization",
  "mcp_cap",
  "hint_followed",
]);

type TokenClass = "fi" | "cr" | "cw" | "out";
const CLASSES: TokenClass[] = ["fi", "cr", "cw", "out"];

// --- Storage ----------------------------------------------------------------
function storageDir(openclawDir: string): string {
  const dir = path.join(openclawDir, "token-optimizer");
  try {
    fs.mkdirSync(dir, { recursive: true });
  } catch {
    /* best effort */
  }
  return dir;
}

interface SessionRecord {
  sessionId: string;
  ts: number; // epoch ms
  model: string; // normalized
  input: number;
  output: number;
  cacheRead: number;
  cacheWrite: number;
  cacheWrite1h: number;
  cacheWrite5m: number;
  costUsd: number;
}

/**
 * Strict numeric coercion at the data boundary (F1/F2). Token fields arrive from
 * scanned AgentRuns OR persisted JSON, where a string ("999999999"), NaN, or a
 * negative is possible (hand-edited history, corrupt upstream parse). Without this,
 * `+=` concatenates a string into a giant numeric string that detonates the headline
 * (~1e40), and a negative silently zeroes/poisons the math. We parse, drop non-finite
 * to 0, and clamp to >= 0 so no string/NaN/negative ever reaches arithmetic.
 */
function numClamp(value: unknown): number {
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n) || n < 0) return 0;
  return n;
}

/**
 * Coerce + clamp a raw record's token fields into a clean SessionRecord. Mirrors
 * the Python `_session_token_vector` clamps (fresh_input >= 0, cache_write >= 0,
 * cache_write <= input, output >= 0). cacheWrite is clamped to <= input AFTER the
 * non-negative pass; the TTL buckets are then re-apportioned within the clamped
 * cacheWrite so cw1h + cw5m can never exceed it (keeps F3's pricing split honest).
 */
function sanitizeRecord(raw: {
  sessionId: unknown;
  ts: number;
  model: string;
  input: unknown;
  output: unknown;
  cacheRead: unknown;
  cacheWrite: unknown;
  cacheWrite1h: unknown;
  cacheWrite5m: unknown;
  costUsd: unknown;
}): SessionRecord {
  const input = numClamp(raw.input);
  const output = numClamp(raw.output);
  const cacheRead = numClamp(raw.cacheRead);
  // cache_write clamped non-negative, then capped at input (mirrors Python cw<=inp).
  const cacheWrite = Math.min(numClamp(raw.cacheWrite), input);
  let cw1h = numClamp(raw.cacheWrite1h);
  let cw5m = numClamp(raw.cacheWrite5m);
  // Keep the TTL split inside the clamped cacheWrite total (proportional scale-down).
  const splitSum = cw1h + cw5m;
  if (splitSum > cacheWrite && splitSum > 0) {
    const scale = cacheWrite / splitSum;
    cw1h *= scale;
    cw5m *= scale;
  }
  return {
    sessionId: String(raw.sessionId),
    ts: raw.ts,
    model: raw.model,
    input,
    output,
    cacheRead,
    cacheWrite,
    cacheWrite1h: cw1h,
    cacheWrite5m: cw5m,
    costUsd: numClamp(raw.costUsd),
  };
}

function toRecord(r: AgentRun): SessionRecord {
  return sanitizeRecord({
    sessionId: r.sessionId,
    ts: r.timestamp.getTime(),
    model: normalizeModelName(r.model) ?? r.model,
    input: r.tokens.input,
    output: r.tokens.output,
    cacheRead: r.tokens.cacheRead,
    cacheWrite: r.tokens.cacheWrite,
    cacheWrite1h: r.cacheWrite1hTokens ?? 0,
    cacheWrite5m: r.cacheWrite5mTokens ?? 0,
    costUsd: r.costUsd,
  });
}

/**
 * Merge freshly-scanned sessions into persisted history (dedup by sessionId),
 * write back, return the union. Persistence keeps the baseline alive even after
 * OpenClaw prunes old JSONL session files.
 */
function mergeAndPersistHistory(
  openclawDir: string,
  fresh: SessionRecord[]
): SessionRecord[] {
  const file = path.join(storageDir(openclawDir), "session-history.json");
  const byId = new Map<string, SessionRecord>();
  try {
    const stored = JSON.parse(fs.readFileSync(file, "utf-8")) as Array<
      Record<string, unknown>
    >;
    for (const r of stored) {
      if (!r || !r.sessionId) continue;
      // F1/F2: persisted JSON can carry string/NaN/negative token fields (hand-edited
      // or corrupt history). Re-sanitize at this boundary so no bad value ever reaches
      // the headline `+=` aggregation.
      const ts = typeof r.ts === "number" && Number.isFinite(r.ts) ? r.ts : 0;
      const rec = sanitizeRecord({
        sessionId: r.sessionId,
        ts,
        model: typeof r.model === "string" ? r.model : String(r.model),
        input: r.input,
        output: r.output,
        cacheRead: r.cacheRead,
        cacheWrite: r.cacheWrite,
        cacheWrite1h: r.cacheWrite1h,
        cacheWrite5m: r.cacheWrite5m,
        costUsd: r.costUsd,
      });
      byId.set(rec.sessionId, rec);
    }
  } catch {
    /* no history yet */
  }
  for (const r of fresh) if (r.sessionId) byId.set(r.sessionId, r); // fresh wins
  const merged = Array.from(byId.values()).sort((a, b) => a.ts - b.ts);
  try {
    const tmp = file + ".tmp";
    fs.writeFileSync(tmp, JSON.stringify(merged), { mode: 0o600 });
    fs.renameSync(tmp, file);
  } catch {
    /* best effort */
  }
  return merged;
}

// --- Per-session pricing (multi-model correct) ------------------------------
function classTokens(r: SessionRecord): Record<TokenClass, number> {
  // OpenClaw token usage is already decomposed (input is fresh; cacheRead and
  // cacheWrite are separate), so no cache_hit_rate derivation is needed.
  return { fi: r.input, cr: r.cacheRead, cw: r.cacheWrite, out: r.output };
}

/** Resolve the rate-card key for a model, proxying unpriced models. */
function priceKey(model: string, proxy: string, openclawDir: string): string {
  const pricing = getPricing(openclawDir);
  const key = pricing[model] ? model : normalizeModelName(model) ?? model;
  return pricing[key] ? key : proxy;
}

/**
 * Per-class cost of ONE session, priced at its OWN model (proxy if unpriced).
 * Returned per class so the waterfall can use real per-class effective rates.
 */
function sessionClassCost(
  r: SessionRecord,
  proxy: string,
  openclawDir: string
): Record<TokenClass, number> {
  const model = priceKey(r.model, proxy, openclawDir);
  const t = classTokens(r);
  // Isolate each class by zeroing the others (calculateCost is linear in tokens,
  // so per-class costs sum to the full session cost).
  const split = { cacheWrite1hTokens: r.cacheWrite1h, cacheWrite5m: r.cacheWrite5m } as {
    cacheWrite1hTokens?: number;
    cacheWrite5mTokens?: number;
  };
  return {
    fi: calculateCost({ input: t.fi, output: 0, cacheRead: 0, cacheWrite: 0 }, model, openclawDir),
    cr: calculateCost({ input: 0, output: 0, cacheRead: t.cr, cacheWrite: 0 }, model, openclawDir),
    cw: calculateCost(
      { input: 0, output: 0, cacheRead: 0, cacheWrite: t.cw },
      model,
      openclawDir,
      { cacheWrite1hTokens: r.cacheWrite1h, cacheWrite5mTokens: r.cacheWrite5m }
    ),
    out: calculateCost({ input: 0, output: t.out, cacheRead: 0, cacheWrite: 0 }, model, openclawDir),
  };
}

function modelShares(records: SessionRecord[]): Record<string, number> {
  const byModel: Record<string, number> = {};
  let total = 0;
  for (const r of records) {
    const t = r.input + r.output + r.cacheRead + r.cacheWrite;
    byModel[r.model] = (byModel[r.model] ?? 0) + t;
    total += t;
  }
  if (total <= 0) return {};
  const shares: Record<string, number> = {};
  for (const [m, t] of Object.entries(byModel)) shares[m] = t / total;
  return shares;
}

function dominantPricedModel(records: SessionRecord[], openclawDir: string): string {
  const pricing = getPricing(openclawDir);
  const byModel: Record<string, number> = {};
  for (const r of records) {
    const key = pricing[r.model] ? r.model : normalizeModelName(r.model) ?? r.model;
    if (pricing[key]) byModel[key] = (byModel[key] ?? 0) + (r.input + r.output + r.cacheRead + r.cacheWrite);
  }
  let best = PROXY_MODEL;
  let bestTok = -1;
  for (const [m, t] of Object.entries(byModel)) if (t > bestTok) { best = m; bestTok = t; }
  return best;
}

// --- Mix-weighted pricing (counterfactual primitives) -----------------------
// Price token classes at a MODEL MIX (share-weighted blend across the priced
// models in an era). calculateCost is per-token linear, so we blend the per-MTok
// rates by share and apply once. Unpriced shares fall back to the proxy model so
// a mix dominated by an unpriced model still produces a non-zero price.

/** Resolve a share map into [pricedModel, share] pairs, proxying unpriced models. */
function pricedShares(
  shares: Record<string, number>,
  proxy: string,
  openclawDir: string
): Array<[string, number]> {
  const pricing = getPricing(openclawDir);
  const blended: Record<string, number> = {};
  for (const [m, s] of Object.entries(shares)) {
    if (!s || s <= 0) continue;
    const key = pricing[m] ? m : normalizeModelName(m) ?? m;
    const model = pricing[key] ? key : proxy;
    blended[model] = (blended[model] ?? 0) + s;
  }
  const items = Object.entries(blended).filter(([, s]) => s > 0) as Array<[string, number]>;
  if (items.length === 0) return [[proxy, 1]];
  return items;
}

/**
 * Price the fresh + cache_read pool and output at a model mix (NO cache-write).
 * Share-weighted blend over the era's priced models.
 */
function pricePool(
  fi: number,
  cr: number,
  out: number,
  shares: Record<string, number>,
  proxy: string,
  openclawDir: string
): number {
  const items = pricedShares(shares, proxy, openclawDir);
  const tot = items.reduce((a, [, s]) => a + s, 0);
  let cost = 0;
  for (const [model, s] of items) {
    cost +=
      (s / tot) *
      calculateCost({ input: fi, output: out, cacheRead: cr, cacheWrite: 0 }, model, openclawDir);
  }
  return cost;
}

/**
 * Price cache-write at a model mix, TTL-aware (1h writes bill at 2x input, 5m at
 * 1.25x). Cache-write IS a routing lever (#1): it is billed at the WRITING model's
 * rate, so it is priced at each arm's OWN mix (baseline mix in the counterfactual,
 * actual mix in actual) — exactly like fresh/cache_read/output.
 */
function priceCacheWrite(
  cw: number,
  cw1h: number,
  cw5m: number,
  shares: Record<string, number>,
  proxy: string,
  openclawDir: string
): number {
  const items = pricedShares(shares, proxy, openclawDir);
  const tot = items.reduce((a, [, s]) => a + s, 0);
  let cost = 0;
  for (const [model, s] of items) {
    cost +=
      (s / tot) *
      calculateCost(
        { input: 0, output: 0, cacheRead: 0, cacheWrite: cw },
        model,
        openclawDir,
        { cacheWrite1hTokens: cw1h, cacheWrite5mTokens: cw5m }
      );
  }
  return cost;
}

/** Cost of 1M fresh-input tokens at a model mix (the "input rate" for repricing). */
function inputRate(
  shares: Record<string, number>,
  proxy: string,
  openclawDir: string
): number {
  return pricePool(1_000_000, 0, 0, shares, proxy, openclawDir);
}

// --- Era statistics ---------------------------------------------------------
interface EraStats {
  n: number;
  costPerSession: number; // winsorized mean of per-session cost
  meanTokens: Record<TokenClass, number>; // winsorized mean per-session tokens
  effRate: Record<TokenClass, number>; // effective $/token observed (cost/token)
  shares: Record<string, number>; // model token shares (display label)
}

/**
 * Aggregate an era. Each session is priced at its own model; then the top 1% of
 * sessions BY COST are winsorized (scaled down) so one runaway session can't
 * dominate. Effective per-class rates are derived from the winsorized totals so
 * the waterfall telescopes exactly to costPerSession.
 */
function computeEraStats(
  records: SessionRecord[],
  openclawDir: string,
  proxyOverride?: string
): EraStats {
  const zero: Record<TokenClass, number> = { fi: 0, cr: 0, cw: 0, out: 0 };
  if (records.length === 0) {
    return { n: 0, costPerSession: 0, meanTokens: { ...zero }, effRate: { ...zero }, shares: {} };
  }
  // A SINGLE proxy (from full history) must price unpriced models in BOTH eras,
  // otherwise the dominant priced model can differ between before/after and
  // create a spurious cost delta for unpriced sessions.
  const proxy = proxyOverride ?? dominantPricedModel(records, openclawDir);

  // Per-session: class tokens, class costs, total cost.
  const rows = records.map((r) => {
    const tok = classTokens(r);
    const cost = sessionClassCost(r, proxy, openclawDir);
    const totalCost = cost.fi + cost.cr + cost.cw + cost.out;
    return { tok, cost, totalCost };
  });

  // Winsorize the top 1% of sessions by total cost.
  const n = rows.length;
  let cap = Infinity;
  if (n >= WINSOR_MIN_SAMPLE) {
    const costs = rows.map((r) => r.totalCost).sort((a, b) => a - b);
    // floor (not round): round((n-1)*0.99) == n-1 for n<=51, which makes the cap
    // the max element and winsorization a no-op for every minimum-sample window.
    // floor leaves the top ~1% genuinely cappable. Clamp to <= n-2 so at least
    // the single largest session is always above the cap.
    cap = costs[Math.min(n - 2, Math.floor((n - 1) * WINSOR_PCT))];
  }

  const tokSum: Record<TokenClass, number> = { ...zero };
  const costSum: Record<TokenClass, number> = { ...zero };
  for (const row of rows) {
    const scale = row.totalCost > cap && row.totalCost > 0 ? cap / row.totalCost : 1;
    for (const c of CLASSES) {
      tokSum[c] += row.tok[c] * scale;
      costSum[c] += row.cost[c] * scale;
    }
  }

  const meanTokens: Record<TokenClass, number> = { ...zero };
  const effRate: Record<TokenClass, number> = { ...zero };
  let costPerSession = 0;
  for (const c of CLASSES) {
    meanTokens[c] = tokSum[c] / n;
    effRate[c] = tokSum[c] > 0 ? costSum[c] / tokSum[c] : 0;
    costPerSession += costSum[c] / n; // == Σ effRate[c]*meanTokens[c]
  }
  return { n, costPerSession, meanTokens, effRate, shares: modelShares(records) };
}

// --- Baseline freeze --------------------------------------------------------
interface FrozenBaseline {
  version: number;
  frozenAt: number;
  installTs: number;
  windowStart: number;
  windowEnd: number;
  stats: EraStats;
  proxy: string; // proxy model used to price unpriced sessions (reused for after)
}

function baselinePath(openclawDir: string): string {
  return path.join(storageDir(openclawDir), "baseline-state.json");
}

function loadFrozenBaseline(openclawDir: string): FrozenBaseline | null {
  try {
    const b = JSON.parse(fs.readFileSync(baselinePath(openclawDir), "utf-8")) as FrozenBaseline;
    if (b && b.version === BASELINE_VERSION) return b;
  } catch {
    /* none */
  }
  return null;
}

function getOrComputeBaseline(
  openclawDir: string,
  history: SessionRecord[],
  now: number,
  proxy: string
): { baseline: FrozenBaseline | null; reason: string } {
  const frozen = loadFrozenBaseline(openclawDir);
  if (frozen) return { baseline: frozen, reason: "frozen" };
  if (history.length === 0) return { baseline: null, reason: "no history" };

  const installTs = history[0].ts;
  const windowStart = installTs + BASELINE_ONBOARDING_DAYS * DAY_MS;
  const windowEnd = windowStart + BASELINE_EARLY_WINDOW_DAYS * DAY_MS;
  const before = history.filter((r) => r.ts >= windowStart && r.ts < windowEnd);

  if (before.length < BASELINE_MIN_STABLE_SESSIONS) {
    return { baseline: null, reason: `building baseline (${before.length}/${BASELINE_MIN_STABLE_SESSIONS} early sessions)` };
  }
  if (now < windowEnd) {
    const daysLeft = Math.ceil((windowEnd - now) / DAY_MS);
    return { baseline: null, reason: `building baseline (${daysLeft}d of early window left)` };
  }

  const baseline: FrozenBaseline = {
    version: BASELINE_VERSION,
    frozenAt: now,
    installTs,
    windowStart,
    windowEnd,
    stats: computeEraStats(before, openclawDir, proxy),
    proxy,
  };
  try {
    const tmp = baselinePath(openclawDir) + ".tmp";
    fs.writeFileSync(tmp, JSON.stringify(baseline), { mode: 0o600 });
    fs.renameSync(tmp, baselinePath(openclawDir));
  } catch {
    /* best effort */
  }
  return { baseline, reason: "frozen" };
}

// ---------------------------------------------------------------------------
// Savings-events aggregation (grouped by event_type, no allowlist)
// ---------------------------------------------------------------------------

/**
 * Friendly labels for known savings event types (mirrors Python
 * _SAVINGS_CATEGORY_LABELS in measure.py). Unknown types fall back to
 * title-case of the raw key.
 */
const SAVINGS_CATEGORY_LABELS: Record<string, string> = {
  resume_lean: "Lean resumes",
  checkpoint_restore: "Checkpoint restores",
  hint_followed: "Hints followed",
  tool_archive: "Tool replacements",
  structural_savings: "Structural (cumulative)",
};

export function savingsCategoryLabel(eventType: string): string {
  if (SAVINGS_CATEGORY_LABELS[eventType]) return SAVINGS_CATEGORY_LABELS[eventType];
  // Graceful fallback: title-case the raw key (underscores -> spaces)
  return eventType
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export interface SavingsEventCategory {
  eventType: string;
  label: string;
  count: number;
  tokensSaved: number;
  costSavedUsd: number;
}

export interface SavingsEventsSummary {
  categories: SavingsEventCategory[];
  totalTokensSaved: number;
  totalCostSavedUsd: number;
  totalCount: number;
}

/**
 * Optional lookback window for savings-events reads. Mirrors Python
 * `_get_savings_summary(days=days)`, which filters `timestamp >= now - days`.
 * When omitted, ALL events are summed (the display/grouping callers want lifetime
 * totals); the compression add-back caller passes a window so a stale event is not
 * counted and then monthly-scaled.
 */
export interface SavingsEventsWindow {
  days: number;
  now?: number;
}

/**
 * Read savings-events.jsonl, group by event_type, and return per-category
 * totals + a grand total. No allowlist: every event_type in the file surfaces.
 * Returns an empty summary (not an error) when the file is missing. When `window`
 * is given, only events whose `timestamp` is within `now - days` are counted.
 */
export function readSavingsEventsByCategory(
  openclawDir?: string,
  window?: SavingsEventsWindow
): SavingsEventsSummary {
  const dir = openclawDir
    ? path.join(openclawDir, "token-optimizer")
    : path.join(
        process.env.HOME ?? process.env.USERPROFILE ?? "",
        ".openclaw",
        "token-optimizer"
      );
  const filePath = path.join(dir, "savings-events.jsonl");

  const byType = new Map<
    string,
    { count: number; tokensSaved: number; costSavedUsd: number }
  >();

  // F4: compute the lookback cutoff (ms epoch). Rows older than the cutoff are
  // skipped so the windowed sum matches the `days` lookback the caller scales by.
  const cutoff =
    window && window.days > 0
      ? (window.now ?? Date.now()) - window.days * DAY_MS
      : null;

  try {
    const raw = fs.readFileSync(filePath, "utf-8");
    for (const line of raw.split("\n")) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      let row: Record<string, unknown>;
      try {
        row = JSON.parse(trimmed) as Record<string, unknown>;
      } catch {
        continue;
      }
      const et = typeof row.event_type === "string" ? row.event_type : null;
      if (!et) continue;
      // Window filter: drop events outside the lookback. A row with a missing or
      // unparseable timestamp is treated as out-of-window (excluded) when a window
      // is requested, so an undated/stale event can't inflate the windowed sum.
      if (cutoff !== null) {
        const tsRaw = row.timestamp;
        const ts = typeof tsRaw === "string" ? Date.parse(tsRaw) : NaN;
        if (!Number.isFinite(ts) || ts < cutoff) continue;
      }
      const tokens =
        typeof row.tokens_saved === "number" ? row.tokens_saved : 0;
      const cost =
        typeof row.cost_saved_usd === "number" ? row.cost_saved_usd : 0;
      const entry = byType.get(et) ?? { count: 0, tokensSaved: 0, costSavedUsd: 0 };
      entry.count++;
      entry.tokensSaved += tokens;
      entry.costSavedUsd += cost;
      byType.set(et, entry);
    }
  } catch {
    /* file missing or unreadable — return empty summary */
  }

  const categories: SavingsEventCategory[] = Array.from(byType.entries())
    .map(([eventType, agg]) => ({
      eventType,
      label: savingsCategoryLabel(eventType),
      count: agg.count,
      tokensSaved: agg.tokensSaved,
      costSavedUsd: agg.costSavedUsd,
    }))
    .sort((a, b) => b.tokensSaved - a.tokensSaved);

  const totalTokensSaved = categories.reduce((s, c) => s + c.tokensSaved, 0);
  const totalCostSavedUsd = categories.reduce((s, c) => s + c.costSavedUsd, 0);
  const totalCount = categories.reduce((s, c) => s + c.count, 0);

  return { categories, totalTokensSaved, totalCostSavedUsd, totalCount };
}

/**
 * Sum the MEASURED compression / volume-reduction savings (cost_saved_usd) over
 * the window, EXCLUDING estimated-tier categories (setup_optimization, mcp_cap,
 * hint_followed) — mirrors measure.py `_get_savings_summary` relocations. This is
 * the directly-metered floor of the compression add-back pool (#3); the only
 * estimated step is the reprice to the baseline input mix, applied by the caller.
 */
function measuredCompressionUsd(
  openclawDir: string,
  window: SavingsEventsWindow
): number {
  const summary = readSavingsEventsByCategory(openclawDir, window);
  let total = 0;
  for (const cat of summary.categories) {
    if (ESTIMATED_TIER_CATEGORIES.has(cat.eventType)) continue;
    total += cat.costSavedUsd;
  }
  return Math.max(0, total);
}

// --- Public result ----------------------------------------------------------
export interface SavingsBreakdownItem {
  key: string;
  label: string;
  monthlyUsd: number;
}

export interface RealizedSavings {
  ready: boolean;
  status: string;
  /** Headline transformation = main + subagent(0) + compression add-back, monthly. */
  monthlySavingsUsd: number;
  savingsPerSession: number;
  /** "The old way" cost per session (counterfactual / current session count). */
  beforeCostPerSession: number;
  /** "Now" cost per session (actual / current session count). */
  afterCostPerSession: number;
  sessionsPerMonth: number;
  beforeMixLabel: string;
  afterMixLabel: string;
  cumulativeSavedUsd: number;
  installDate: string | null;
  breakdown: SavingsBreakdownItem[];
  // --- Counterfactual detail (parity with measure.py) ---
  /** Combined counterfactual arm = main cf + compression add-back, monthly USD. */
  counterfactualMonthlyUsd: number;
  /** Actual arm = current volume at current mix, monthly USD. */
  actualMonthlyUsd: number;
  mainTransformationUsd: number;
  /** Always 0 on OpenClaw — no Claude-style sidechains (documented gap). */
  subagentTransformationUsd: number;
  compressionTransformationUsd: number;
  /** Directly-metered compression $ before the baseline-mix reprice. */
  compressionMeasuredUsd: number;
  transformationPct: number;
  beforeOpus: number;
  afterOpus: number;
}

function mixLabel(shares: Record<string, number>): string {
  const top = Object.entries(shares).sort((a, b) => b[1] - a[1])[0];
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
  counterfactualMonthlyUsd: 0,
  actualMonthlyUsd: 0,
  mainTransformationUsd: 0,
  subagentTransformationUsd: 0,
  compressionTransformationUsd: 0,
  compressionMeasuredUsd: 0,
  transformationPct: 0,
  beforeOpus: 0,
  afterOpus: 0,
});

/**
 * Compute the realized current-volume counterfactual transformation. `now` is
 * injectable for testing. See the module header for the full methodology.
 */
export function computeRealizedSavings(
  openclawDir: string,
  days: number = 30,
  now: number = Date.now()
): RealizedSavings {
  let fresh: SessionRecord[] = [];
  try {
    fresh = scanAllSessions(openclawDir, 36500).map(toRecord);
  } catch {
    fresh = [];
  }
  const history = mergeAndPersistHistory(openclawDir, fresh);
  if (history.length === 0) return NOT_READY("no sessions yet");

  // One proxy for unpriced models, derived from full history, used in both eras.
  const globalProxy = dominantPricedModel(history, openclawDir);
  const { baseline, reason } = getOrComputeBaseline(openclawDir, history, now, globalProxy);
  const installDate = new Date(history[0].ts).toISOString().slice(0, 10);
  if (!baseline) {
    const r = NOT_READY(reason);
    r.installDate = installDate;
    return r;
  }

  // "After" = recent sessions in the lookback window, strictly after the
  // baseline window (cohort separation: never compare the user to themselves).
  const afterStart = Math.max(baseline.windowEnd, now - days * DAY_MS);
  const after = history.filter((r) => r.ts >= afterStart);
  if (after.length < AFTER_MIN_SESSIONS) {
    const r = NOT_READY(`building comparison (${after.length}/${AFTER_MIN_SESSIONS} recent sessions)`);
    r.installDate = installDate;
    r.beforeCostPerSession = baseline.stats.costPerSession;
    r.beforeMixLabel = mixLabel(baseline.stats.shares);
    return r;
  }

  const bs = baseline.stats;
  // Reuse the baseline's stored proxy so before/after price unpriced models
  // identically (a frozen baseline keeps its original proxy).
  const proxy = baseline.proxy;
  // The "after" era's mix shares are needed for the ACTUAL arm. computeEraStats
  // is used here ONLY for the mix-share anchor (efficiency), not for volume.
  const as = computeEraStats(after, openclawDir, proxy);

  // EFFICIENCY ANCHORS from the frozen baseline (mix shares + pool cache-hit).
  // OpenClaw is non-Anthropic -> ALWAYS the user's OWN measured mix. NO 95% Opus
  // floor (never fabricate Opus they never ran). before_shares = baseline mix;
  // if the baseline mix is empty, fall back to the actual mix (routing lever -> 0).
  const beforeShares =
    Object.keys(bs.shares).length > 0 ? bs.shares : as.shares;
  const afterShares = as.shares;

  // Baseline pool cache-hit, defined over the FRESH+CACHE_READ POOL only (cache
  // write excluded), from the baseline's winsorized typical-session token vector.
  // base_hit = baseline cache_read / (baseline fresh + baseline cache_read).
  const bPool = bs.meanTokens.fi + bs.meanTokens.cr;
  let baseHit: number | null = bPool > 0 ? bs.meanTokens.cr / bPool : null;

  // CURRENT window = the `after` sessions, aggregated into billed token classes
  // (SUM, not mean). NO winsorization / NO outlier drop (methodology #1): a heavy
  // session's tokens are REAL volume that genuinely cost more at the baseline mix,
  // so dropping them is a pure definitional undercount. This is the EXACT volume
  // held constant across both arms.
  let F = 0, CR = 0, CW = 0, O = 0, CW1h = 0, CW5m = 0;
  for (const r of after) {
    F += r.input;
    CR += r.cacheRead;
    CW += r.cacheWrite;
    O += r.output;
    CW1h += r.cacheWrite1h;
    CW5m += r.cacheWrite5m;
  }
  // Apportion any cache-write not split into TTL buckets as 5m (conservative).
  const splitSum = CW1h + CW5m;
  if (splitSum < CW) CW5m += CW - splitSum;

  const totalIn = F + CR + CW;
  if (totalIn <= 0) {
    const r = NOT_READY("no recent billed volume");
    r.installDate = installDate;
    return r;
  }

  const curPool = F + CR;
  const curHit = curPool > 0 ? CR / curPool : 0;
  if (baseHit === null) baseHit = curHit; // caching lever neutralized; conservative

  const afterWindowDays = Math.max(1, (now - afterStart) / DAY_MS);
  const sessionsPerMonth = (after.length / afterWindowDays) * 30;
  // Window is `days`; scale aggregate window cost to a monthly figure.
  const monthlyScale = 30 / Math.max(1, days);
  const m = (x: number) => x * monthlyScale;

  // ACTUAL = current volume at the current mix. Pool (fresh + cache_read) + output,
  // PLUS cache-write (TTL-aware) priced at the actual mix.
  const actualWindow =
    pricePool(F, CR, O, afterShares, proxy, openclawDir) +
    priceCacheWrite(CW, CW1h, CW5m, afterShares, proxy, openclawDir);
  if (actualWindow <= 0) {
    const r = NOT_READY("insufficient billed cost");
    r.installDate = installDate;
    return r;
  }

  // COUNTERFACTUAL ("the old way") = SAME volume, caching redistributed over the
  // FRESH+CACHE_READ POOL at the baseline pool-hit rate, pool priced at the baseline
  // mix, AND cache-write priced at the baseline mix (cache-write IS a routing lever).
  // CW count unchanged. Volume invariant cf_F + cf_CR + CW == totalIn holds exactly,
  // and base_hit in [0,1] keeps cf_F non-negative.
  const pool = totalIn - CW; // = F + CR
  const cfCR = baseHit * pool;
  const cfF = pool - cfCR;
  const counterfactualWindow =
    pricePool(cfF, cfCR, O, beforeShares, proxy, openclawDir) +
    priceCacheWrite(CW, CW1h, CW5m, beforeShares, proxy, openclawDir);

  // COMPRESSION ADD-BACK (#3): directly-metered tokens TO removed from context.
  // DISJOINT from the billed pool (already removed before billing). Reprice the
  // measured $ to the baseline input mix; actual for this pool is 0, so the whole
  // repriced value is transformation. Clamp >= 0.
  // F4: window the compression events to the SAME `days` lookback as the billed
  // volume (mirrors Python `_get_savings_summary(days=days)`), so a stale event
  // outside the window is not summed and then monthly-scaled.
  const compMeasured = measuredCompressionUsd(openclawDir, { days, now });
  const inAfter = inputRate(afterShares, proxy, openclawDir);
  const inBefore = inputRate(beforeShares, proxy, openclawDir);
  const compReprice = inAfter > 0 ? inBefore / inAfter : 1;
  const compressionAddback = Math.max(0, compMeasured * compReprice);

  // MAIN transformation (monthly). Clamp >= 0.
  const mainTransformation = Math.max(0, m(counterfactualWindow - actualWindow));

  // SUBAGENT pool (#2) = 0. OpenClaw has NO Claude-style sidechains (subagent
  // transcripts), so there is no separate sidechain pool to price. Documented gap.
  const subagentTransformation = 0;

  const compressionMonthly = m(compressionAddback);
  const transformation = mainTransformation + subagentTransformation + compressionMonthly;

  // Combined arms (headline spans the main pool + the compression add-back; the
  // compression pool's actual is 0, so its counterfactual == its contribution).
  const actualMonthly = m(actualWindow);
  const counterfactualMonthly = m(counterfactualWindow) + compressionMonthly;
  const transformationPct =
    counterfactualMonthly > 0 ? transformation / counterfactualMonthly : 0;

  // --- Attribution breakdown (waterfall over the efficiency levers) ---
  // Morph the counterfactual into the actual one lever at a time. Routing first
  // (baseline footprint INCLUDING cache-write, repriced from baseline mix to the
  // actual mix), then caching at the actual mix. The two UNROUNDED steps telescope
  // exactly to the MAIN transformation; compression is the third (disjoint) lever.
  let sRoute = 0, sCache = 0;
  if (mainTransformation > 0) {
    const vRoute =
      pricePool(cfF, cfCR, O, afterShares, proxy, openclawDir) +
      priceCacheWrite(CW, CW1h, CW5m, afterShares, proxy, openclawDir);
    sRoute = m(counterfactualWindow - vRoute); // before->after mix (incl. cache-write)
    sCache = m(vRoute - actualWindow); // remaining gap = caching
  }
  const breakdown: SavingsBreakdownItem[] = [
    { key: "routing", label: "Smarter model routing (incl. cache-write)", monthlyUsd: round2(sRoute) },
    { key: "context_rereads", label: "Lighter sessions (better cache reuse)", monthlyUsd: round2(sCache) },
    // Subagent lever omitted (OpenClaw has no sidechains; always 0).
    { key: "context_compression", label: "Lighter context (metered removals)", monthlyUsd: round2(compressionMonthly) },
  ];

  // Cumulative: apply the per-session transformation rate across every
  // post-baseline session (transformation per current session * total sessions).
  const perSessionTransformation = transformation / Math.max(1, after.length);
  const allAfter = history.filter((r) => r.ts >= baseline.windowEnd);
  const cumulative = perSessionTransformation * allAfter.length;

  // Per-session arms (the dashboard reads before/after CPS): divide the monthly
  // arms by the current session count so the per-session panel stays sensible.
  const beforeCps = counterfactualMonthly / after.length;
  const afterCps = actualMonthly / after.length;

  return {
    ready: true,
    status: "ok",
    monthlySavingsUsd: round2(transformation),
    savingsPerSession: round4(beforeCps - afterCps),
    beforeCostPerSession: round4(beforeCps),
    afterCostPerSession: round4(afterCps),
    sessionsPerMonth,
    beforeMixLabel: mixLabel(beforeShares),
    afterMixLabel: mixLabel(afterShares),
    cumulativeSavedUsd: round2(cumulative),
    installDate,
    breakdown,
    counterfactualMonthlyUsd: round2(counterfactualMonthly),
    actualMonthlyUsd: round2(actualMonthly),
    mainTransformationUsd: round2(mainTransformation),
    subagentTransformationUsd: subagentTransformation,
    compressionTransformationUsd: round2(compressionMonthly),
    compressionMeasuredUsd: round2(compMeasured),
    transformationPct: round4(transformationPct),
    beforeOpus: round4(beforeShares.opus ?? 0),
    afterOpus: round4(afterShares.opus ?? 0),
  };
}

function round2(x: number): number {
  return Math.round(x * 100) / 100;
}
function round4(x: number): number {
  return Math.round(x * 10000) / 10000;
}
