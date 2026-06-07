export declare const CODE_CHARS_PER_TOKEN = 3.3;
/** Estimate tokens for a text string. CJK counted ~1 token/char, rest at 3.3 chars/token. */
export declare function estimateTokens(text: string): number;
/** Estimate tokens from a byte count (e.g. fs.statSync().size). Assumes ~ASCII. */
export declare function estimateTokensFromBytes(nBytes: number): number;
//# sourceMappingURL=token-estimate.d.ts.map