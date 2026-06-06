"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.computeRealizedSavings = computeRealizedSavings;
/**
 * Realized savings engine for OpenClaw (the "$11/session -> $3/session" delta).
 *
 * OpenClaw port of the before/after methodology that ships on Claude Code and
 * Codex. Claude/Codex use Python stdlib sqlite3 (free); OpenClaw is a zero-dep
 * Node plugin (engines >=18), so we persist to JSON under
 * ~/.openclaw/token-optimizer/ exactly like v5-features.json.
 *
 * MULTI-MODEL CORRECTNESS: OpenClaw/OpenCode run many models across parallel
 * agents with very different per-token pricing. So we DO NOT price a single
 * blended "average session" (that misattributes when models have different
 * session sizes). Instead every session is priced at ITS OWN model's rate card,
 * then averaged. The waterfall is built from per-token-class *effective* rates
 * (cost/token actually observed per era), which encode the model mix, and
 * telescopes exactly to the headline delta.
 *
 * Distinct from waste detectors: detectors emit FORWARD-LOOKING "you could save"
 * (monthlyWasteUsd). This measures BACKWARD-LOOKING "you have saved" by comparing
 * a frozen early-usage baseline against current usage, both priced at today's
 * rate cards (so the delta reflects YOUR behavior + routing, not vendor price
 * drift).
 */
const fs = __importStar(require("fs"));
const path = __importStar(require("path"));
const session_parser_1 = require("./session-parser");
const pricing_1 = require("./pricing");
// --- Constants (mirror measure.py baseline tunables) ------------------------
const BASELINE_ONBOARDING_DAYS = 1; // skip day 1 (learning-curve sessions)
const BASELINE_EARLY_WINDOW_DAYS = 30; // the "before" window after onboarding
const BASELINE_MIN_STABLE_SESSIONS = 30; // need this many before freezing
const AFTER_MIN_SESSIONS = 10; // need this many recent sessions to compare
const WINSOR_PCT = 0.99; // cap the top 1% of sessions by cost
const WINSOR_MIN_SAMPLE = 10; // below this, plain mean (cap is meaningless)
const BASELINE_VERSION = 2; // bumped: per-session pricing + effRate waterfall
const DAY_MS = 86_400_000;
const PROXY_MODEL = "sonnet"; // price unpriced/unknown models at this rate card
const CLASSES = ["fi", "cr", "cw", "out"];
// --- Storage ----------------------------------------------------------------
function storageDir(openclawDir) {
    const dir = path.join(openclawDir, "token-optimizer");
    try {
        fs.mkdirSync(dir, { recursive: true });
    }
    catch {
        /* best effort */
    }
    return dir;
}
function toRecord(r) {
    return {
        sessionId: r.sessionId,
        ts: r.timestamp.getTime(),
        model: (0, pricing_1.normalizeModelName)(r.model) ?? r.model,
        input: r.tokens.input,
        output: r.tokens.output,
        cacheRead: r.tokens.cacheRead,
        cacheWrite: r.tokens.cacheWrite,
        cacheWrite1h: r.cacheWrite1hTokens ?? 0,
        cacheWrite5m: r.cacheWrite5mTokens ?? 0,
        costUsd: r.costUsd,
    };
}
/**
 * Merge freshly-scanned sessions into persisted history (dedup by sessionId),
 * write back, return the union. Persistence keeps the baseline alive even after
 * OpenClaw prunes old JSONL session files.
 */
