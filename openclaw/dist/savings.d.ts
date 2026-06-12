export declare function savingsCategoryLabel(eventType: string): string;
export interface SavingsEventCategory {
    eventType: string;
    label: string;
    count: number;
    tokensSaved: number;
    costSavedUsd: number;
}
export interface SavingsEventsSummary {
    categories: SavingsEventCategory[];
    totalTokensSaved: number;
    totalCostSavedUsd: number;
    totalCount: number;
}
/**
 * Read savings-events.jsonl, group by event_type, and return per-category
 * totals + a grand total. No allowlist: every event_type in the file surfaces.
 * Returns an empty summary (not an error) when the file is missing.
 */
export declare function readSavingsEventsByCategory(openclawDir?: string): SavingsEventsSummary;
export interface SavingsBreakdownItem {
    key: string;
    label: string;
    monthlyUsd: number;
}
export interface RealizedSavings {
    ready: boolean;
    status: string;
    monthlySavingsUsd: number;
    savingsPerSession: number;
    beforeCostPerSession: number;
    afterCostPerSession: number;
    sessionsPerMonth: number;
    beforeMixLabel: string;
    afterMixLabel: string;
    cumulativeSavedUsd: number;
    installDate: string | null;
    breakdown: SavingsBreakdownItem[];
}
/**
 * Compute realized before/after savings. `now` is injectable for testing.
 */
export declare function computeRealizedSavings(openclawDir: string, days?: number, now?: number): RealizedSavings;
//# sourceMappingURL=savings.d.ts.map