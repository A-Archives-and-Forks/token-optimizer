// Filesystem locations the companion reads. All derived from the Claude home
// dir so tests can point at a fixture by passing an explicit base.
import * as os from 'os';
import * as path from 'path';
import * as fs from 'fs';

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
export type Runtime = 'auto' | 'claude' | 'copilot';

// Most-recent mtime (ms) of any file directly in `dir`, or 0 if the dir is
// missing/empty/unreadable. Used as a cheap "was this runtime active recently"
// signal. The token-optimizer cache dir is a small flat dir, so a single
// readdir + stat pass is inexpensive and only runs at activation / config change.
function mostRecentMtimeMs(dir: string): number {
  try {
    let max = 0;
    for (const entry of fs.readdirSync(dir)) {
      try {
        const m = fs.statSync(path.join(dir, entry)).mtimeMs;
        if (m > max) max = m;
      } catch {
        // unreadable entry — skip
      }
    }
    return max;
  } catch {
    return 0; // dir absent or unreadable
  }
}

// Auto-detect the active runtime so Copilot users don't have to flip a setting.
// Compares the most recent write to each runtime's token-optimizer cache and
// prefers Copilot ONLY when its cache exists and is strictly more recent than
// Claude's. A pure-Copilot user (no Claude cache) gets Copilot; a pure-Claude
// user or the no-data case falls back to Claude (the historical default); a
// mixed user follows whichever runtime they used most recently.
export function resolveAutoPaths(homeDir: string = os.homedir()): ClaudePaths {
  const claudeCache = path.join(homeDir, '.claude', 'token-optimizer');
  const copilotCache = path.join(homeDir, '.copilot', 'token-optimizer');
  const copilotMs = mostRecentMtimeMs(copilotCache);
  const claudeMs = mostRecentMtimeMs(claudeCache);
  if (copilotMs > 0 && copilotMs > claudeMs) return resolveCopilotPaths(homeDir);
  return resolvePaths(homeDir);
}

// Has the Token Optimizer CLI plugin ever run on this machine? True when a
// token-optimizer cache dir exists under any known runtime home (~/.claude or
// ~/.copilot) — the plugin creates it on first run. Used to tell "installed but
// idle" (show the normal empty state) from "extension present, plugin missing"
// (show the install funnel). Directory presence, not freshness: a plugin the
// user hasn't run today is still installed.
export function detectPluginInstalled(homeDir: string = os.homedir()): boolean {
  const candidates = [
    path.join(homeDir, '.claude', 'token-optimizer'),
    path.join(homeDir, '.copilot', 'token-optimizer'),
  ];
  for (const dir of candidates) {
    try {
      if (fs.statSync(dir).isDirectory()) return true;
    } catch {
      // absent/unreadable — try the next candidate
    }
  }
  return false;
}

// Factory: pick the right paths object based on the runtime setting string.
// 'claude' / 'copilot' are explicit overrides; 'auto' (the default) and any
// unrecognized value resolve by detecting the most recently active runtime.
export function resolvePathsForRuntime(
  runtime: string | undefined,
  homeDir: string = os.homedir()
): ClaudePaths {
  if (runtime === 'copilot') return resolveCopilotPaths(homeDir);
  if (runtime === 'claude') return resolvePaths(homeDir);
  return resolveAutoPaths(homeDir);
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
