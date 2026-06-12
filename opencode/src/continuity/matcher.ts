const STOP_WORDS = new Set([
  "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
  "have", "has", "had", "do", "does", "did", "will", "would", "shall",
  "should", "may", "might", "must", "can", "could", "to", "of", "in",
  "for", "on", "with", "at", "by", "from", "as", "into", "about",
  "like", "through", "after", "over", "between", "out", "up", "down",
  "that", "this", "it", "its", "my", "your", "his", "her", "we", "they",
  "them", "what", "which", "who", "when", "where", "how", "not", "no",
  "but", "or", "and", "if", "then", "so", "than", "too", "very", "just",
  "i", "me", "let", "us",
]);

function tokenize(text: string): string[] {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9\s_.-]/g, " ")
    .split(/\s+/)
    .filter(Boolean);
}

function extractKeywords(text: string): string[] {
  return tokenize(text).filter((w) => w.length > 2 && !STOP_WORDS.has(w));
}

export function scoreRelevance(userPrompt: string, checkpointContent: string): number {
  const promptKeywords = extractKeywords(userPrompt);
  if (promptKeywords.length === 0) return 0;

  // Word-level membership, NOT substring: a keyword "test" must not match
  // "latest"/"protest", and "port" must not match "report"/"support". Substring
  // matching inflated scores and injected unrelated prior sessions.
  const contentTokens = new Set(tokenize(checkpointContent));
  let matches = 0;
  for (const kw of promptKeywords) {
    if (contentTokens.has(kw)) matches++;
  }

  return matches / promptKeywords.length;
}

export interface CheckpointMatch {
  content: string;
  score: number;
  sessionId: string;
  mode: string;
  /** Byte length of the full checkpoint content before truncation.
   *  Used as the floor input to the checkpoint_restore savings estimate. */
  rawBytes: number;
}

function safeSlice(str: string, maxChars: number): string {
  if (str.length <= maxChars) return str;
  let end = maxChars;
  const code = str.charCodeAt(end - 1);
  if (code >= 0xd800 && code <= 0xdbff) end--;
  return str.slice(0, end) + "\n[... truncated]";
}

/**
 * Make raw checkpoint body safe to inject after a [RECOVERED DATA ...] sentinel.
 *
 * The body is prior-conversation text and may be attacker-influenced (a pasted
 * file, fetched web content, a crafted message). Strip C0 control chars and
 * DEFANG any forged RECOVERED-DATA sentinel / role-prefix so the body cannot
 * "close" the data fence and smuggle the following lines in as live instructions.
 *
 * Mirrors Python's _neutralize_recovered_body() in measure.py exactly:
 *   - Strip C0 control chars except TAB and LF.
 *   - Defang forged "[RECOVERED..." sentinels: leading bracket becomes paren.
 *   - Bracket role-prefix lines (system:, user:, assistant:, etc.) so they
 *     cannot read as a new turn or system instruction.
 */
export function neutralizeRecoveredBody(text: string): string {
  if (!text) return "";
  // Strip C0 control chars except tab (\x09) and newline (\x0a).
  text = text.replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, " ");
  // Defang forged open/close sentinels: "[RECOVERED...", "[/RECOVERED...", etc.
  text = text.replace(/\[(\s*\/?\s*RECOVERED\b)/gi, "($1");
  // Defang role-prefix lines that could read as a new turn / system instruction.
  text = text.replace(
    /^(\s*)(system|assistant|user|human|developer|tool|instructions?)(\s*:)/gim,
    "$1[$2]$3",
  );
  return text;
}

export function findBestCheckpoint(
  userPrompt: string,
  checkpoints: Array<{ session_id: string; content: string; mode: string; created_at: number }>,
  threshold: number,
  maxChars: number = 2000,
): CheckpointMatch | null {
  let best: CheckpointMatch | null = null;
  let bestScore = 0;

  for (const cp of checkpoints) {
    const score = scoreRelevance(userPrompt, cp.content);
    if (score > bestScore && score >= threshold) {
      bestScore = score;
      // Neutralize forged sentinels / control chars before injecting.
      // The raw checkpoint body is replayed prior-conversation text and is
      // attacker-influenceable; it must not be able to break the data fence.
      const safeContent = neutralizeRecoveredBody(safeSlice(cp.content, maxChars));
      best = {
        content: safeContent,
        score,
        sessionId: cp.session_id,
        mode: cp.mode,
        // Preserve the full byte length BEFORE truncation so the caller can
        // compute a checkpoint_restore floor estimate from the real checkpoint
        // size rather than the (possibly truncated) injected excerpt.
        rawBytes: Buffer.byteLength(cp.content, "utf8"),
      };
    }
  }

  return best;
}
