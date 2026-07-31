import { expect, test } from "bun:test";
import type { TokenBreakdown } from "./models";
import { calculateCost, DEFAULT_PRICING, normalizeModelName, resetPricingCache } from "./pricing";
import { contextWindowForModel, freshSessionSavingsUsd } from "./quality";

const BASE_TOKENS: TokenBreakdown = {
  input: 50_000,
  output: 50_000,
  cacheRead: 50_000,
  cacheWrite: 50_000,
};

const LONG_CONTEXT_TOKENS: TokenBreakdown = {
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

test("GPT-5.6 aliases normalize to the documented canonical IDs", () => {
  expect(normalizeModelName("gpt-5.6")).toBe("gpt-5.6-sol");
  expect(normalizeModelName("openrouter/openai/gpt-5.6-sol-2026-07-09")).toBe("gpt-5.6-sol");
  expect(normalizeModelName("GPT-5.6 Sol Pro")).toBe("gpt-5.6-sol");
  expect(normalizeModelName("openai:gpt-5.6-terra-2026-07-09")).toBe("gpt-5.6-terra");
  expect(normalizeModelName("gpt-5.6_luna")).toBe("gpt-5.6-luna");
});

test("GPT-5.6 pricing applies base and long-context API-equivalent rates", () => {
  resetPricingCache();
  for (const entry of CASES) {
    const rates = DEFAULT_PRICING[entry.model];
    expect(rates.input * 1e6).toBe(entry.input);
    expect(rates.cacheRead * 1e6).toBe(entry.cached);
    expect(rates.cacheWrite * 1e6).toBe(entry.cacheWrite);
    expect(rates.output * 1e6).toBe(entry.output);
    expect(calculateCost(BASE_TOKENS, entry.model, "/tmp/token-optimizer-gpt56-no-config")).toBeCloseTo(entry.base);
    expect(calculateCost(LONG_CONTEXT_TOKENS, entry.model, "/tmp/token-optimizer-gpt56-no-config")).toBeCloseTo(entry.longContext);
  }
});

test("GPT-5.6 feeds OpenClaw savings and context-window helpers", () => {
  expect(freshSessionSavingsUsd(1_000_000, "GPT-5.6 Sol Pro")).toBeCloseTo(5.0);
  expect(freshSessionSavingsUsd(1_000_000, "openai:gpt-5.6-terra-2026-07-09")).toBeCloseTo(2.0);
  expect(freshSessionSavingsUsd(1_000_000, "gpt-5.6_luna")).toBeCloseTo(0.20);
  expect(contextWindowForModel("gpt-5.6")).toBe(1_050_000);
  expect(contextWindowForModel("openrouter/openai/gpt-5.6-terra-2026-07-09")).toBe(1_050_000);
});
