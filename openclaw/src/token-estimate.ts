// Shared calibrated token estimator (parity with Python token_estimate.py / U-F).
//
// Why not `/ 4`: real source code averages ~3.3 chars/token for the Claude/GPT
// BPE families, so dividing by 4 undercounts ~15-20%. CJK is ~1 char/token, so
// `/ 4` undercounts it ~3x. One calibrated helper keeps every surface consistent.

export const CODE_CHARS_PER_TOKEN = 3.3;

function isCjk(code: number): boolean {
  return (
    (code >= 0x3040 && code <= 0x30ff) || // Hiragana + Katakana
    (code >= 0x3400 && code <= 0x4dbf) || // CJK Ext A
    (code >= 0x4e00 && code <= 0x9fff) || // CJK Unified
    (code >= 0xac00 && code <= 0xd7a3) || // Hangul syllables
    (code >= 0xf900 && code <= 0xfaff) || // CJK compatibility
    (code >= 0xff00 && code <= 0xffef) || // Half/full-width
    (code >= 0x20000 && code <= 0x2fa1f)  // CJK Ext B-F + supplement
  );
}

// eslint-disable-next-line no-control-regex
const NON_ASCII = /[^\x00-\x7F]/;

/** Estimate tokens for a text string. CJK counted ~1 token/char, rest at 3.3 chars/token. */
export function estimateTokens(text: string): number {
  if (!text) return 0;
  // Fast path: pure ASCII (the vast majority of source) has no CJK.
  if (!NON_ASCII.test(text)) {
    return Math.max(1, Math.ceil(text.length / CODE_CHARS_PER_TOKEN));
  }
  let cjk = 0;
  for (const ch of text) {
    if (isCjk(ch.codePointAt(0) as number)) cjk += 1;
  }
  const other = text.length - cjk;
  return Math.max(1, Math.ceil(other / CODE_CHARS_PER_TOKEN) + cjk);
}

/** Estimate tokens from a byte count (e.g. fs.statSync().size). Assumes ~ASCII. */
export function estimateTokensFromBytes(nBytes: number): number {
  if (!nBytes || nBytes <= 0) return 0;
  return Math.max(1, Math.ceil(nBytes / CODE_CHARS_PER_TOKEN));
}
