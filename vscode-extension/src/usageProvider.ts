// The 5h/7d tier ladder (KTD5):
//   1. fresh sidecar (rate-limits.json written by statusline)  -> authoritative
//   2. else, if Live Usage is ON: OAuth lookup (cached ~60s)    -> authoritative
//   3. else: last-known sidecar value, labeled stale/(est)      -> honest fallback
//
// We deliberately do NOT reconstruct the 5h window from trailing JSONL: research
// shows that estimate is blind to cross-device usage and routinely wrong. A real
// number labeled "(est) as of Nm ago" is more honest than a fabricated one.
import { Snapshot, RateLimits } from './types';
import { fetchOauthUsage } from './oauthUsage';

const OAUTH_CACHE_MS = 60_000;

export interface ResolveOptions {
  liveUsageOn: boolean;
  credentialsPath: string;
  nowMs: number;
}

// Injectable so tests can supply a fake without touching the network.
export type UsageFetcher = (credentialsPath: string, nowMs: number) => Promise<RateLimits | null>;

export class UsageProvider {
  private oauthCache: { timestamp: number; value: Snapshot['rateLimits'] } | null = null;
  private inflight: Promise<Snapshot['rateLimits']> | null = null;
  private cacheGen = 0; // bumped by invalidate() to disown in-flight fetches

  constructor(private fetcher: UsageFetcher = fetchOauthUsage) {}

  // Mutates and returns the snapshot with the best available rate-limit data.
  async resolve(snap: Snapshot, opts: ResolveOptions): Promise<Snapshot> {
    // Tier 1: fresh sidecar already on the snapshot.
    if (snap.rateLimits && !snap.rateLimitsStale) return snap;

    // Tier 2: Live Usage enabled -> OAuth (60s cache).
    if (opts.liveUsageOn) {
      const fresh = await this.getOauth(opts);
      if (fresh) {
        snap.rateLimits = fresh;
        snap.rateLimitsStale = false;
        snap.hasData = true;
        return snap;
      }
    }

    // Tier 3: keep the last-known sidecar value, marked stale. (snap already
    // carries rateLimitsStale=true here, or rateLimits=null if nothing exists.)
    return snap;
  }

  private async getOauth(opts: ResolveOptions): Promise<Snapshot['rateLimits']> {
    if (this.oauthCache && opts.nowMs - this.oauthCache.timestamp < OAUTH_CACHE_MS) {
      return this.oauthCache.value;
    }
    // Coalesce concurrent cache-misses into a single network request.
    if (this.inflight) return this.inflight;

    const gen = this.cacheGen;
    this.inflight = (async () => {
      let value: Snapshot['rateLimits'] = null;
      try {
        // The injected fetcher *should* resolve null on failure, but don't
        // trust the contract — a throw here must not reject renderFrom.
        value = await this.fetcher(opts.credentialsPath, opts.nowMs);
      } catch {
        value = null;
      }
      // Only cache if we weren't invalidated mid-flight (e.g. the user toggled
      // Live Usage during the fetch) — otherwise the stale result would
      // repopulate the cache and defeat the invalidation.
      if (gen === this.cacheGen) {
        // Cache even a null result so a missing-creds/offline state doesn't
        // hammer the endpoint every refresh tick.
        this.oauthCache = { timestamp: opts.nowMs, value };
      }
      return value;
    })();
    try {
      return await this.inflight;
    } finally {
      // Leave a newer in-flight (created after an invalidation) untouched.
      if (gen === this.cacheGen) this.inflight = null;
    }
  }

  // Drop the cache AND disown any in-flight fetch so an explicit toggle/refresh
  // fetches fresh — a coalesced in-flight result must not repopulate the cache.
  invalidate(): void {
    this.oauthCache = null;
    this.inflight = null;
    this.cacheGen++;
  }
}
