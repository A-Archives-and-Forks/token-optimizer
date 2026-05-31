// Parse a single usage window from the statusline sidecar (`used_percentage` +
// epoch `resets_at`). Tolerant of a few shape variants (`utilization`, ISO-string
// `resets_at`) so a future sidecar format change degrades gracefully rather than
// rendering "NaN%".
import { RateWindow } from './types';

export function parseRateWindow(w: any): RateWindow | null {
  if (!w) return null;
  const used =
    typeof w.used_percentage === 'number'
      ? w.used_percentage
      : typeof w.utilization === 'number'
        ? w.utilization
        : null;
  // NaN passes `typeof === 'number'`, so reject non-finite explicitly —
  // otherwise it propagates through Math.min/max and renders "NaN%".
  if (used == null || !Number.isFinite(used)) return null;

  let resetsAt: number | null = null;
  if (typeof w.resets_at === 'number' && Number.isFinite(w.resets_at) && w.resets_at > 0) {
    resetsAt = w.resets_at;
  } else if (typeof w.resets_at === 'string') {
    const parsed = Math.floor(Date.parse(w.resets_at) / 1000);
    resetsAt = Number.isFinite(parsed) && parsed > 0 ? parsed : null;
  }

  return { usedPercentage: Math.max(0, Math.min(100, used)), resetsAt };
}
