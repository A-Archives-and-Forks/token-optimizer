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
import { test, expect, beforeEach, afterEach } from "bun:test";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { computeRealizedSavings } from "./savings.js";

const DAY = 86_400_000;
const T0 = Date.parse("2026-01-01T00:00:00Z"); // install day

interface HistRecord {
  sessionId: string;
  ts: number;
  model: string;
  input: number;
  output: number;
  cacheRead: number;
  cacheWrite: number;
  cacheWrite1h: number;
  cacheWrite5m: number;
  costUsd: number;
  durationSec: number;
}

// Baseline (early-window) session: Opus, high cache reuse — the frozen "old way".
function beforeRow(tsMs: number, model = "opus"): HistRecord {
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
function afterRow(tsMs: number, perInput: number, model: string): HistRecord {
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
function beforeRows(): HistRecord[] {
  const rows = [beforeRow(T0)];
  for (let i = 0; i < 35; i++) {
    const ts = T0 + 2 * DAY + i * 0.5 * DAY;
    const model = i < 33 ? "opus" : "sonnet"; // ~95% Opus by session
    rows.push(beforeRow(ts, model));
  }
  return rows;
}

function afterRows(now: number, n: number, perInput: number, opusShare: number, spacingDays: number): HistRecord[] {
  const rows: HistRecord[] = [];
  for (let i = 0; i < n; i++) {
    const model = i < Math.round(n * opusShare) ? "opus" : "sonnet";
    rows.push(afterRow(now - (i + 1) * DAY * spacingDays, perInput, model));
  }
  return rows;
}

let dir: string;

beforeEach(() => {
  dir = fs.mkdtempSync(path.join(os.tmpdir(), "oc-savings-"));
  fs.mkdirSync(path.join(dir, "token-optimizer"), { recursive: true });
});

afterEach(() => {
  fs.rmSync(dir, { recursive: true, force: true });
});

function run(now: number, days: number, after: HistRecord[]): ReturnType<typeof computeRealizedSavings> {
  fs.writeFileSync(
    path.join(dir, "token-optimizer", "session-history.json"),
    JSON.stringify([...beforeRows(), ...after]),
  );
  return computeRealizedSavings(dir, days, now);
}

test("young after-window: trackedDays is the after-window span, not install age", () => {
  // Day 33 post-install: after-window = [T0+31d, T0+33d] = 2 days.
  const NOW = T0 + 33 * DAY;
  const r = run(NOW, 30, afterRows(NOW, 12, 4_000_000, 0.0, 0.15));
  expect(r.ready).toBe(true);
  expect(r.trackedDays).toBeGreaterThanOrEqual(1);
  expect(r.trackedDays).toBeLessThanOrEqual(3);
});

test("exactly 30 days of after-window keeps the run-rate (boundary)", () => {
  // now - 30d == windowEnd == T0+31d exactly.
  const NOW = T0 + 61 * DAY;
  const r = run(NOW, 30, afterRows(NOW, 40, 4_000_000, 0.5, 0.5));
  expect(r.ready).toBe(true);
  expect(r.trackedDays).toBe(30);
});

test("mature install: trackedDays == 30 at the default 30-day lookback", () => {
  const NOW = T0 + 200 * DAY;
  const r = run(NOW, 30, afterRows(NOW, 40, 4_000_000, 0.5, 0.2));
  expect(r.ready).toBe(true);
  expect(r.trackedDays).toBe(30);
});
