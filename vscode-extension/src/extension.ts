// Entry point: wire DataSource -> StatusBar, register commands. Rate limits come
// straight from the statusline sidecar on the snapshot (no network lookups).
import * as vscode from 'vscode';
import { resolvePaths } from './paths';
import { DataSource } from './dataSource';
import { StatusBar } from './statusBar';
import { StatusPanel, PanelAction } from './statusPanel';
import { registerCommands } from './commands';
import { buildPanelModel } from './format';
import { Snapshot } from './types';

export function activate(context: vscode.ExtensionContext): void {
  const paths = resolvePaths();
  const statusBar = new StatusBar();

  const cfg = () => vscode.workspace.getConfiguration('tokenOptimizer');
  const staleAfter = () => cfg().get<number>('staleAfterSeconds', 180);

  let disposed = false;

  const statusPanel = new StatusPanel((action: PanelAction) => {
    void vscode.commands.executeCommand(`tokenOptimizer.${action}`);
  });

  const renderFrom = (snap: Snapshot): void => {
    if (disposed) return; // a queued render may resume mid-disposal
    try {
      statusBar.render(snap);
      statusPanel.update(buildPanelModel(snap, { nowMs: Date.now() }));
    } catch {
      // Rendering must never break the editor.
    }
  };

  const dataSource = new DataSource(paths, staleAfter, renderFrom);

  // Re-read from disk and recompute transcript estimates on explicit refresh.
  const refreshNow = () => dataSource.refresh(true);

  registerCommands(context, { paths, onConfigChanged: refreshNow });

  // Clicking the status bar opens the expanded panel.
  context.subscriptions.push(
    vscode.commands.registerCommand('tokenOptimizer.showStatus', () => {
      refreshNow();
      statusPanel.show();
    })
  );

  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration('tokenOptimizer')) dataSource.refresh(false);
    })
  );

  // Disposal order (reverse of push): the `disposed` flag flips FIRST, before
  // dataSource and statusBar are torn down, so an in-flight renderFrom bails out
  // before touching a disposed status bar item.
  context.subscriptions.push(statusBar);
  context.subscriptions.push({ dispose: () => statusPanel.dispose() });
  context.subscriptions.push({ dispose: () => dataSource.dispose() });
  context.subscriptions.push({ dispose: () => { disposed = true; } });

  dataSource.start();
}

export function deactivate(): void {
  // Disposables registered on context.subscriptions are cleaned up by VS Code.
}
