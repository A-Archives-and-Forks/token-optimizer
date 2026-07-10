import { TokenBreakdown } from "./models";
export type PricingTier = "anthropic" | "vertex-global" | "vertex-regional" | "bedrock";
/** Pricing tier labels for display. */
export declare const PRICING_TIER_LABELS: Record<PricingTier, string>;
/**
 * Get the cost multiplier for a pricing tier.
 * Only vertex-regional charges differently (10% surcharge on Claude models).
 * All other tiers use base Anthropic rates.
 */
export declare function tierMultiplier(tier: PricingTier, model: string): number;
/** Load the user's selected pricing tier from config. Defaults to "anthropic". */
export declare function loadPricingTier(openclawDir?: string): PricingTier;
export interface ModelPricing {
    input: number;
    output: number;
    cacheRead: number;
    /** 5-minute cache-write rate (1.25x input). Use for 5m-TTL writes or when TTL is unknown. */
    cacheWrite: number;
    /** 1-hour cache-write rate (2x input). Only set for Claude models that support the 1h tier. */
    cacheWrite1h?: number;
}
export interface CacheWriteSplit {
    cacheWrite1hTokens?: number;
    cacheWrite5mTokens?: number;
}
/** Default pricing (USD per token). Verified May 30, 2026. */
export declare const DEFAULT_PRICING: Record<string, ModelPricing>;
/** Swap the sonnet card to the introductory rate while it is in effect (idempotent). Returns
 * true when introductory pricing is active. `asOf` (epoch ms) overrides both env and clock. */
export declare function applySonnetIntroPricing(asOf?: number): boolean;
/** Get pricing with user overrides merged on top of defaults. */
export declare function getPricing(openclawDir?: string): Record<string, ModelPricing>;
/** Reset cached pricing (for testing or config reload). */
export declare function resetPricingCache(): void;
/**
 * Normalize a model ID into a pricing key.
 * Handles provider prefixes (anthropic/claude-sonnet-4-6 -> sonnet)
 * and version suffixes (gpt-5.2-2026-03 -> gpt-5.2).
 */
export declare function normalizeModelName(modelId: string): string | null;
/**
 * Estimate cost delta if a different model was used.
 * Returns savings in USD and percentage.
 */
export declare function simulateModelSwitch(tokens: TokenBreakdown, currentModel: string, targetModel: string, openclawDir?: string): {
    currentCost: number;
    targetCost: number;
    savingsUsd: number;
    savingsPct: number;
};
/** Calculate USD cost. Uses user config pricing if available, then defaults.
 *
 * For Claude models pass cacheWriteSplit to apply
 * the correct per-TTL-tier rate (1h = 2x input; 5m = 1.25x input). When
 * the split is unavailable, or when a remainder is unsplit, those tokens use
 * the 5m rate.
 */
export declare function calculateCost(tokens: TokenBreakdown, model: string, openclawDir?: string, cacheWriteSplit?: CacheWriteSplit): number;
//# sourceMappingURL=pricing.d.ts.map