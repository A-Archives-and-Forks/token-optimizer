import * as fs from 'fs';
import * as path from 'path';
import { RateLimits, RateWindow } from './types';

const FIVE_HOURS_MS = 5 * 60 * 60 * 1000;
const SEVEN_DAYS_MS = 7 * 24 * 60 * 60 * 1000;
const DEFAULT_MAX_FILES = 120;
const DEFAULT_MAX_BYTES = 40 * 1024 * 1024;

export interface EstimateOptions {
  nowMs: number;
  baseline: RateLimits | null;
  maxFiles?: number;
  maxBytes?: number;
}

interface UsageEvent {
  ts: number;
  tokens: number;
}

export function estimateRateLimitsFromTranscripts(
  projectsDir: string,
  opts: EstimateOptions
): RateLimits | null {
  if (!opts.baseline || opts.baseline.timestamp <= 0) return null;
  const files = recentJsonlFiles(projectsDir, opts.nowMs, opts.baseline.timestamp, {
    maxFiles: opts.maxFiles ?? DEFAULT_MAX_FILES,
    maxBytes: opts.maxBytes ?? DEFAULT_MAX_BYTES,
  });
  if (!files.length) return null;

  const events: UsageEvent[] = [];
  for (const file of files) {
    events.push(...readUsageEvents(file));
  }
  if (!events.length) return null;

  const fiveHour = estimateWindow(
    opts.baseline.fiveHour,
    events,
    opts.baseline.timestamp,
    opts.nowMs,
    FIVE_HOURS_MS
  );
  const sevenDay = estimateWindow(
    opts.baseline.sevenDay,
    events,
    opts.baseline.timestamp,
    opts.nowMs,
    SEVEN_DAYS_MS
  );
  if (!fiveHour && !sevenDay) return null;
  return {
    fiveHour,
    sevenDay,
    timestamp: opts.nowMs,
    source: 'transcript-estimate',
  };
}

function estimateWindow(
  baseline: RateWindow | null,
  events: UsageEvent[],
  baselineMs: number,
  nowMs: number,
  windowMs: number
): RateWindow | null {
  if (!baseline || baseline.usedPercentage <= 0) return null;
  const baselineTokens = sumWindow(events, baselineMs - windowMs, baselineMs);
  const currentEvents = events.filter((e) => e.ts > nowMs - windowMs && e.ts <= nowMs);
  const currentTokens = currentEvents.reduce((sum, e) => sum + e.tokens, 0);
  if (baselineTokens <= 0 || currentTokens <= 0) return null;
  const pct = Math.max(0, Math.min(100, Math.round((currentTokens / baselineTokens) * baseline.usedPercentage)));
  return {
    usedPercentage: pct,
    resetsAt: estimateReset(baseline.resetsAt, currentEvents, nowMs, windowMs),
    freshness: 'estimated',
    source: 'transcript-estimate',
    ageSeconds: null,
  };
}

function estimateReset(
  baselineResetSec: number | null,
  currentEvents: UsageEvent[],
  nowMs: number,
  windowMs: number
): number | null {
  if (baselineResetSec && baselineResetSec * 1000 > nowMs) return baselineResetSec;
  if (!currentEvents.length) return null;
  const oldest = currentEvents.reduce((min, e) => Math.min(min, e.ts), currentEvents[0].ts);
  return Math.floor((oldest + windowMs) / 1000);
}

function sumWindow(events: UsageEvent[], startMs: number, endMs: number): number {
  return events.reduce((sum, e) => (e.ts > startMs && e.ts <= endMs ? sum + e.tokens : sum), 0);
}

function recentJsonlFiles(
  projectsDir: string,
  nowMs: number,
  baselineMs: number,
  opts: { maxFiles: number; maxBytes: number }
): string[] {
  let dirs: fs.Dirent[];
  try {
    dirs = fs.readdirSync(projectsDir, { withFileTypes: true });
  } catch {
    return [];
  }
  const oldestNeeded = Math.min(nowMs - SEVEN_DAYS_MS, baselineMs - SEVEN_DAYS_MS);
  const candidates: Array<{ file: string; mtimeMs: number; size: number }> = [];
  for (const dir of dirs) {
    if (!dir.isDirectory()) continue;
    const projectPath = path.join(projectsDir, dir.name);
    let files: string[];
    try {
      files = fs.readdirSync(projectPath);
    } catch {
      continue;
    }
    for (const name of files) {
      if (!name.endsWith('.jsonl')) continue;
      const file = path.join(projectPath, name);
      try {
        const st = fs.statSync(file);
        if (st.mtimeMs >= oldestNeeded) candidates.push({ file, mtimeMs: st.mtimeMs, size: st.size });
      } catch {
        continue;
      }
    }
  }
  candidates.sort((a, b) => b.mtimeMs - a.mtimeMs);
  const selected: string[] = [];
  let bytes = 0;
  for (const c of candidates) {
    if (selected.length >= opts.maxFiles) break;
    if (bytes + c.size > opts.maxBytes && selected.length > 0) continue;
    bytes += c.size;
    selected.push(c.file);
  }
  return selected;
}

function readUsageEvents(filePath: string): UsageEvent[] {
  let text: string;
  try {
    text = fs.readFileSync(filePath, 'utf8');
  } catch {
    return [];
  }
  const events: UsageEvent[] = [];
  for (const line of text.split('\n')) {
    if (!line.trim()) continue;
    let obj: any;
    try {
      obj = JSON.parse(line);
    } catch {
      continue;
    }
    const ts = Date.parse(obj.timestamp || obj.message?.timestamp || '');
    if (!Number.isFinite(ts)) continue;
    const usage = obj.message?.usage ?? obj.usage;
    if (!usage) continue;
    const tokens =
      numberish(usage.input_tokens) +
      numberish(usage.cache_creation_input_tokens) +
      numberish(usage.cache_read_input_tokens) +
      numberish(usage.output_tokens);
    if (tokens > 0) events.push({ ts, tokens });
  }
  return events;
}

function numberish(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0;
}
