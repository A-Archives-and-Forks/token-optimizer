/**
 * GitHub #103 — per-project scoping for OpenClaw continuity injection.
 *
 * A two-project checkpoint leaks project A's Key Decisions into project B's
 * hint because the checkpoint's session-wide fields are dumped verbatim once
 * the checkpoint clears the same-project gate. The fix is a set-overlap
 * keep/drop rule (``keepRecoveredItem``) applied to each recovered item BEFORE
 * the existing slices, with one disclosure line emitted only when something
 * is dropped.
 *
 * The parity fixture (``PARITY_FIXTURE``) is duplicated verbatim in the Python
 * and OpenCode scoping tests so all three runtimes' keep/drop decisions are
 * asserted against the SAME token inputs.
 */
import { test, expect } from "bun:test";
import {
  keepRecoveredItem,
  buildResumeLeanBlock,
  buildContinuityHint,
  type CheckpointEntry,
  type ContinuityCandidate,
} from "./continuity.js";

// ---------------------------------------------------------------------------
// Shared parity fixture — MUST stay byte-identical to the Python + OpenCode
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
  // in the class), so they have < 3 distinctive tokens -> keepRecoveredItem
  // always keeps them. Cross-project FILE paths are dropped by a SEPARATE
  // rule at the filter sites (crossProjectFileDrop: an absolute-path prefix
  // check against cwd), NOT by this decision function; the per-item token
  // filter targets session-wide TEXT fields (Key Decisions).
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
  expect(keepRecoveredItem("alpha beta gamma delta epsilon zeta", new Set())).toBe(false);
  expect(keepRecoveredItem("alpha beta", new Set())).toBe(true);
});

// ---------------------------------------------------------------------------
// buildResumeLeanBlock — mixed A/B checkpoint queried from B
// ---------------------------------------------------------------------------

const PROJ_B = "/home/u/beta";
const PROJ_A = "/home/u/gamma";

function makeEntry(): CheckpointEntry {
  return {
    path: "/tmp/checkpoints/test-session.md",
    sessionDirName: "testsession",
    trigger: "auto",
    createdAt: Date.now(),
  };
}

function mixedAbCheckpointMd(): string {
  // OpenClaw checkpoint .md format: blockquote header + ## sections with
  // "- " bullet items parsed by parseCheckpointSections.
  return [
    "> Quality: B (82/100)",
    "> Fill: 70%",
    "",
    "## Key Decisions",
    "- Ship the beta feature behind a feature flag",
    "- Refactor the gamma delta epsilon module for project alpha",
    "- Wire beta_router into the request pipeline",
    "",
    "## File Changes",
    `- ${PROJ_B}/src/beta_router.py`,
    `- ${PROJ_A}/src/gamma_engine.py`,
    "",
    "## Recent Messages",
    "### User",
    "work on the beta feature",
    "",
  ].join("\n");
}

test("buildResumeLeanBlock drops A-only decisions and emits one disclosure", () => {
  const entry = makeEntry();
  const content = mixedAbCheckpointMd();
  const block = buildResumeLeanBlock(entry, content, 3500, "continue the beta work", PROJ_B);

  // B-overlapping decisions kept:
  expect(block).toContain("beta feature behind a feature flag");
  expect(block).toContain("beta_router");
  // A-only DECISION dropped:
  expect(block).not.toContain("gamma delta epsilon");
  // A-only FILE path dropped (cross-project absolute path not under cwd):
  expect(block).not.toContain("gamma_engine");
  // Exactly one disclosure line. Both the A-only decision AND the A-only
  // file path are dropped, so the disclosure reports both categories:
  const disclosureCount = (block.match(/- Omitted \(same session, different project\):/g) || []).length;
  expect(disclosureCount).toBe(1);
  expect(block).toContain("- Omitted (same session, different project): 1 decision(s), 1 file(s)");
});

test("buildResumeLeanBlock single-project checkpoint emits NO disclosure", () => {
  const entry = makeEntry();
  const content = [
    "> Quality: A (95/100)",
    "",
    "## Key Decisions",
    "- Ship the beta feature behind a feature flag",
    "- Wire beta_router into the request pipeline",
    "",
    "## File Changes",
    `- ${PROJ_B}/src/beta_router.py`,
    `- ${PROJ_B}/src/beta_core.py`,
    "",
    "## Recent Messages",
    "### User",
    "work on the beta feature",
    "",
  ].join("\n");
  const block = buildResumeLeanBlock(entry, content, 3500, "continue the beta work", PROJ_B);

  expect(block).toContain("beta feature");
  expect(block).toContain("beta_router");
  expect(block).not.toContain("- Omitted");
});

test("buildResumeLeanBlock no filter when cwd absent (backward compat)", () => {
  const entry = makeEntry();
  const content = mixedAbCheckpointMd();
  // Legacy call: no promptText/cwd -> unfiltered, A-only items survive, no
  // disclosure line.
  const block = buildResumeLeanBlock(entry, content);

  expect(block).toContain("gamma delta epsilon");
  expect(block).not.toContain("- Omitted");
});

// ---------------------------------------------------------------------------
// buildContinuityHint — filtered rebuild replaces raw 800-char excerpt
// ---------------------------------------------------------------------------

test("buildContinuityHint drops A-only decisions and emits disclosure", () => {
  const entry = makeEntry();
  const content = mixedAbCheckpointMd();
  const candidate: ContinuityCandidate = {
    entry,
    score: 0.9,
    content,
  };
  const hint = buildContinuityHint(candidate, "continue the beta work", PROJ_B);

  // B-overlapping decision kept in the filtered rebuild:
  expect(hint).toContain("beta feature behind a feature flag");
  // A-only DECISION dropped:
  expect(hint).not.toContain("gamma delta epsilon");
  // A-only FILE path dropped (cross-project absolute path not under cwd):
  expect(hint).not.toContain("gamma_engine");
  // Exactly one disclosure line. Both the A-only decision AND the A-only
  // file path are dropped, so the disclosure reports both categories:
  const disclosureCount = (hint.match(/- Omitted \(same session, different project\):/g) || []).length;
  expect(disclosureCount).toBe(1);
  expect(hint).toContain("- Omitted (same session, different project): 1 decision(s), 1 file(s)");
});

test("buildContinuityHint single-project checkpoint emits NO disclosure", () => {
  const entry = makeEntry();
  const content = [
    "> Quality: A (95/100)",
    "",
    "## Key Decisions",
    "- Ship the beta feature behind a feature flag",
    "",
    "## File Changes",
    `- ${PROJ_B}/src/beta_router.py`,
    "",
    "## Recent Messages",
    "### User",
    "work on the beta feature",
    "",
  ].join("\n");
  const candidate: ContinuityCandidate = {
    entry,
    score: 0.9,
    content,
  };
  const hint = buildContinuityHint(candidate, "continue the beta work", PROJ_B);

  expect(hint).toContain("beta feature");
  expect(hint).not.toContain("- Omitted");
});
