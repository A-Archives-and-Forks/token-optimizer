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
    }
    return this.db;
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
