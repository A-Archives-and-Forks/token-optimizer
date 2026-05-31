// Entry point: wire DataSource -> UsageProvider -> StatusBar, register commands.
import * as vscode from 'vscode';
import { resolvePaths } from './paths';
import { DataSource } from './dataSource';
import { UsageProvider } from './usageProvider';
import { StatusBar } from './statusBar';
import { StatusPanel, PanelAction } from './statusPanel';
import { registerCommands } from './commands';
import { buildPanelModel } from './format';
import { Snapshot } from './types';

export function activate(context: vscode.ExtensionContext): void {
  const paths = resolvePaths();
  const statusBar = new StatusBar();
  const usage = new UsageProvider();

  const cfg = () => vscode.workspace.getConfiguration('tokenOptimizer');
  const liveUsageOn = () => cfg().get<boolean>('liveUsage', false);
  const staleAfter = () => cfg().get<number>('staleAfterSeconds', 180);

  let disposed = false;
  let renderSeq = 0;

  const statusPanel = new StatusPanel((action: PanelAction) => {
    void vscode.commands.executeCommand(`tokenOptimizer.${action}`);
  });

  const renderFrom = async (snap: Snapshot): Promise<void> => {
    const seq = ++renderSeq;
    try {
      // resolve() may await an OAuth fetch (seconds). It mutates snap in place.
      await usage.resolve(snap, {
        liveUsageOn: liveUsageOn(),
        credentialsPath: paths.credentials,
        nowMs: Date.now(),
      });
    } catch {
      // resolve is defensive, but never let a rejection escape this `void`-ed
      // call as an unhandled rejection — fall through and render what we have.
    }
    // Drop a slow in-flight render that a newer one already superseded, and
    // never touch the status bar after disposal.
    if (disposed || seq !== renderSeq) return;
    try {
      statusBar.render(snap, liveUsageOn());
      statusPanel.update(buildPanelModel(snap, { liveUsageOn: liveUsageOn(), nowMs: Date.now() }));
    } catch {
      // Rendering must never break the editor.
    }
  };

  const dataSource = new DataSource(paths, staleAfter, (snap) => {
    void renderFrom(snap);
  });

  // Re-read from disk on any config change (the Live Usage toggle, settings UI,
  // or the explicit refresh command) rather than re-using the last snapshot —
  // that snapshot was already resolved/mutated, so its non-stale rate limits
  // would short-circuit the freshly-enabled OAuth fetch.
  const onConfigChanged = () => {
    usage.invalidate();
    dataSource.refresh();
  };

  registerCommands(context, { paths, onConfigChanged });

  // Clicking the status bar opens the expanded panel.
  context.subscriptions.push(
    vscode.commands.registerCommand('tokenOptimizer.showStatus', () => statusPanel.show())
  );

  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration('tokenOptimizer')) onConfigChanged();
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
