// Filesystem locations the companion reads. All derived from the Claude home
// dir so tests can point at a fixture by passing an explicit base.
import * as os from 'os';
import * as path from 'path';

export interface ClaudePaths {
  claudeDir: string;
  cacheDir: string; // ~/.claude/token-optimizer  (or ~/.copilot/token-optimizer)
  projectsDir: string; // ~/.claude/projects  (unused/empty for Copilot mode)
  liveFill: string;
  rateLimits: string;
  dashboardFile: string;
  qualityCache(sessionId: string): string;
  // Copilot-only: directory that holds per-session state dirs.
  // Undefined for Claude mode; used by findActiveCopilotSession.
  sessionStateDir?: string;
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
    dashboardFile: path.join(claudeDir, '_backups', 'token-optimizer', 'dashboard.html'),
    qualityCache: (sessionId: string) =>
      path.join(cacheDir, `quality-cache-${sanitizeSessionId(sessionId)}.json`),
  };
}

// Paths for a GitHub Copilot Token Optimizer install at ~/.copilot/token-optimizer/.
// The cache-file names (live-fill.json, rate-limits.json, quality-cache-*.json) are
// intentionally identical to the Claude layout so cacheReader / dataSource are
// runtime-agnostic — only the base directory changes.
export function resolveCopilotPaths(homeDir: string = os.homedir()): ClaudePaths {
  const copilotDir = path.join(homeDir, '.copilot');
  const cacheDir = path.join(copilotDir, 'token-optimizer');
  return {
    // claudeDir is used only by DataSource.readEffort (reads ~/.claude/settings.json).
    // For Copilot mode there is no equivalent settings file, so we point at the
    // copilot dir; readEffort will return null gracefully when the file is absent.
    claudeDir: copilotDir,
    cacheDir,
    // Copilot does not use the Claude projects transcript layout.  DataSource's
    // findActiveSession call is skipped when sessionStateDir is set (see dataSource.ts).
    projectsDir: path.join(copilotDir, 'projects'),
    liveFill: path.join(cacheDir, 'live-fill.json'),
    rateLimits: path.join(cacheDir, 'rate-limits.json'),
    dashboardFile: path.join(copilotDir, 'token-optimizer', 'dashboard.html'),
    qualityCache: (sessionId: string) =>
      path.join(cacheDir, `quality-cache-${sanitizeSessionId(sessionId)}.json`),
    sessionStateDir: path.join(copilotDir, 'session-state'),
  };
}

// The runtime setting value.  Matches tokenOptimizer.runtime in package.json.
export type Runtime = 'claude' | 'copilot';

// Factory: pick the right paths object based on the runtime setting string.
// Falls back to Claude paths for any unrecognized value.
export function resolvePathsForRuntime(
  runtime: string | undefined,
  homeDir: string = os.homedir()
): ClaudePaths {
  if (runtime === 'copilot') return resolveCopilotPaths(homeDir);
  return resolvePaths(homeDir);
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