function mergeAndPersistHistory(openclawDir, fresh) {
    const file = path.join(storageDir(openclawDir), "session-history.json");
    const byId = new Map();
    try {
        const stored = JSON.parse(fs.readFileSync(file, "utf-8"));
        for (const r of stored)
            if (r && r.sessionId)
                byId.set(r.sessionId, r);
    }
    catch {
        /* no history yet */
    }
    for (const r of fresh)
        if (r.sessionId)
            byId.set(r.sessionId, r); // fresh wins
    const merged = Array.from(byId.values()).sort((a, b) => a.ts - b.ts);
    try {
        const tmp = file + ".tmp";
        fs.writeFileSync(tmp, JSON.stringify(merged), { mode: 0o600 });
        fs.renameSync(tmp, file);
    }
    catch {
        /* best effort */
    }
    return merged;
}
// --- Per-session pricing (multi-model correct) ------------------------------
function classTokens(r) {
    // OpenClaw token usage is already decomposed (input is fresh; cacheRead and
    // cacheWrite are separate), so no cache_hit_rate derivation is needed.
    return { fi: r.input, cr: r.cacheRead, cw: r.cacheWrite, out: r.output };
}
/** Resolve the rate-card key for a model, proxying unpriced models. */
function priceKey(model, proxy, openclawDir) {
    const pricing = (0, pricing_1.getPricing)(openclawDir);
    const key = pricing[model] ? model : (0, pricing_1.normalizeModelName)(model) ?? model;
    return pricing[key] ? key : proxy;
}
/**
 * Per-class cost of ONE session, priced at its OWN model (proxy if unpriced).
 * Returned per class so the waterfall can use real per-class effective rates.
 */
function sessionClassCost(r, proxy, openclawDir) {
    const model = priceKey(r.model, proxy, openclawDir);
    const t = classTokens(r);
    // Isolate each class by zeroing the others (calculateCost is linear in tokens,
    // so per-class costs sum to the full session cost).
    const split = { cacheWrite1hTokens: r.cacheWrite1h, cacheWrite5m: r.cacheWrite5m };
    return {
        fi: (0, pricing_1.calculateCost)({ input: t.fi, output: 0, cacheRead: 0, cacheWrite: 0 }, model, openclawDir),
        cr: (0, pricing_1.calculateCost)({ input: 0, output: 0, cacheRead: t.cr, cacheWrite: 0 }, model, openclawDir),
        cw: (0, pricing_1.calculateCost)({ input: 0, output: 0, cacheRead: 0, cacheWrite: t.cw }, model, openclawDir, { cacheWrite1hTokens: r.cacheWrite1h, cacheWrite5mTokens: r.cacheWrite5m }),
        out: (0, pricing_1.calculateCost)({ input: 0, output: t.out, cacheRead: 0, cacheWrite: 0 }, model, openclawDir),
    };
}
function modelShares(records) {
    const byModel = {};
    let total = 0;
    for (const r of records) {
        const t = r.input + r.output + r.cacheRead + r.cacheWrite;
        byModel[r.model] = (byModel[r.model] ?? 0) + t;
        total += t;
    }
    if (total <= 0)
        return {};
    const shares = {};
    for (const [m, t] of Object.entries(byModel))
        shares[m] = t / total;
    return shares;
}
function dominantPricedModel(records, openclawDir) {
    const pricing = (0, pricing_1.getPricing)(openclawDir);
    const byModel = {};
    for (const r of records) {
        const key = pricing[r.model] ? r.model : (0, pricing_1.normalizeModelName)(r.model) ?? r.model;
        if (pricing[key])
            byModel[key] = (byModel[key] ?? 0) + (r.input + r.output + r.cacheRead + r.cacheWrite);
    }
    let best = PROXY_MODEL;
    let bestTok = -1;
    for (const [m, t] of Object.entries(byModel))
        if (t > bestTok) {
            best = m;
            bestTok = t;
        }
    return best;
}
/**
 * Aggregate an era. Each session is priced at its own model; then the top 1% of
 * sessions BY COST are winsorized (scaled down) so one runaway session can't
 * dominate. Effective per-class rates are derived from the winsorized totals so
 * the waterfall telescopes exactly to costPerSession.
 */
