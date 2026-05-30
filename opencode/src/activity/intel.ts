const MAX_SUMMARIES_PER_WINDOW = 3;
const COOLDOWN_WINDOW_MS = 5 * 60 * 1000;

/** Tool output at or above this size (chars) is treated as "large". */
export const LARGE_OUTPUT_THRESHOLD = 8192;

/**
 * Rate-limit marker for large tool outputs, scoped to a single session's state.
 * `recentSummaries` is mutated in place (it lives on the per-session state, so
 * each session keeps its own cooldown). Returns true if the event was counted,
 * false if the per-window cap is already reached.
 */
export function trackLargeOutputEvent(recentSummaries: number[]): boolean {
  const now = Date.now();
  const cutoff = now - COOLDOWN_WINDOW_MS;

  // Drop expired markers in place.
  let w = 0;
  for (let r = 0; r < recentSummaries.length; r++) {
    if (recentSummaries[r] >= cutoff) recentSummaries[w++] = recentSummaries[r];
  }
  recentSummaries.length = w;

  if (recentSummaries.length >= MAX_SUMMARIES_PER_WINDOW) return false;
  recentSummaries.push(now);
  return true;
}
