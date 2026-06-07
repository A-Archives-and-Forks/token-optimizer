import { Database } from "bun:sqlite";
import { existsSync, mkdirSync } from "node:fs";
import { join } from "node:path";

const TRENDS_SCHEMA = `
CREATE TABLE IF NOT EXISTS session_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL UNIQUE,
  date TEXT NOT NULL,
  project TEXT,
  model TEXT,
  tokens_input INTEGER DEFAULT 0,
  tokens_output INTEGER DEFAULT 0,
  tokens_cache_read INTEGER DEFAULT 0,
  tokens_cache_write INTEGER DEFAULT 0,
  cost_usd REAL DEFAULT 0,
  resource_health REAL,
  session_efficiency REAL,
  tool_calls INTEGER DEFAULT 0,
  compactions INTEGER DEFAULT 0,
  mode TEXT,
  duration_seconds INTEGER DEFAULT 0,
  created_at REAL NOT NULL
);
`;

const SAVINGS_EVENTS_SCHEMA = `
CREATE TABLE IF NOT EXISTS savings_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp TEXT NOT NULL,
  event_type TEXT NOT NULL,
  tokens_saved INTEGER DEFAULT 0,
  cost_saved_usd REAL DEFAULT 0.0,
  session_id TEXT,
  detail TEXT,
  model TEXT
);
`;

// Sonnet input rate ($/M tokens) used as fallback when the active model is
// unknown at savings-log time (e.g. checkpoint inject fires before the first
// assistant message arrives). Matches Python's _log_savings_event fallback.
const SONNET_INPUT_RATE_PER_MTOK = 3.0;

export interface SessionTrendData {
  sessionId: string;
  project: string | null;
  model: string | null;
  tokensInput: number;
  tokensOutput: number;
  tokensCacheRead: number;
  tokensCacheWrite: number;
  costUsd: number;
  resourceHealth: number | null;
  sessionEfficiency: number | null;
  toolCalls: number;
  compactions: number;
  mode: string | null;
  durationSeconds: number;
}

export class TrendsStore {
  private db: Database | null = null;
  private dbPath: string;

  constructor(dataDir: string) {
    const trendsDir = join(dataDir, "token-optimizer");
    if (!existsSync(trendsDir)) {
      mkdirSync(trendsDir, { recursive: true });
    }
    this.dbPath = join(trendsDir, "trends.db");
  }

  private connect(): Database {
    if (!this.db) {
      this.db = new Database(this.dbPath, { create: true });
      this.db.exec("PRAGMA journal_mode=WAL");
      this.db.exec("PRAGMA busy_timeout=3000");
      this.db.exec(TRENDS_SCHEMA);
      this.db.exec(SAVINGS_EVENTS_SCHEMA);
    }
    return this.db;
  }

  /**
   * Log a realized-savings event to the savings_events table.
   *
   * This is the TypeScript equivalent of Python's `_log_savings_event`.
   * Model is not known at checkpoint-inject time, so cost_saved_usd is
   * priced at the Sonnet input fallback rate — same behaviour as Python's
   * resolver when the session model cannot be determined.
   *
   * Guards:
   *   - tokensSaved <= 0  → no-op (never credit zero or negative)
   *   - Any exception     → silently swallowed (must never break the caller)
   */
  logSavingsEvent(
    eventType: string,
    tokensSaved: number,
    sessionId: string | null,
    detail: string | null,
    model: string | null = null,
  ): void {
    if (tokensSaved <= 0) return;
    try {
      const db = this.connect();
      const costSavedUsd = (tokensSaved * SONNET_INPUT_RATE_PER_MTOK) / 1_000_000;
      db.run(
        `INSERT INTO savings_events (timestamp, event_type, tokens_saved, cost_saved_usd, session_id, detail, model)
         VALUES (?, ?, ?, ?, ?, ?, ?)`,
        [
          new Date().toISOString(),
          eventType,
          tokensSaved,
          costSavedUsd,
          sessionId ?? null,
          detail ?? null,
          model ?? null,
        ],
      );
    } catch {
      // Best-effort: never crash the caller over savings tracking
    }
  }

  close(): void {
    if (this.db) {
      this.db.close();
      this.db = null;
    }
  }

  recordSession(data: SessionTrendData): void {
    const db = this.connect();
    const date = new Date().toISOString().split("T")[0];
    // Upsert that PRESERVES the original date + created_at. INSERT OR REPLACE is a
    // DELETE+INSERT, so a re-recorded session (double session.deleted) would jump
    // to today's date bucket and lose its original timestamp.
    db.run(
      `INSERT INTO session_log
       (session_id, date, project, model, tokens_input, tokens_output, tokens_cache_read, tokens_cache_write,
        cost_usd, resource_health, session_efficiency, tool_calls, compactions, mode, duration_seconds, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
       ON CONFLICT(session_id) DO UPDATE SET
         project=excluded.project, model=excluded.model,
         tokens_input=excluded.tokens_input, tokens_output=excluded.tokens_output,
         tokens_cache_read=excluded.tokens_cache_read, tokens_cache_write=excluded.tokens_cache_write,
         cost_usd=excluded.cost_usd, resource_health=excluded.resource_health,
         session_efficiency=excluded.session_efficiency, tool_calls=excluded.tool_calls,
         compactions=excluded.compactions, mode=excluded.mode,
         duration_seconds=excluded.duration_seconds`,
      [
        data.sessionId,
        date,
        data.project,
        data.model,
        data.tokensInput,
        data.tokensOutput,
        data.tokensCacheRead,
        data.tokensCacheWrite,
        data.costUsd,
        data.resourceHealth,
        data.sessionEfficiency,
        data.toolCalls,
        data.compactions,
        data.mode,
        data.durationSeconds,
        Date.now() / 1000,
      ],
    );
  }

  getRecentSessions(days: number = 30): Array<Record<string, unknown>> {
    const db = this.connect();
    const cutoff = new Date();
    cutoff.setDate(cutoff.getDate() - days);
    const cutoffStr = cutoff.toISOString().split("T")[0];

    return db
      .query("SELECT * FROM session_log WHERE date >= ? ORDER BY created_at DESC")
      .all(cutoffStr) as Array<Record<string, unknown>>;
  }

  /** All sessions ever recorded, oldest-first. Used to establish the realized-
   *  savings baseline (the earliest stable usage window). */
  getAllSessions(): Array<Record<string, unknown>> {
    const db = this.connect();
    return db
      .query("SELECT * FROM session_log ORDER BY created_at ASC")
      .all() as Array<Record<string, unknown>>;
  }

  getDailyStats(days: number = 30): Array<Record<string, unknown>> {
    const db = this.connect();
    const cutoff = new Date();
    cutoff.setDate(cutoff.getDate() - days);
    const cutoffStr = cutoff.toISOString().split("T")[0];

    return db
      .query(
        `SELECT date,
                COUNT(*) as sessions,
                SUM(tokens_input) as total_input,
                SUM(tokens_output) as total_output,
                AVG(COALESCE(resource_health, 0)) as avg_resource_health,
                AVG(COALESCE(session_efficiency, 0)) as avg_session_efficiency
         FROM session_log
         WHERE date >= ?
         GROUP BY date
         ORDER BY date DESC`,
      )
      .all(cutoffStr) as Array<Record<string, unknown>>;
  }
}
