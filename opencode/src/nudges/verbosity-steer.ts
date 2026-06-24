/**
 * Verbosity-steer nudge: inject a tiered conciseness nudge when context is
 * under pressure. Mirrors the Python `run_verbosity_steer` function in
 * measure.py.
 *
 * Tiered messaging:
 *   25-74% fill + degraded quality (<75)  → gentle nudge
 *   75-89% fill                           → strong nudge with specific directives
 *   90%+ fill                             → suppressed (adding tokens makes it worse)
 *
 * Cooldown: max 3 nudges per session, 5 min between nudges.
 * Shares the same nudge_count / last_nudge_time fields as the quality nudge
 * so the two features share a single cooldown counter.
 */

import type { SessionStore } from "../storage/session-store.js";

const COOLDOWN_SEC = 300; // 5 minutes
const SESSION_CAP = 3;
const GENTLE_FILL_THRESHOLD = 25;
const STRONG_FILL_THRESHOLD = 75;
const CRITICAL_FILL_THRESHOLD = 90;
const QUALITY_THRESHOLD = 75;

export interface VerbositySteerResult {
  shouldNudge: boolean;
  message: string | null;
  tier: "gentle" | "strong" | "suppressed" | "none";
}

export function checkVerbositySteer(
  store: SessionStore,
  fillPct: number,
  qualityScore: number,
): VerbositySteerResult {
  const cache = store.getQualityCache();
  const nudgeCount = cache?.nudge_count ?? 0;
  const lastNudgeTime = cache?.last_nudge_time ?? 0;
  const now = Date.now() / 1000;

  // Cooldown and session cap (shared with quality nudge)
  if (nudgeCount >= SESSION_CAP) return { shouldNudge: false, message: null, tier: "none" };
  if (now - lastNudgeTime < COOLDOWN_SEC) return { shouldNudge: false, message: null, tier: "none" };

  // At 90%+ fill, don't add more tokens — suppress entirely
  if (fillPct >= CRITICAL_FILL_THRESHOLD) {
    return { shouldNudge: false, message: null, tier: "suppressed" };
  }

  // Strong tier: 75%+ fill regardless of quality
  if (fillPct >= STRONG_FILL_THRESHOLD) {
    const message =
      `[Token Optimizer] Context at ${Math.round(fillPct)}% capacity, quality ${Math.round(qualityScore)}/100. ` +
      "Reason as deeply as you need — but keep your visible output lean: no preamble, " +
      "no restating the request, no explanations unless asked. Every token saved extends the session.";
    return { shouldNudge: true, message, tier: "strong" };
  }

  // Gentle tier: 25%+ fill with degraded quality
  if (fillPct >= GENTLE_FILL_THRESHOLD && qualityScore < QUALITY_THRESHOLD) {
    const message =
      `[Token Optimizer] Context at ${Math.round(fillPct)}% capacity, quality ${Math.round(qualityScore)}/100. ` +
      "Reason fully, then keep your output lean — skip restating the request and " +
      "omit unnecessary preamble. Every token saved extends the session.";
    return { shouldNudge: true, message, tier: "gentle" };
  }

  return { shouldNudge: false, message: null, tier: "none" };
}

/**
 * Estimate output token savings from a verbosity-steer nudge.
 * The nudge causes the model to produce ~10-15% fewer output tokens
 * on affected responses. Returns [tokensSaved, tier] for logging.
 */
export function verbositySteerSavingsEstimate(
  fillPct: number,
): [number, string] {
  const avgResponseTokens = 800;
  const reduction = fillPct >= STRONG_FILL_THRESHOLD ? 0.15 : 0.10;
  return [Math.round(avgResponseTokens * reduction), fillPct >= STRONG_FILL_THRESHOLD ? "strong" : "gentle"];
}
