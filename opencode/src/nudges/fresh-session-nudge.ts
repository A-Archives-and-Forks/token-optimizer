/**
 * Fresh-session nudge: fires once per session when context is BOTH long
 * (fill >= FRESH_NUDGE_MIN_FILL_PCT) AND degraded (quality < FRESH_NUDGE_QUALITY_THRESHOLD).
 *
 * Confidently reassures the user that Token Optimizer has checkpointed their
 * active task so a fresh session resumes exactly where they stopped, and shows
 * the concrete tokens they would reclaim by starting fresh now.
 *
 * Takes PRECEDENCE over the ordinary quality/compact nudge (the caller skips
 * that when this fires — both messages would be noise).
 *
 * Ported from Python _maybe_fresh_session_nudge / _fresh_session_savings_estimate
 * in skills/token-optimizer/scripts/measure.py.
 */

import { contextWindowForModel } from "../util/context-window.js";

function intEnv(key: string, fallback: number): number {
  const raw = process.env[key]?.trim();
  if (!raw) return fallback;
  const parsed = parseInt(raw, 10);
  return isNaN(parsed) ? fallback : parsed;
}

// Env-tunable thresholds, matching Python constants.
export const FRESH_NUDGE_QUALITY_THRESHOLD = intEnv("TOKEN_OPTIMIZER_FRESH_NUDGE_QUALITY", 70);
export const FRESH_NUDGE_MIN_FILL_PCT = intEnv("TOKEN_OPTIMIZER_FRESH_NUDGE_MIN_FILL", 50);

/** Tokens re-injected by a fresh lean-resume (the small overhead the new session pays). */
const FRESH_NUDGE_LEAN_BLOCK_TOKENS = 1000;

export interface FreshNudgeResult {
  shouldNudge: boolean;
  message: string | null;
}

/**
 * Estimate tokens reclaimed by starting a fresh session now.
 * current context size = (fillPct / 100) * contextWindow
 * savings = current context - lean block re-injection overhead
 *
 * @param fillPct   0-100 (percentage, not fraction)
 * @param model     optional model id for context-window lookup
 * @returns [savedTokens, contextWindow]
 */
export function freshSessionSavingsEstimate(fillPct: number, model?: string): [number, number] {
  const contextWindow = contextWindowForModel(model ?? "");
  const clampedFill = Math.max(0, Math.min(100, fillPct));
  const currentCtx = Math.round((clampedFill / 100) * contextWindow);
  const saved = Math.max(0, currentCtx - FRESH_NUDGE_LEAN_BLOCK_TOKENS);
  return [saved, contextWindow];
}

/**
 * Check whether the fresh-session nudge should fire for this turn.
 *
 * @param currentScore        current quality/resource-health score (0-100)
 * @param fillPct             current context fill as 0-100 (percentage, not fraction)
 * @param previousScore       score from the previous turn (null = no prior score yet)
 * @param freshNudgeFired     whether the nudge already fired this session
 * @param nudgesEnabled       whether quality nudges are enabled in config
 * @param model               optional model id for context-window lookup
 */
export function checkFreshSessionNudge(
  currentScore: number,
  fillPct: number,
  previousScore: number | null,
  freshNudgeFired: boolean,
  nudgesEnabled: boolean,
  model?: string,
): FreshNudgeResult {
  if (!nudgesEnabled) return { shouldNudge: false, message: null };

  // Post-compaction suppression: no prior score means this is a fresh/just-compacted
  // session. Let the ordinary nudge seed the baseline first.
  if (previousScore === null) return { shouldNudge: false, message: null };

  // Once per session.
  if (freshNudgeFired) return { shouldNudge: false, message: null };

  // Both conditions must hold: long session AND degraded quality.
  if (!(currentScore < FRESH_NUDGE_QUALITY_THRESHOLD && fillPct >= FRESH_NUDGE_MIN_FILL_PCT)) {
    return { shouldNudge: false, message: null };
  }

  const [saved] = freshSessionSavingsEstimate(fillPct, model);
  const savedStr = saved >= 1000 ? `~${Math.floor(saved / 1000)}K` : `~${saved}`;
  const fillRounded = Math.round(fillPct);
  const scoreRounded = Math.round(currentScore);

  const message =
    `[Token Optimizer] This session is long (${fillRounded}% full) and context quality has fallen to ${scoreRounded}. ` +
    `Starting a fresh session now would reclaim ${savedStr} tokens (~${fillRounded}% of your window). ` +
    `You won't lose your place: Token Optimizer has checkpointed your active task, key decisions, files, and tool results, ` +
    `so a new session picks up exactly where you stopped. Just open one and say "continue this" — the context is rebuilt for free.`;

  return { shouldNudge: true, message };
}
