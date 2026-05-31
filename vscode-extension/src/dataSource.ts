// Watches Token Optimizer's cache dir and produces a fresh Snapshot on change
// or on a slow timer (so session duration ticks and JSONL-fallback fill stays
// current even when nothing else writes). This is the only data module that
// imports `vscode`; all parsing is delegated to the vscode-free cacheReader.
import * as fs from 'fs';
import * as path from 'path';
import * as vscode from 'vscode';
import { ClaudePaths } from './paths';
import { findActiveSession, ActiveSession } from './sessionResolver';
import { JsonlTailer } from './jsonlTail';
import { buildSnapshot } from './cacheReader';
import { Snapshot, emptySnapshot } from './types';

const DEBOUNCE_MS = 400;
const TICK_MS = 5000;
const RESCAN_EVERY_TICKS = 6; // ~30s safety net for sessions that write no cache yet
const EFFORT_MAP: Record<string, string> = { low: 'lo', medium: 'med', high: 'hi' };

export class DataSource {
  private watcher: vscode.FileSystemWatcher | undefined;
  private focusSub: vscode.Disposable | undefined;
  private workspaceSub: vscode.Disposable | undefined;
  private timer: NodeJS.Timeout | undefined;
  private debounce: NodeJS.Timeout | undefined;
  private tailer: JsonlTailer | undefined;
  private disposed = false;
  // Session resolution and effort rarely change, so we don't re-scan every
  // project dir or re-read settings on each 5s tick — only when the cache dir
  // changes (a new session writes there) or on a periodic safety rescan.
  private cachedSession: ActiveSession | null = null;
  private cachedEffort: string | null = null;
  private needsRescan = true;
  private tick = 0;

  constructor(
    private paths: ClaudePaths,
    private getStaleAfterSeconds: () => number,
    private onSnapshot: (snap: Snapshot) => void
  ) {}

  start(): void {
    try {
      this.watcher = vscode.workspace.createFileSystemWatcher(
        new vscode.RelativePattern(vscode.Uri.file(this.paths.cacheDir), '*.json')
      );
      // Content changes (live-fill / rate-limits / quality-cache updates) belong
      // to the SAME session, so they must NOT trigger a session re-scan — those
      // writes fire several times a second and re-scanning stat-walks every
      // project dir. Only a new or removed file can mean a session change.
      this.watcher.onDidChange(() => this.scheduleRefresh(false));
      this.watcher.onDidCreate(() => this.scheduleRefresh(true));
      this.watcher.onDidDelete(() => this.scheduleRefresh(true));
    } catch {
      // Watching is best-effort; the timer below still keeps us current.
    }
    this.timer = setInterval(() => {
      // Don't poll while the window is unfocused — nobody is looking, and the
      // watcher still catches real changes. Focus regain forces a refresh below.
      if (!this.isFocused()) return;
      this.tick++;
      if (this.tick % RESCAN_EVERY_TICKS === 0) this.needsRescan = true;
      this.refresh();
    }, TICK_MS);
    try {
      this.focusSub = vscode.window.onDidChangeWindowState((s) => {
        if (s.focused) this.refresh();
      });
    } catch {
      // window state API unavailable — timer + watcher still cover us.
    }
    try {
      this.workspaceSub = vscode.workspace.onDidChangeWorkspaceFolders(() => {
        this.needsRescan = true;
        this.refresh();
      });
    } catch {
      // ignore — folder rarely changes mid-session.
    }
    // Defer the first (synchronous, fs-walking) refresh off the activation path
    // so activate() returns immediately and never trips VS Code's >500ms watchdog.
    setImmediate(() => this.refresh());
  }

  // The window's workspace folder, used to scope session resolution to this
  // window. First folder when a multi-root workspace; undefined if none open.
  private workspaceDir(): string | null {
    try {
      return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? null;
    } catch {
      return null;
    }
  }

  private isFocused(): boolean {
    try {
      return vscode.window.state.focused;
    } catch {
      return true; // if the API is unavailable, behave as before (always poll)
    }
  }

  private scheduleRefresh(rescan: boolean): void {
    if (rescan) this.needsRescan = true;
    if (this.debounce) clearTimeout(this.debounce);
    this.debounce = setTimeout(() => this.refresh(), DEBOUNCE_MS);
  }

  refresh(): void {
    if (this.disposed) return;
    let snap: Snapshot;
    try {
      snap = this.buildFromDisk();
    } catch {
      snap = emptySnapshot();
    }
    this.onSnapshot(snap);
  }

  private buildFromDisk(): Snapshot {
    if (this.needsRescan) {
      this.cachedSession = findActiveSession(this.paths.projectsDir, {
        workspaceDir: this.workspaceDir(),
      });
      this.cachedEffort = this.readEffort();
      this.needsRescan = false;
    }
    const session = this.cachedSession;

    let jsonlTokens: number | null = null;
    let jsonlModel: string | null = null;
    if (session) {
      if (!this.tailer) this.tailer = new JsonlTailer(session.jsonlPath);
      else this.tailer.setPath(session.jsonlPath);
      const tail = this.tailer.read();
      jsonlTokens = tail.tokens;
      jsonlModel = tail.model;
    } else {
      // No session: drop the tailer so a future session starts from offset 0.
      this.tailer = undefined;
    }

    return buildSnapshot({
      qualityJson: session ? readIfExists(this.paths.qualityCache(session.sessionId)) : null,
      liveFillJson: readIfExists(this.paths.liveFill),
      rateLimitsJson: readIfExists(this.paths.rateLimits),
      jsonlTokens,
      jsonlModel,
      effort: this.cachedEffort,
      sessionId: session ? session.sessionId : null,
      scoped: this.workspaceDir() != null,
      nowMs: Date.now(),
      staleAfterSeconds: this.getStaleAfterSeconds(),
    });
  }

  private readEffort(): string | null {
    const raw = readIfExists(path.join(this.paths.claudeDir, 'settings.json'));
    if (!raw) return null;
    try {
      const level = JSON.parse(raw).effortLevel;
      return level ? EFFORT_MAP[level] || level : null;
    } catch {
      return null;
    }
  }

  dispose(): void {
    this.disposed = true;
    if (this.debounce) clearTimeout(this.debounce);
    if (this.timer) clearInterval(this.timer);
    this.watcher?.dispose();
    this.focusSub?.dispose();
    this.workspaceSub?.dispose();
  }
}

function readIfExists(filePath: string): string | null {
  try {
    return fs.readFileSync(filePath, 'utf8');
  } catch {
    return null;
  }
}
