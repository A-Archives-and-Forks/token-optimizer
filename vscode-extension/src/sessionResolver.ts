// Pick the Claude Code session that belongs to THIS window.
//
// Claude Code stores each transcript at projects/<encoded-cwd>/<session>.jsonl.
// The naive "globally most-recent JSONL" heuristic shows the wrong session when
// several Claude sessions run at once (a different window's session can be more
// recently written). So when the window has a workspace folder, we scope to the
// transcript dir(s) for that folder (and any subfolder cwd), and only fall back
// to global-most-recent when no folder is open.
import * as fs from 'fs';
import * as path from 'path';
import { sanitizeSessionId, encodeProjectDir } from './paths';

export interface ActiveSession {
  sessionId: string;
  jsonlPath: string;
  mtimeMs: number;
}

export interface ResolveOptions {
  // Absolute path of the window's workspace folder, if any.
  workspaceDir?: string | null;
}

export function findActiveSession(
  projectsDir: string,
  opts: ResolveOptions = {}
): ActiveSession | null {
  let dirs: fs.Dirent[];
  try {
    dirs = fs.readdirSync(projectsDir, { withFileTypes: true });
  } catch {
    return null;
  }

  if (opts.workspaceDir) {
    const prefix = encodeProjectDir(opts.workspaceDir);
    // Prefer an EXACT cwd match (claude run at the workspace root — the common
    // case). Only this avoids the greedy-ancestor trap: a workspace like
    // '/Users/me' would otherwise prefix-match every project beneath it.
    const exact = dirs.filter((d) => d.isDirectory() && d.name === prefix);
    const exactHit = newestSessionIn(projectsDir, exact);
    if (exactHit) return exactHit;

    // No session at the root — fall back to subfolder cwds (claude run in a
    // subdirectory). The '-' boundary stops '/a/proj' matching '/a/proj2'. A
    // hyphenated sibling ('/a/proj-extra') is genuinely ambiguous from '/a/proj'
    // (both '/' and '-' encode to '-'), but the exact-match above covers the
    // common case, so this only over-matches when the workspace has no session
    // of its own AND such a sibling exists — rare and self-correcting.
    const sub = dirs.filter((d) => d.isDirectory() && d.name.startsWith(prefix + '-'));
    // A workspace IS open: only show its session. If none, report no session
    // rather than a different window's — that wrong data is the bug we're fixing.
    return newestSessionIn(projectsDir, sub);
  }

  // No workspace folder open — best effort: globally most-recent.
  return newestSessionIn(projectsDir, dirs.filter((d) => d.isDirectory()));
}

function newestSessionIn(projectsDir: string, dirs: fs.Dirent[]): ActiveSession | null {
  let best: ActiveSession | null = null;
  for (const dir of dirs) {
    const projectPath = path.join(projectsDir, dir.name);
    let files: string[];
    try {
      files = fs.readdirSync(projectPath);
    } catch {
      continue;
    }
    for (const file of files) {
      if (!file.endsWith('.jsonl')) continue; // skips the subagents/ subdir
      const full = path.join(projectPath, file);
      let mtimeMs: number;
      try {
        mtimeMs = fs.statSync(full).mtimeMs;
      } catch {
        continue;
      }
      if (!best || mtimeMs > best.mtimeMs) {
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
