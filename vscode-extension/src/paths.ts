// Filesystem locations the companion reads. All derived from the Claude home
// dir so tests can point at a fixture by passing an explicit base.
import * as os from 'os';
import * as path from 'path';

export interface ClaudePaths {
  claudeDir: string;
  cacheDir: string; // ~/.claude/token-optimizer
  projectsDir: string; // ~/.claude/projects
  liveFill: string;
  rateLimits: string;
  credentials: string;
  dashboardFile: string;
  qualityCache(sessionId: string): string;
}

export function resolvePaths(homeDir: string = os.homedir()): ClaudePaths {
  const claudeDir = path.join(homeDir, '.claude');
  const cacheDir = path.join(claudeDir, 'token-optimizer');
  return {
    claudeDir,
    cacheDir,
    projectsDir: path.join(claudeDir, 'projects'),
    liveFill: path.join(cacheDir, 'live-fill.json'),
    rateLimits: path.join(cacheDir, 'rate-limits.json'),
    credentials: path.join(claudeDir, '.credentials.json'),
    dashboardFile: path.join(claudeDir, '_backups', 'token-optimizer', 'dashboard.html'),
    qualityCache: (sessionId: string) =>
      path.join(cacheDir, `quality-cache-${sanitizeSessionId(sessionId)}.json`),
  };
}

// Mirror measure.py's sanitize: strip anything outside [A-Za-z0-9_-] so a
// crafted session id can never escape the cache dir.
export function sanitizeSessionId(sessionId: string): string {
  return (sessionId || '').replace(/[^a-zA-Z0-9_-]/g, '');
}

// Claude Code names each transcript directory after the session's cwd, with
// every non-alphanumeric character replaced by '-' (so '/Users/x/.claude' →
// '-Users-x--claude'). Used to scope session resolution to the window's folder.
export function encodeProjectDir(cwd: string): string {
  return (cwd || '').replace(/[^a-zA-Z0-9]/g, '-');
}
