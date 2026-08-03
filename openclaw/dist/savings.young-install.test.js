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
/**
 * Young-install savings guard tests (OpenClaw engine, v5.11.79).
 *
 * Preseeds `<dir>/token-optimizer/session-history.json` (the merge seam) and
 * pins the `trackedDays` signal = floor(afterWindowDays) that drives the
 * dashboard's young-install guard. `scanAllSessions` on the empty temp dir
 * returns [] (caught), so the persisted history is the sole input.
 *
 * Run: bun test src/savings.young-install.test.ts
 */
const bun_test_1 = require("bun:test");
const fs = __importStar(require("node:fs"));
const os = __importStar(require("node:os"));
const path = __importStar(require("node:path"));
const savings_js_1 = require("./savings.js");
const DAY = 86_400_000;
const T0 = Date.parse("2026-01-01T00:00:00Z"); // install day
// Baseline (early-window) session: Opus, high cache reuse — the frozen "old way".
function beforeRow(tsMs, model = "opus") {
    return {
        sessionId: `s-${tsMs}`,
        ts: tsMs,
        model,
        input: 30_000,
        output: 51_000,
        cacheRead: 13_500_000,
        cacheWrite: 487_000,
        cacheWrite1h: 0,
        cacheWrite5m: 487_000,
        costUsd: 0,
        durationSec: 300, // above the 60s quality gate
    };
}
const BASE_HIT = 13_500_000 / (13_500_000 + 30_000);
// Recent "after" session: Sonnet-leaning so the routing lever (Opus -> Sonnet)
// yields a positive transformation.
function afterRow(tsMs, perInput, model) {
    const cr = perInput * BASE_HIT;
    const fi = perInput - cr;
    const cw = perInput * 0.03;
    return {
        sessionId: `s-${tsMs}`,
        ts: tsMs,
        model,
        input: Math.round(fi),
        output: Math.round(perInput * 0.01),
        cacheRead: Math.round(cr),
        cacheWrite: Math.round(cw),
        cacheWrite1h: 0,
        cacheWrite5m: Math.round(cw),
        costUsd: 0,
        durationSec: 300,
    };
}
// 35 opus-heavy before-rows in [T0+2d, ~T0+19.5d] + install anchor at T0.
// Early window = [T0+1d, T0+31d); the block sits densely inside it.
function beforeRows() {
    const rows = [beforeRow(T0)];
    for (let i = 0; i < 35; i++) {
        const ts = T0 + 2 * DAY + i * 0.5 * DAY;
        const model = i < 33 ? "opus" : "sonnet"; // ~95% Opus by session
        rows.push(beforeRow(ts, model));
    }
    return rows;
}
function afterRows(now, n, perInput, opusShare, spacingDays) {
    const rows = [];
    for (let i = 0; i < n; i++) {
        const model = i < Math.round(n * opusShare) ? "opus" : "sonnet";
        rows.push(afterRow(now - (i + 1) * DAY * spacingDays, perInput, model));
    }
    return rows;
}
let dir;
(0, bun_test_1.beforeEach)(() => {
    dir = fs.mkdtempSync(path.join(os.tmpdir(), "oc-savings-"));
    fs.mkdirSync(path.join(dir, "token-optimizer"), { recursive: true });
});
(0, bun_test_1.afterEach)(() => {
    fs.rmSync(dir, { recursive: true, force: true });
});
function run(now, days, after) {
    fs.writeFileSync(path.join(dir, "token-optimizer", "session-history.json"), JSON.stringify([...beforeRows(), ...after]));
    return (0, savings_js_1.computeRealizedSavings)(dir, days, now);
}
(0, bun_test_1.test)("young after-window: trackedDays is the after-window span, not install age", () => {
    // Day 33 post-install: after-window = [T0+31d, T0+33d] = 2 days.
    const NOW = T0 + 33 * DAY;
    const r = run(NOW, 30, afterRows(NOW, 12, 4_000_000, 0.0, 0.15));
    (0, bun_test_1.expect)(r.ready).toBe(true);
    (0, bun_test_1.expect)(r.trackedDays).toBeGreaterThanOrEqual(1);
    (0, bun_test_1.expect)(r.trackedDays).toBeLessThanOrEqual(3);
});
(0, bun_test_1.test)("exactly 30 days of after-window keeps the run-rate (boundary)", () => {
    // now - 30d == windowEnd == T0+31d exactly.
    const NOW = T0 + 61 * DAY;
    const r = run(NOW, 30, afterRows(NOW, 40, 4_000_000, 0.5, 0.5));
    (0, bun_test_1.expect)(r.ready).toBe(true);
    (0, bun_test_1.expect)(r.trackedDays).toBe(30);
});
(0, bun_test_1.test)("mature install: trackedDays == 30 at the default 30-day lookback", () => {
    const NOW = T0 + 200 * DAY;
    const r = run(NOW, 30, afterRows(NOW, 40, 4_000_000, 0.5, 0.2));
    (0, bun_test_1.expect)(r.ready).toBe(true);
    (0, bun_test_1.expect)(r.trackedDays).toBe(30);
});
//# sourceMappingURL=savings.young-install.test.js.map