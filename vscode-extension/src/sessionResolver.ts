// Pick the Claude Code session that belongs to this window. We mirror
// measure.py's _find_current_session_jsonl: the globally most-recently-modified
// transcript across all project dirs is almost always the active session.
// vscode-free so it can be unit-tested against a fixture tree.
import * as fs from 'fs';
import * as path from 'path';
import { sanitizeSessionId } from './paths';

export interface ActiveSession {
  sessionId: string;
  jsonlPath: string;
  mtimeMs: number;
}

export function findActiveSession(projectsDir: string): ActiveSession | null {
  let best: ActiveSession | null = null;
  let entries: fs.Dirent[];
  try {
    entries = fs.readdirSync(projectsDir, { withFileTypes: true });
  } catch {
    return null;
  }
  for (const dir of entries) {
    if (!dir.isDirectory()) continue;
    const projectPath = path.join(projectsDir, dir.name);
    let files: string[];
    try {
      files = fs.readdirSync(projectPath);
    } catch {
      continue;
    }
    for (const file of files) {
      if (!file.endsWith('.jsonl')) continue;
      const full = path.join(projectPath, file);
      let mtimeMs: number;
      try {
        mtimeMs = fs.statSync(full).mtimeMs;
      } catch {
        continue;
      }
      if (!best || mtimeMs > best.mtimeMs) {
        // Sanitize at the source so the invariant holds at construction, not
        // only at the single current path consumer.
        best = {
          sessionId: sanitizeSessionId(file.replace(/\.jsonl$/, '')),
          jsonlPath: full,
          mtimeMs,
        };
      }
    }
  }
  return best;
}
