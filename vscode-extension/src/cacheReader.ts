// Pure parsing of Token Optimizer's on-disk cache files into a Snapshot.
// Takes already-read file contents (strings) so it stays vscode-free and fully
// unit-testable; the actual fs reads live in dataSource.
import { Snapshot, RateLimits, AgentInfo, emptySnapshot } from './types';
import { parseRateWindow } from './rateWindow';

const STALE_QUALITY_SECONDS = 300; // mirror statusline.js: score older than 5min => stale

export interface RawInputs {
  qualityJson: string | null;
  liveFillJson: string | null;
  rateLimitsJson: string | null;
  jsonlFill: number | null;
  jsonlModel: string | null;
  effort: string | null;
  sessionId: string | null; // to confirm the cache belongs to the active session
  nowMs: number;
  staleAfterSeconds: number; // for rate-limit staleness labeling
}

const MAX_AGENT_DESC = 120;

function isPlainObject(v: any): boolean {
  return v != null && typeof v === 'object' && !Array.isArray(v);
}

function clampScore(n: number): number {
  return Math.max(0, Math.min(100, Math.round(n)));
}

// Match statusline.js agent-elapsed formatting: "4m30s" / "45s".
function elapsedSince(startTime: any, nowMs: number): string | null {
  if (typeof startTime !== 'string') return null;
  const start = Date.parse(startTime);
  if (!Number.isFinite(start)) return null;
  const secs = Math.floor((nowMs - start) / 1000);
  if (secs < 0) return null;
  return secs >= 60 ? `${Math.floor(secs / 60)}m${secs % 60}s` : `${secs}s`;
}

function safeParse(json: string | null): any {
  if (!json) return null;
  try {
    return JSON.parse(json);
  } catch {
    return null;
  }
}

function gradeFor(score: number): string {
  if (score >= 90) return 'S';
  if (score >= 80) return 'A';
  if (score >= 70) return 'B';
  if (score >= 55) return 'C';
  if (score >= 40) return 'D';
  return 'F';
}

export function buildSnapshot(inputs: RawInputs): Snapshot {
  const snap = emptySnapshot();
  const q = safeParse(inputs.qualityJson);
  const live = safeParse(inputs.liveFillJson);
  const rl = safeParse(inputs.rateLimitsJson);

  snap.model = inputs.jsonlModel;
  snap.effort = inputs.effort;

  // ---- Context fill: live-fill.json wins (authoritative from statusline),
  // else JSONL tail. ----
  if (live && typeof live.used_percentage === 'number') {
    snap.fillPct = Math.max(0, Math.min(100, Math.round(live.used_percentage)));
    snap.fillSource = 'live-fill';
  } else if (inputs.jsonlFill != null) {
    snap.fillPct = inputs.jsonlFill;
    snap.fillSource = 'jsonl';
  }

  // ---- Quality scores ----
  // Require a real object: a JSON array/scalar is truthy but carries no fields,
  // and would otherwise flip hasData on with everything null ("connected" lie).
  if (isPlainObject(q)) {
    snap.hasData = true;

    // Only trust per-session details (duration, agents) when the cache actually
    // belongs to the resolved session — mirrors statusline.js cacheMatchesSession.
    const cacheMatchesSession =
      typeof q.session_file === 'string' &&
      !!inputs.sessionId &&
      q.session_file.includes(inputs.sessionId);

    const rh = typeof q.resource_health === 'number' ? q.resource_health : q.score;
    if (typeof rh === 'number' && Number.isFinite(rh)) {
      const score = clampScore(rh);
      const grade = q.resource_health_grade || q.grade || gradeFor(score);
      let stale = false;
      const ts = q.timestamp ? Date.parse(q.timestamp) : NaN;
      if (isNaN(ts) || (inputs.nowMs - ts) / 1000 > STALE_QUALITY_SECONDS) stale = true;
      snap.contextQ = { score, grade, stale };
    }

    if (typeof q.session_efficiency === 'number' && Number.isFinite(q.session_efficiency)) {
      const score = clampScore(q.session_efficiency);
      snap.eff = { score, grade: q.session_efficiency_grade || gradeFor(score) };
    }

    if (q.fill_warning && q.fill_warning.level) {
      snap.fillWarning = {
        level: q.fill_warning.level,
        value: Math.round(q.fill_warning.fill_pct || 0),
      };
    } else if (q.regime_change && typeof q.regime_change.fill_pct === 'number') {
      snap.regimeChangeFillPct = Math.round(q.regime_change.fill_pct);
    }

    if (q.tool_call_warning && q.tool_call_warning.level) {
      snap.toolWarning = {
        level: q.tool_call_warning.level,
        value: q.tool_calls || 0,
      };
    }

    if (typeof q.compactions === 'number') {
      snap.compactions = q.compactions;
      const lossPct = q.breakdown?.compaction_depth?.cumulative_loss_pct;
      if (typeof lossPct === 'number' && Number.isFinite(lossPct)) {
        snap.compactionLossPct = Math.max(0, Math.round(lossPct));
      } else if (q.compactions >= 3) {
        snap.compactionLossPct = 95;
      } else if (q.compactions === 2) {
        snap.compactionLossPct = 88;
      } else if (q.compactions === 1) {
        snap.compactionLossPct = 65;
      }
    }

    if (cacheMatchesSession && typeof q.session_start_ts === 'number' && q.session_start_ts > 0) {
      const elapsed = Math.floor(inputs.nowMs / 1000 - q.session_start_ts);
      if (elapsed > 0 && elapsed < 604800) snap.durationSec = elapsed;
    }

    if (cacheMatchesSession && Array.isArray(q.active_agents)) {
      snap.agents = q.active_agents
        .filter((a: any) => a && a.status === 'running')
        .slice(0, 3)
        .map(
          (a: any): AgentInfo => ({
            model: stripControl(String(a.model || '?')).slice(0, MAX_AGENT_DESC),
            description: stripControl(String(a.description || '')).slice(0, MAX_AGENT_DESC),
            elapsed: elapsedSince(a.start_time, inputs.nowMs),
          })
        );
    }
  }

  if (snap.fillPct != null) snap.hasData = true;

  // ---- Rate limits: sidecar (authoritative when fresh) ----
  if (rl) {
    const fiveHour = parseRateWindow(rl.five_hour);
    const sevenDay = parseRateWindow(rl.seven_day);
    if (fiveHour || sevenDay) {
      const ts = typeof rl.timestamp === 'number' ? rl.timestamp : 0;
      const ageSec = ts ? (inputs.nowMs - ts) / 1000 : Infinity;
      const limits: RateLimits = {
        fiveHour,
        sevenDay,
        timestamp: ts,
        source: rl.source === 'oauth' ? 'oauth' : 'statusline',
      };
      snap.rateLimits = limits;
      snap.rateLimitsStale = ageSec > inputs.staleAfterSeconds;
      snap.hasData = true;
    }
  }

  return snap;
}

function stripControl(s: string): string {
  // Drop ANSI escapes and control chars an agent description might carry.
  // eslint-disable-next-line no-control-regex
  return s.replace(/\x1b\[[0-9;]*[a-zA-Z]/g, '').replace(/[\x00-\x1f]/g, '');
}
