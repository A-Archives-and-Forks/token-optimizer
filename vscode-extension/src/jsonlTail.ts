// Fallback context-fill reader for pure panel mode (no terminal => no live-fill.json).
// Tails the transcript JSONL by byte offset so we never re-read the whole file,
// and derives fill % from the last assistant `usage` object — the same method
// ccusage uses. Also surfaces the model id, which isn't in any sidecar.
import * as fs from 'fs';

export interface TailResult {
  tokens: number | null; // live context footprint from the last assistant turn
  model: string | null;
}

// Context window sizes by model family. This is the FALLBACK only — when the
// quality-cache carries an authoritative `model_context_window`, cacheReader uses
// that instead (measure.py resolves it with full plan/config awareness).
//
// Mirrors measure.py `detect_context_window` so the fallback matches the engine:
//   1. CLAUDE_CODE_DISABLE_1M_CONTEXT=1  -> 200k (explicit opt-out)
//   2. numeric TOKEN_OPTIMIZER_CONTEXT_SIZE -> that value
//   3. Haiku -> 200k; any other (or unknown) Claude model -> 1M GA (since Mar 2026)
// The JSONL `message.model` is the BARE id ("claude-opus-4-8", no "[1m]" suffix),
// so the old "only [1m] => 1M" rule divided every 1M session by 200k and inflated
// fill ~5x — the VS Code panel bug. Defaulting non-Haiku to 1M fixes it.
//
// NOTE: the env checks only take effect when the extension host inherited the
// shell environment (e.g. VS Code launched from a terminal). They are a
// best-effort mirror; the authoritative window is `q.model_context_window` from
// the quality cache (see cacheReader), which this only backstops when absent.
const HAIKU_WINDOW = 200_000;
const MILLION_WINDOW = 1_000_000;

export function windowForModel(model: string | null): number {
  if (process.env.CLAUDE_CODE_DISABLE_1M_CONTEXT === '1') return HAIKU_WINDOW;
  // Number (not parseInt): parseInt('1e6')===1 and parseInt('200000abc')===200000
  // would both pass the >0 guard and silently set a wrong window. Number rejects
  // trailing junk (NaN) and reads scientific notation correctly.
  const override = Number((process.env.TOKEN_OPTIMIZER_CONTEXT_SIZE || '').trim());
  if (Number.isFinite(override) && override > 0) return override;
  if (!model) return MILLION_WINDOW; // unknown model: match measure.py's 1M default
  if (model.toLowerCase().includes('haiku')) return HAIKU_WINDOW;
  return MILLION_WINDOW;
}

export class JsonlTailer {
  private offset = 0;
  private lastResult: TailResult = { tokens: null, model: null };

  constructor(private filePath: string) {}

  // Reset offset when the path changes (session switch) so we re-scan the new file.
  setPath(filePath: string): void {
    if (filePath !== this.filePath) {
      this.filePath = filePath;
      this.offset = 0;
      this.lastResult = { tokens: null, model: null };
    }
  }

  read(): TailResult {
    let size: number;
    try {
      size = fs.statSync(this.filePath).size;
    } catch {
      return this.lastResult;
    }
    // File truncated/rotated — start over.
    if (size < this.offset) this.offset = 0;
    if (size === this.offset) return this.lastResult;

    let chunk = '';
    try {
      const fd = fs.openSync(this.filePath, 'r');
      try {
        const len = size - this.offset;
        const buf = Buffer.allocUnsafe(len);
        const bytesRead = fs.readSync(fd, buf, 0, len, this.offset);
        // Only consume up to the last newline. A newline byte (0x0A) never
        // appears inside a multi-byte UTF-8 sequence, so cutting there avoids
        // decoding a split codepoint (U+FFFD) AND avoids skipping bytes of an
        // incomplete trailing line written between our stat and read.
        const lastNl = buf.lastIndexOf(0x0a, bytesRead - 1);
        if (lastNl < 0) {
          // No complete line yet; leave offset put and wait for more bytes.
          return this.lastResult;
        }
        chunk = buf.toString('utf8', 0, lastNl + 1);
        this.offset += lastNl + 1;
      } finally {
        fs.closeSync(fd);
      }
    } catch {
      return this.lastResult;
    }

    const parsed = parseLatestUsage(chunk);
    if (parsed) {
      // Return the raw token count and let the caller divide by the authoritative
      // window size (from the quality-cache) — the model string alone can't tell
      // us whether this is a 200k or 1M-context session.
      this.lastResult = {
        tokens: parsed.tokens,
        model: parsed.model ?? this.lastResult.model,
      };
    }
    return this.lastResult;
  }
}

interface LatestUsage {
  tokens: number;
  model: string | null;
}

// Scan newline-delimited JSON for the last assistant turn carrying a usage
// object, summing input + cache tokens (the live context footprint).
export function parseLatestUsage(text: string): LatestUsage | null {
  const lines = text.split('\n');
  for (let i = lines.length - 1; i >= 0; i--) {
    const line = lines[i].trim();
    if (!line) continue;
    let obj: any;
    try {
      obj = JSON.parse(line);
    } catch {
      continue;
    }
    const message = obj?.message ?? obj;
    const usage = message?.usage;
    if (!usage) continue;
    const tokens =
      (usage.input_tokens || 0) +
      (usage.cache_creation_input_tokens || 0) +
      (usage.cache_read_input_tokens || 0);
    if (tokens <= 0) continue;
    const model =
      (typeof message?.model === 'string' && message.model) ||
      (typeof obj?.model === 'string' && obj.model) ||
      null;
    return { tokens, model };
  }
  return null;
}
