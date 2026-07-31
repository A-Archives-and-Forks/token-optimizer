import { expect, test } from "bun:test";
import { DEFAULT_PRICING, normalizeModelName, price, price_cw } from "./pricing.js";
import { modelInputRatePer1M } from "./nudges/fresh-session-nudge.js";
import { contextWindowForModel } from "./util/context-window.js";

const CASES = [
  { model: "gpt-5.6-sol", input: 5.0, cached: 0.50, cacheWrite: 6.25, output: 30.0 },
  { model: "gpt-5.6-terra", input: 2.0, cached: 0.20, cacheWrite: 2.50, output: 12.0 },
  { model: "gpt-5.6-luna", input: 0.20, cached: 0.02, cacheWrite: 0.25, output: 1.20 },
];

test("GPT-5.6 aliases and Sol Pro display names normalize to canonical pricing keys", () => {
  expect(normalizeModelName("gpt-5.6")).toBe("gpt-5.6-sol");
  expect(normalizeModelName("openrouter/openai/gpt-5.6-sol-2026-07-09")).toBe("gpt-5.6-sol");
  expect(normalizeModelName("GPT-5.6 Sol Pro")).toBe("gpt-5.6-sol");
  expect(normalizeModelName("openai:gpt-5.6-terra-2026-07-09")).toBe("gpt-5.6-terra");
  expect(normalizeModelName("gpt-5.6_luna")).toBe("gpt-5.6-luna");
  expect(normalizeModelName("gpt-5.5-pro")).toBe("gpt-5.5-pro");
});

test("GPT-5.6 base pricing covers fresh, cached, cache-write, and output tokens", () => {
  for (const { model, input, cached, cacheWrite, output } of CASES) {
    const rates = DEFAULT_PRICING[model];
    expect(rates.input * 1e6).toBe(input);
    expect(rates.cacheRead * 1e6).toBe(cached);
    expect(rates.cacheWrite * 1e6).toBe(cacheWrite);
    expect(rates.output * 1e6).toBe(output);
    expect(price(1_000_000, 1_000_000, 1_000_000, { [model]: 1 })).toBeCloseTo(input + cached + output);
    expect(price_cw(1_000_000, { [model]: 1 })).toBeCloseTo(cacheWrite);
  }
});

test("GPT-5.6 pricing feeds hook savings and context-window helpers", () => {
  expect(modelInputRatePer1M("GPT-5.6 Sol Pro")).toBe(5.0);
  expect(modelInputRatePer1M("openai:gpt-5.6-terra-2026-07-09")).toBe(2.0);
  expect(modelInputRatePer1M("gpt-5.6_luna")).toBe(0.20);
  expect(contextWindowForModel("gpt-5.6")).toBe(1_050_000);
  expect(contextWindowForModel("openrouter/openai/gpt-5.6-terra-2026-07-09")).toBe(1_050_000);
});
