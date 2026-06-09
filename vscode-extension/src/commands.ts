// Command handlers: open the dashboard and a manual refresh.
import * as fs from 'fs';
import * as http from 'http';
import * as vscode from 'vscode';
import { ClaudePaths } from './paths';

const DAEMON_URL = 'http://localhost:24842/token-optimizer';

export interface CommandDeps {
  // FIX 1: Accept a getter so openDashboard always resolves paths lazily,
  // picking up any runtime switch that happened after activation.
  getPaths: () => ClaudePaths;
  onConfigChanged: () => void; // re-read from disk + re-render immediately
}

export function registerCommands(
  context: vscode.ExtensionContext,
  deps: CommandDeps
): void {
  context.subscriptions.push(
    vscode.commands.registerCommand('tokenOptimizer.openDashboard', () => openDashboard(deps.getPaths())),
    vscode.commands.registerCommand('tokenOptimizer.refresh', () => deps.onConfigChanged())
  );
}

async function openDashboard(paths: ClaudePaths): Promise<void> {
  if (await daemonAlive()) {
    await vscode.env.openExternal(vscode.Uri.parse(DAEMON_URL));
    return;
  }
  if (fs.existsSync(paths.dashboardFile)) {
    await vscode.env.openExternal(vscode.Uri.file(paths.dashboardFile));
    return;
  }
  vscode.window.showInformationMessage(
    'Token Optimizer dashboard not found yet. Run /token-dashboard once in Claude Code to generate it.'
  );
}

function daemonAlive(): Promise<boolean> {
  return new Promise((resolve) => {
    const req = http.get(DAEMON_URL, { timeout: 600 }, (res) => {
      res.resume();
      resolve(!!res.statusCode && res.statusCode < 500);
    });
    req.on('error', () => resolve(false));
    req.on('timeout', () => {
      req.destroy();
      // FIX 4: Suppress the unhandled error event that fires after destroy().
      req.on('error', () => {});
      resolve(false);
    });
  });
}
