/**
 * GitHub #103 — per-project scoping for the lean resume block.
 *
 * A two-project checkpoint leaks project A's Key Decisions into project B's
 * hint because the checkpoint's session-wide fields are dumped verbatim once
 * the checkpoint clears the same-project gate. The fix is a set-overlap
 * keep/drop rule (``keepRecoveredItem``) applied to each recovered item BEFORE
 * the existing slices, with one disclosure line emitted only when something
 * is dropped.
 *
 * The parity fixture (``PARITY_FIXTURE``) is duplicated verbatim in the Python
 * and OpenClaw scoping tests so all three runtimes' keep/drop decisions are
 * asserted against the SAME token inputs.
 */
import { test, expect } from "bun:test";
import { buildLeanResumeContext, keepRecoveredItem, type CheckpointRow } from "./resume-lean.js";

// ---------------------------------------------------------------------------
// Shared parity fixture — MUST stay byte-identical to the Python + OpenClaw
// scoping tests. (item_text, keep_tokens, expected_keep)
// ---------------------------------------------------------------------------

const PARITY_FIXTURE: Array<[string, Set<string>, boolean]> = [
  // < 3 distinctive tokens -> inconclusive -> keep (even with zero overlap)
  ["gamma delta", new Set(["alpha", "beta"]), true],
  // >= 3 distinctive tokens, zero overlap -> DROP
  ["refactor gamma delta epsilon module", new Set(["alpha", "beta"]), false],
  // >= 3 distinctive tokens, nonempty overlap -> keep
  ["refactor gamma delta alpha module", new Set(["alpha", "beta"]), true],
  // Full paths are SINGLE tokens with regex [a-zA-Z0-9_./:-]+ (slashes are
  // in the class), so they have < 3 distinctive tokens -> always kept.
  // This is by design: the same-project gate handles checkpoint-level path
  // filtering; the per-item filter targets session-wide TEXT fields.
  ["/home/u/alpha/src/main.py", new Set(["alpha", "main"]), true],
  ["/home/u/gamma/src/other.py", new Set(["alpha", "main"]), true],
  // empty item -> keep
  ["", new Set(["alpha"]), true],
];

// ---------------------------------------------------------------------------
// Parity: the decision function on a shared token fixture
// ---------------------------------------------------------------------------

test("keepRecoveredItem matches the shared parity fixture exactly", () => {
  for (const [itemText, keepTokens, expected] of PARITY_FIXTURE) {
    const got = keepRecoveredItem(itemText, keepTokens);
    expect(got).toBe(expected);
  }
});

test("keepRecoveredItem is purely set-overlap, no float threshold", () => {
  // A huge item with zero overlap still drops; a tiny item with zero overlap
  // still keeps. No score is computed.
  expect(keepRecoveredItem("alpha beta gamma delta epsilon zeta", new Set())).toBe(false);
  expect(keepRecoveredItem("alpha beta", new Set())).toBe(true);
});

// ---------------------------------------------------------------------------
// buildLeanResumeContext — mixed A/B checkpoint queried from B
// ---------------------------------------------------------------------------

function makeCheckpointRow(overrides: Partial<CheckpointRow> = {}): CheckpointRow {
  return {
    session_id: "abcdefgh1234",
    trigger: "manual",
    dbPath: "/tmp/test.db",
    created_at: Math.floor(Date.now() / 1000),
    active_files: JSON.stringify([]),
    decisions: JSON.stringify([]),
    content: "## Topic Summary\nwork on the beta feature\n",
    mode: "code",
    quality_score: 85,
    fill_pct: 70,
    ...overrides,
  };
}

const PROJ_B = "/home/u/beta";
const PROJ_A = "/home/u/gamma";

function mixedAbCheckpoint(): CheckpointRow {
  return makeCheckpointRow({
    active_files: JSON.stringify([
      `${PROJ_B}/src/beta_router.py`,
      `${PROJ_A}/src/gamma_engine.py`,
    ]),
    decisions: JSON.stringify([
      // B-overlapping: names beta (cwd basename) -> keep
      "Ship the beta feature behind a feature flag",
      // A-only: names only gamma/alpha (project A) -> drop
      "Refactor the gamma delta epsilon module for project alpha",
      // B-overlapping: names beta_router (B file stem) -> keep
      "Wire beta_router into the request pipeline",
    ]),
    content: "## Topic Summary\nwork on the beta feature\n",
  });
}

test("buildLeanResumeContext drops A-only decisions and emits one disclosure", () => {
  const cp = mixedAbCheckpoint();
  const block = buildLeanResumeContext(cp, "abcdefgh1234", 3500, "continue the beta work", PROJ_B);

  // B-overlapping decisions kept:
  expect(block).toContain("beta feature behind a feature flag");
  expect(block).toContain("beta_router");
  // A-only DECISION dropped:
  expect(block).not.toContain("gamma delta epsilon");
  // Exactly one disclosure line. File paths are single-token -> kept, so
  // F=0 is elided. Only the 1 dropped decision is reported:
  const disclosureCount = (block.match(/- Omitted \(same session, different project\):/g) || []).length;
  expect(disclosureCount).toBe(1);
  expect(block).toContain("- Omitted (same session, different project): 1 decision(s)");
  expect(block).not.toContain("file(s)");
});

test("buildLeanResumeContext single-project checkpoint emits NO disclosure", () => {
  const cp = makeCheckpointRow({
    active_files: JSON.stringify([
      `${PROJ_B}/src/beta_router.py`,
      `${PROJ_B}/src/beta_core.py`,
    ]),
    decisions: JSON.stringify([
      "Ship the beta feature behind a feature flag",
      "Wire beta_router into the request pipeline",
    ]),
  });
  const block = buildLeanResumeContext(cp, "abcdefgh1234", 3500, "continue the beta work", PROJ_B);

  expect(block).toContain("beta feature");
  expect(block).toContain("beta_router");
  expect(block).not.toContain("- Omitted");
});

test("buildLeanResumeContext no filter when cwd absent (backward compat)", () => {
  const cp = mixedAbCheckpoint();
  // Legacy call: no promptText/cwd -> unfiltered, A-only items survive, no
  // disclosure line.
  const block = buildLeanResumeContext(cp, "abcdefgh1234");

  expect(block).toContain("gamma delta epsilon");
  expect(block).not.toContain("- Omitted");
});
