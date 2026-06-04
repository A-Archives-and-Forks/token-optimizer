import { tool, type ToolDefinition } from "@opencode-ai/plugin";
import { writeDashboard } from "../dashboard/generator.js";

export function createDashboardTool(
  getDataDir: () => string,
  onBeforeGenerate?: () => void,
): ToolDefinition {
  return tool({
    description:
      "Generate and open the Token Optimizer dashboard. Shows quality trends, session history, " +
      "and daily stats in an interactive HTML page.",
    args: {
      days: tool.schema.number().optional().describe("Number of days to include (default 30)"),
    },
    async execute(args) {
      const dataDir = getDataDir();
      const days = Math.max(1, Math.min(args.days ?? 30, 365));

      try {
        // Roll live sessions into trends.db first so the dashboard is never
        // empty just because no session-end event has fired yet (#54).
        try {
          onBeforeGenerate?.();
        } catch (err) {
          console.warn("[Token Optimizer] dashboard pre-flush failed:", err);
        }

        const outputPath = writeDashboard({ dataDir, days });

        const { execFileSync } = await import("node:child_process");
        const platform = process.platform;
        if (platform === "darwin") {
          execFileSync("open", [outputPath]);
        } else if (platform === "linux") {
          try { execFileSync("xdg-open", [outputPath]); } catch { execFileSync("sensible-browser", [outputPath]); }
        } else if (platform === "win32") {
          execFileSync("cmd", ["/c", "start", "", outputPath]);
        }

        return {
          title: "Dashboard Generated",
          output: `Dashboard written to ${outputPath} and opened in browser.\n\nShowing ${days} days of session data.`,
        };
      } catch (err) {
        return {
          title: "Dashboard Error",
          output: `Failed to generate dashboard: ${err instanceof Error ? err.message : String(err)}`,
        };
      }
    },
  });
}