function computeEraStats(records, openclawDir, proxyOverride) {
    const zero = { fi: 0, cr: 0, cw: 0, out: 0 };
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
    const tokSum = { ...zero };
    const costSum = { ...zero };
    for (const row of rows) {
        const scale = row.totalCost > cap && row.totalCost > 0 ? cap / row.totalCost : 1;
        for (const c of CLASSES) {
            tokSum[c] += row.tok[c] * scale;
            costSum[c] += row.cost[c] * scale;
        }
    }
    const meanTokens = { ...zero };
    const effRate = { ...zero };
    let costPerSession = 0;
    for (const c of CLASSES) {
        meanTokens[c] = tokSum[c] / n;
        effRate[c] = tokSum[c] > 0 ? costSum[c] / tokSum[c] : 0;
        costPerSession += costSum[c] / n; // == Σ effRate[c]*meanTokens[c]
    }
    return { n, costPerSession, meanTokens, effRate, shares: modelShares(records) };
}
function baselinePath(openclawDir) {
    return path.join(storageDir(openclawDir), "baseline-state.json");
}
function loadFrozenBaseline(openclawDir) {
    try {
        const b = JSON.parse(fs.readFileSync(baselinePath(openclawDir), "utf-8"));
        if (b && b.version === BASELINE_VERSION)
            return b;
    }
    catch {
        /* none */
    }
    return null;
}
function getOrComputeBaseline(openclawDir, history, now, proxy) {
    const frozen = loadFrozenBaseline(openclawDir);
    if (frozen)
        return { baseline: frozen, reason: "frozen" };
    if (history.length === 0)
        return { baseline: null, reason: "no history" };
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
    const baseline = {
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
    }
    catch {
        /* best effort */
    }
    return { baseline, reason: "frozen" };
}
function mixLabel(shares) {
    const top = Object.entries(shares).sort((a, b) => b[1] - a[1])[0];
    return top ? `${Math.round(top[1] * 100)}% ${top[0]}` : "n/a";
}
const NOT_READY = (status) => ({
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
 * Compute realized before/after savings. `now` is injectable for testing.
 */
function computeRealizedSavings(openclawDir, days = 30, now = Date.now()) {
    let fresh = [];
    try {
        fresh = (0, session_parser_1.scanAllSessions)(openclawDir, 36500).map(toRecord);
    }
    catch {
        fresh = [];
    }
    const history = mergeAndPersistHistory(openclawDir, fresh);
    if (history.length === 0)
        return NOT_READY("no sessions yet");
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
    const as = computeEraStats(after, openclawDir, baseline.proxy);
    const beforeCost = bs.costPerSession;
    const afterCost = as.costPerSession;
    const perSession = beforeCost - afterCost;
    const afterWindowDays = Math.max(1, (now - afterStart) / DAY_MS);
    const sessionsPerMonth = (after.length / afterWindowDays) * 30;
    const monthly = perSession * sessionsPerMonth;
    // Cumulative: per-session delta across every post-baseline session.
    const allAfter = history.filter((r) => r.ts >= baseline.windowEnd);
    const cumulative = perSession * allAfter.length;
    // Waterfall via per-class effective rates. Telescopes EXACTLY to perSession:
    //   routing  = Σ_c (effRate_before[c] - effRate_after[c]) * meanTokens_before[c]
    //   volume_c = effRate_after[c] * (meanTokens_before[c] - meanTokens_after[c])
    // routing captures model-mix + price-class shifts; volume_c captures behavior.
    let routing = 0;
    for (const c of CLASSES)
        routing += (bs.effRate[c] - as.effRate[c]) * bs.meanTokens[c];
    const vol = (c) => as.effRate[c] * (bs.meanTokens[c] - as.meanTokens[c]);
    const perMonth = (x) => x * sessionsPerMonth;
    const breakdown = [
        { key: "routing", label: "Model routing & pricing", monthlyUsd: perMonth(routing) },
        { key: "cache", label: "Context / cache reuse", monthlyUsd: perMonth(vol("cr") + vol("cw")) },
        { key: "fresh_input", label: "Fresh input", monthlyUsd: perMonth(vol("fi")) },
        { key: "output", label: "Output", monthlyUsd: perMonth(vol("out")) },
    ];
    return {
        ready: true,
        status: "ok",
        monthlySavingsUsd: monthly,
        savingsPerSession: perSession,
        beforeCostPerSession: beforeCost,
        afterCostPerSession: afterCost,
        sessionsPerMonth,
        beforeMixLabel: mixLabel(bs.shares),
        afterMixLabel: mixLabel(as.shares),
        cumulativeSavedUsd: cumulative,
        installDate,
        breakdown,
    };
}
//# sourceMappingURL=savings.js.map