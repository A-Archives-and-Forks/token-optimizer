"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
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
const bun_test_1 = require("bun:test");
const continuity_js_1 = require("./continuity.js");
// ---------------------------------------------------------------------------
// Shared parity fixture — MUST stay byte-identical to the Python + OpenCode
// scoping tests. (item_text, keep_tokens, expected_keep)
// ---------------------------------------------------------------------------
const PARITY_FIXTURE = [
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
(0, bun_test_1.test)("keepRecoveredItem matches the shared parity fixture exactly", () => {
    for (const [itemText, keepTokens, expected] of PARITY_FIXTURE) {
        const got = (0, continuity_js_1.keepRecoveredItem)(itemText, keepTokens);
        (0, bun_test_1.expect)(got).toBe(expected);
    }
});
(0, bun_test_1.test)("keepRecoveredItem is purely set-overlap, no float threshold", () => {
    (0, bun_test_1.expect)((0, continuity_js_1.keepRecoveredItem)("alpha beta gamma delta epsilon zeta", new Set())).toBe(false);
    (0, bun_test_1.expect)((0, continuity_js_1.keepRecoveredItem)("alpha beta", new Set())).toBe(true);
});
// ---------------------------------------------------------------------------
// buildResumeLeanBlock — mixed A/B checkpoint queried from B
// ---------------------------------------------------------------------------
const PROJ_B = "/home/u/beta";
const PROJ_A = "/home/u/gamma";
function makeEntry() {
    return {
        path: "/tmp/checkpoints/test-session.md",
        sessionDirName: "testsession",
        trigger: "auto",
        createdAt: Date.now(),
    };
}
function mixedAbCheckpointMd() {
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
(0, bun_test_1.test)("buildResumeLeanBlock drops A-only decisions and emits one disclosure", () => {
    const entry = makeEntry();
    const content = mixedAbCheckpointMd();
    const block = (0, continuity_js_1.buildResumeLeanBlock)(entry, content, 3500, "continue the beta work", PROJ_B);
    // B-overlapping decisions kept:
    (0, bun_test_1.expect)(block).toContain("beta feature behind a feature flag");
    (0, bun_test_1.expect)(block).toContain("beta_router");
    // A-only DECISION dropped:
    (0, bun_test_1.expect)(block).not.toContain("gamma delta epsilon");
    // Exactly one disclosure line. File paths are single-token -> kept, so
    // F=0 is elided. Only the 1 dropped decision is reported:
    const disclosureCount = (block.match(/- Omitted \(same session, different project\):/g) || []).length;
    (0, bun_test_1.expect)(disclosureCount).toBe(1);
    (0, bun_test_1.expect)(block).toContain("- Omitted (same session, different project): 1 decision(s)");
    (0, bun_test_1.expect)(block).not.toContain("file(s)");
});
(0, bun_test_1.test)("buildResumeLeanBlock single-project checkpoint emits NO disclosure", () => {
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
    const block = (0, continuity_js_1.buildResumeLeanBlock)(entry, content, 3500, "continue the beta work", PROJ_B);
    (0, bun_test_1.expect)(block).toContain("beta feature");
    (0, bun_test_1.expect)(block).toContain("beta_router");
    (0, bun_test_1.expect)(block).not.toContain("- Omitted");
});
(0, bun_test_1.test)("buildResumeLeanBlock no filter when cwd absent (backward compat)", () => {
    const entry = makeEntry();
    const content = mixedAbCheckpointMd();
    // Legacy call: no promptText/cwd -> unfiltered, A-only items survive, no
    // disclosure line.
    const block = (0, continuity_js_1.buildResumeLeanBlock)(entry, content);
    (0, bun_test_1.expect)(block).toContain("gamma delta epsilon");
    (0, bun_test_1.expect)(block).not.toContain("- Omitted");
});
// ---------------------------------------------------------------------------
// buildContinuityHint — filtered rebuild replaces raw 800-char excerpt
// ---------------------------------------------------------------------------
(0, bun_test_1.test)("buildContinuityHint drops A-only decisions and emits disclosure", () => {
    const entry = makeEntry();
    const content = mixedAbCheckpointMd();
    const candidate = {
        entry,
        score: 0.9,
        content,
    };
    const hint = (0, continuity_js_1.buildContinuityHint)(candidate, "continue the beta work", PROJ_B);
    // B-overlapping decision kept in the filtered rebuild:
    (0, bun_test_1.expect)(hint).toContain("beta feature behind a feature flag");
    // A-only DECISION dropped:
    (0, bun_test_1.expect)(hint).not.toContain("gamma delta epsilon");
    // Exactly one disclosure line:
    const disclosureCount = (hint.match(/- Omitted \(same session, different project\):/g) || []).length;
    (0, bun_test_1.expect)(disclosureCount).toBe(1);
    (0, bun_test_1.expect)(hint).toContain("- Omitted (same session, different project): 1 decision(s)");
});
(0, bun_test_1.test)("buildContinuityHint single-project checkpoint emits NO disclosure", () => {
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
    const candidate = {
        entry,
        score: 0.9,
        content,
    };
    const hint = (0, continuity_js_1.buildContinuityHint)(candidate, "continue the beta work", PROJ_B);
    (0, bun_test_1.expect)(hint).toContain("beta feature");
    (0, bun_test_1.expect)(hint).not.toContain("- Omitted");
});
//# sourceMappingURL=continuity-scoping.test.js.map