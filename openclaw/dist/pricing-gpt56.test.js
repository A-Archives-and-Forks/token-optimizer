"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
const bun_test_1 = require("bun:test");
const pricing_1 = require("./pricing");
const quality_1 = require("./quality");
const BASE_TOKENS = {
    input: 50_000,
    output: 50_000,
    cacheRead: 50_000,
    cacheWrite: 50_000,
};
const LONG_CONTEXT_TOKENS = {
    input: 1_000_000,
    output: 1_000_000,
    cacheRead: 1_000_000,
    cacheWrite: 1_000_000,
};
const CASES = [
    { model: "gpt-5.6-sol", base: 2.0875, longContext: 68.50, input: 5.0, cached: 0.50, cacheWrite: 6.25, output: 30.0 },
    { model: "gpt-5.6-terra", base: 0.8350, longContext: 27.40, input: 2.0, cached: 0.20, cacheWrite: 2.50, output: 12.0 },
    { model: "gpt-5.6-luna", base: 0.0835, longContext: 2.74, input: 0.20, cached: 0.02, cacheWrite: 0.25, output: 1.20 },
];
(0, bun_test_1.test)("GPT-5.6 aliases normalize to the documented canonical IDs", () => {
    (0, bun_test_1.expect)((0, pricing_1.normalizeModelName)("gpt-5.6")).toBe("gpt-5.6-sol");
    (0, bun_test_1.expect)((0, pricing_1.normalizeModelName)("openrouter/openai/gpt-5.6-sol-2026-07-09")).toBe("gpt-5.6-sol");
    (0, bun_test_1.expect)((0, pricing_1.normalizeModelName)("GPT-5.6 Sol Pro")).toBe("gpt-5.6-sol");
    (0, bun_test_1.expect)((0, pricing_1.normalizeModelName)("openai:gpt-5.6-terra-2026-07-09")).toBe("gpt-5.6-terra");
    (0, bun_test_1.expect)((0, pricing_1.normalizeModelName)("gpt-5.6_luna")).toBe("gpt-5.6-luna");
});
(0, bun_test_1.test)("GPT-5.6 pricing applies base and long-context API-equivalent rates", () => {
    (0, pricing_1.resetPricingCache)();
    for (const entry of CASES) {
        const rates = pricing_1.DEFAULT_PRICING[entry.model];
        (0, bun_test_1.expect)(rates.input * 1e6).toBe(entry.input);
        (0, bun_test_1.expect)(rates.cacheRead * 1e6).toBe(entry.cached);
        (0, bun_test_1.expect)(rates.cacheWrite * 1e6).toBe(entry.cacheWrite);
        (0, bun_test_1.expect)(rates.output * 1e6).toBe(entry.output);
        (0, bun_test_1.expect)((0, pricing_1.calculateCost)(BASE_TOKENS, entry.model, "/tmp/token-optimizer-gpt56-no-config")).toBeCloseTo(entry.base);
        (0, bun_test_1.expect)((0, pricing_1.calculateCost)(LONG_CONTEXT_TOKENS, entry.model, "/tmp/token-optimizer-gpt56-no-config")).toBeCloseTo(entry.longContext);
    }
});
(0, bun_test_1.test)("GPT-5.6 feeds OpenClaw savings and context-window helpers", () => {
    (0, bun_test_1.expect)((0, quality_1.freshSessionSavingsUsd)(1_000_000, "GPT-5.6 Sol Pro")).toBeCloseTo(5.0);
    (0, bun_test_1.expect)((0, quality_1.freshSessionSavingsUsd)(1_000_000, "openai:gpt-5.6-terra-2026-07-09")).toBeCloseTo(2.0);
    (0, bun_test_1.expect)((0, quality_1.freshSessionSavingsUsd)(1_000_000, "gpt-5.6_luna")).toBeCloseTo(0.20);
    (0, bun_test_1.expect)((0, quality_1.contextWindowForModel)("gpt-5.6")).toBe(1_050_000);
    (0, bun_test_1.expect)((0, quality_1.contextWindowForModel)("openrouter/openai/gpt-5.6-terra-2026-07-09")).toBe(1_050_000);
});
//# sourceMappingURL=pricing-gpt56.test.js.map