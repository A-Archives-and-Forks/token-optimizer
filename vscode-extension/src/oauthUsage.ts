// Opt-in, off-by-default authoritative usage lookup. Uses the user's EXISTING
// Claude subscription OAuth token (the same credential Claude Code itself uses
// for /usage) to read account usage. This is a status read, not a model call:
// zero tokens, no billing.
//
// Data flow of the token: read from ~/.claude/.credentials.json, placed only in
// the Authorization header of a single HTTPS GET to the hardcoded Anthropic host
// (USAGE_HOST below). It is not written to disk, output, or any other host.
//
// Everything here degrades gracefully — missing creds, network error, or an
// unexpected response shape all return null so the caller falls back to the
// sidecar value. The exact endpoint/shape may drift; that's why this is opt-in.
import * as fs from 'fs';
import * as https from 'https';
import { RateLimits } from './types';
import { parseRateWindow } from './rateWindow';

const USAGE_HOST = 'api.anthropic.com';
const USAGE_PATH = '/api/oauth/usage';
const TIMEOUT_MS = 5000;

export function readOauthToken(credentialsPath: string): string | null {
  let raw: string;
  try {
    raw = fs.readFileSync(credentialsPath, 'utf8');
  } catch {
    return null;
  }
  let obj: any;
  try {
    obj = JSON.parse(raw);
  } catch {
    return null;
  }
  // Token has lived under a few keys across versions; probe the known shapes.
  const candidates = [
    obj?.claudeAiOauth?.accessToken,
    obj?.claudeAiOauth?.access_token,
    obj?.oauth?.accessToken,
    obj?.accessToken,
    obj?.access_token,
  ];
  for (const c of candidates) {
    if (typeof c === 'string' && c.length > 0) return c;
  }
  return null;
}

// Map a parsed usage response into RateLimits. Tolerant of nesting variants.
export function parseUsageResponse(body: string, nowMs: number): RateLimits | null {
  let obj: any;
  try {
    obj = JSON.parse(body);
  } catch {
    return null;
  }
  const root = obj?.rate_limits ?? obj;
  const fiveHour = parseRateWindow(root?.five_hour);
  const sevenDay = parseRateWindow(root?.seven_day);
  if (!fiveHour && !sevenDay) return null;
  return { fiveHour, sevenDay, timestamp: nowMs, source: 'oauth' };
}

export function fetchOauthUsage(
  credentialsPath: string,
  nowMs: number
): Promise<RateLimits | null> {
  const token = readOauthToken(credentialsPath);
  if (!token) return Promise.resolve(null);

  return new Promise((resolve) => {
    let settled = false;
    const done = (v: RateLimits | null) => {
      if (settled) return;
      settled = true;
      resolve(v);
    };
    const req = https.request(
      {
        host: USAGE_HOST,
        path: USAGE_PATH,
        method: 'GET',
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: 'application/json',
          'User-Agent': 'token-optimizer-vscode',
        },
        timeout: TIMEOUT_MS,
      },
      (res) => {
        if (!res.statusCode || res.statusCode < 200 || res.statusCode >= 300) {
          res.resume();
          return done(null);
        }
        let data = '';
        res.setEncoding('utf8');
        res.on('data', (c) => {
          data += c;
          // Guard against a runaway body. Settle explicitly: after destroy the
          // 'end' event never fires, so relying on it would hang the promise.
          if (data.length > 1_000_000) {
            res.destroy();
            done(null);
          }
        });
        res.on('end', () => done(parseUsageResponse(data, nowMs)));
      }
    );
    req.on('error', () => done(null));
    req.on('timeout', () => {
      req.destroy();
      done(null);
    });
    req.end();
  });
}
