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