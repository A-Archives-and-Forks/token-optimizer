// Parse a single usage window from any of the shapes we encounter: the
// statusline sidecar (`used_percentage` + epoch `resets_at`) and the OAuth
// usage response (which may use `utilization` and/or an ISO-string `resets_at`).
// Tolerant of all known variants so both readers share one implementation.
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
