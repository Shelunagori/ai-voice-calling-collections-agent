// SERVER-ONLY. Imported exclusively by route handlers under app/api/operator/**.
// Holds the backend OPERATOR_TOKEN. It is not NEXT_PUBLIC_, so Next.js never inlines it
// into client bundles (checked by scripts/check-client-bundle.mjs and tests).
//
// Flow (no browser login):
//   browser --same-origin fetch--> Next.js route handler (this module)
//           --Authorization: Bearer OPERATOR_TOKEN--> Railway (explicitly listed routes only)
// Only two backend routes are ever proxied: GET /api/sessions/{uuid} and POST /api/operator/calls.
// The browser never sends, receives or stores the token; backend headers/bodies are not passed through.

import { E164 } from "../phone";

export { E164 };

export type ServerConfig = { token: string; backend: string };
type Env = Record<string, string | undefined>;

export function serverConfig(env: Env = process.env): ServerConfig {
  return {
    token: env.OPERATOR_TOKEN ?? "",
    backend: (env.BACKEND_API_BASE_URL || env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000").replace(/\/+$/, ""),
  };
}

export type ProxyResult = { status: number; body: Record<string, unknown> };
type FetchLike = (url: string, init: RequestInit) => Promise<Response>;

function fail(status: number, code: string, detail: string, extra: Record<string, unknown> = {}): ProxyResult {
  return { status, body: { ok: false, code, detail, ...extra } };
}

async function backendFetch(
  url: string,
  init: RequestInit,
  fetchImpl: FetchLike | undefined,
  timeoutMs: number,
): Promise<Response | ProxyResult> {
  try {
    return await (fetchImpl ?? (fetch as FetchLike))(url, {
      ...init,
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch (e) {
    const name = (e as { name?: string })?.name;
    if (name === "TimeoutError" || name === "AbortError") return fail(504, "timeout", "The backend did not answer in time.");
    return fail(502, "backend_unreachable", "The backend could not be reached.");
  }
}

async function detailOf(r: Response): Promise<string> {
  try {
    const b = (await r.json()) as { detail?: unknown };
    return typeof b.detail === "string" ? b.detail : "";
  } catch {
    return "";
  }
}

// ------------------------------------------------------------------ session detail (read)

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Session/audit detail. The server always attaches the token when configured, so phone
 *  sessions open without any browser login; browser-demo sessions never needed it. */
export async function proxySessionDetail(
  id: string,
  opts: { config?: ServerConfig; fetchImpl?: FetchLike; timeoutMs?: number } = {},
): Promise<ProxyResult> {
  const cfg = opts.config ?? serverConfig();
  if (!UUID.test(id)) return fail(400, "invalid_session_id", "Session id must be a UUID.");
  const headers: Record<string, string> = { Accept: "application/json" };
  if (cfg.token) headers.Authorization = `Bearer ${cfg.token}`;
  const r = await backendFetch(`${cfg.backend}/api/sessions/${id}`, { headers }, opts.fetchImpl, opts.timeoutMs ?? 10_000);
  if (!(r instanceof Response)) return r;
  if (r.status === 404) return fail(404, "not_found", "Session not found.");
  if (r.status === 401 || r.status === 403) {
    return cfg.token
      ? fail(502, "operator_token_rejected", "The backend rejected the server's operator token (OPERATOR_TOKEN differs between Vercel and Railway?).")
      : fail(503, "operator_not_configured", "Phone-session detail needs OPERATOR_TOKEN on the frontend server.");
  }
  if (!r.ok) return fail(502, "backend_error", `The backend returned HTTP ${r.status}.`);
  let data: unknown;
  try {
    data = await r.json();
  } catch {
    return fail(502, "bad_backend_response", "The backend returned malformed JSON.");
  }
  if (!data || typeof data !== "object" || !("session" in data)) {
    return fail(502, "bad_backend_response", "The backend response has an unexpected shape.");
  }
  return { status: 200, body: { ok: true, operator: !!cfg.token, detail: data } };
}

// ------------------------------------------------------------------ start call (write)

const SCENARIO = /^[A-Za-z]$/;
const LANGUAGES = new Set(["en", "ja"]);

export type CallInput = { to: string; scenario: string; language: "en" | "ja" };

export function validateCallInput(raw: unknown): CallInput | string {
  if (!raw || typeof raw !== "object") return "Request body must be a JSON object.";
  const b = raw as Record<string, unknown>;
  const to = typeof b.to === "string" ? b.to.replace(/[\s()-]/g, "") : "";
  if (!E164.test(to)) return "Destination must be an E.164 number, e.g. +81 90 1234 5678 → +819012345678.";
  if (typeof b.scenario !== "string" || !SCENARIO.test(b.scenario)) return "Scenario must be a single scenario key such as A.";
  if (typeof b.language !== "string" || !LANGUAGES.has(b.language)) return "Language must be en or ja.";
  return { to, scenario: b.scenario.toUpperCase(), language: b.language as "en" | "ja" };
}

/** Lightweight abuse guard for Start Call (per server instance; the backend's own hourly
 *  limit, allowlist and contact policy remain authoritative). */
export class CallGuard {
  private hits = new Map<string, number[]>();
  private lastByDestination = new Map<string, number>();
  constructor(
    private maxPerWindow = 3,
    private windowMs = 10 * 60_000,
    private duplicateMs = 30_000,
  ) {}

  check(client: string, to: string, nowMs: number = Date.now()): "ok" | "rate_limited" | "duplicate_request" {
    const last = this.lastByDestination.get(to);
    if (last !== undefined && nowMs - last < this.duplicateMs) return "duplicate_request";
    const recent = (this.hits.get(client) ?? []).filter((t) => nowMs - t < this.windowMs);
    if (recent.length >= this.maxPerWindow) {
      this.hits.set(client, recent);
      return "rate_limited";
    }
    recent.push(nowMs);
    this.hits.set(client, recent);
    this.lastByDestination.set(to, nowMs);
    if (this.hits.size > 5000) this.hits.clear();
    if (this.lastByDestination.size > 5000) this.lastByDestination.clear();
    return "ok";
  }
}

export const callGuard = new CallGuard();

export type PolicyDecisionOut = { rule: string; decision: string; reason: string; details?: Record<string, unknown> };

function sanitizeDecisions(v: unknown): PolicyDecisionOut[] {
  if (!Array.isArray(v)) return [];
  return v
    .filter((d): d is Record<string, unknown> => !!d && typeof d === "object")
    .map((d) => ({
      rule: String(d.rule ?? ""),
      decision: String(d.decision ?? ""),
      reason: String(d.reason ?? ""),
      ...(d.details && typeof d.details === "object" ? { details: d.details as Record<string, unknown> } : {}),
    }));
}

/** POST /api/operator/calls on the backend with the server-side token. Only `to`,
 *  `scenario` and `language` are forwarded; nothing from the browser's headers is. */
export async function proxyStartCall(
  raw: unknown,
  opts: { config?: ServerConfig; fetchImpl?: FetchLike; timeoutMs?: number; client?: string; guard?: CallGuard; nowMs?: number } = {},
): Promise<ProxyResult> {
  const cfg = opts.config ?? serverConfig();
  const input = validateCallInput(raw);
  if (typeof input === "string") return fail(400, "invalid_request", input);
  if (!cfg.token) return fail(503, "operator_not_configured", "Start Call needs OPERATOR_TOKEN on the frontend server.");
  const verdict = (opts.guard ?? callGuard).check(opts.client ?? "unknown", input.to, opts.nowMs);
  if (verdict === "duplicate_request") return fail(409, "duplicate_request", "A call to this number was just started; wait 30 seconds.");
  if (verdict === "rate_limited") return fail(429, "rate_limited", "Too many calls started from this browser; try again later.");

  const r = await backendFetch(
    `${cfg.backend}/api/operator/calls`,
    {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json", Authorization: `Bearer ${cfg.token}` },
      body: JSON.stringify(input),
    },
    opts.fetchImpl,
    opts.timeoutMs ?? 15_000,
  );
  if (!(r instanceof Response)) return r;
  if (!r.ok) {
    const detail = await detailOf(r);
    switch (r.status) {
      case 401:
        return fail(502, "operator_token_rejected", "The backend rejected the server's operator token (OPERATOR_TOKEN differs between Vercel and Railway?).");
      case 403:
        if (/DEMO_CALL_ALLOWED_NUMBERS/.test(detail)) return fail(403, "not_allowlisted", "This number is not on the demo allowlist, so it cannot be dialed.");
        return fail(503, "operator_not_configured", "Operator endpoints are disabled on the backend (OPERATOR_TOKEN not set on Railway).");
      case 404:
        return fail(400, "unknown_scenario", "The backend does not know this scenario.");
      case 409:
        return fail(409, "telephony_disabled", "Telephony is not enabled on the backend.");
      case 422:
        return fail(400, "invalid_request", "The backend rejected the request fields.");
      case 429:
        return fail(429, "rate_limited", "The backend's hourly call limit was reached.");
      case 502:
        return fail(502, "provider_error", /telephony provider error: [a-z_]+/.exec(detail)?.[0] ?? "The telephony provider failed to place the call.");
      default:
        return fail(502, "backend_error", `The backend returned HTTP ${r.status}.`);
    }
  }
  let b: Record<string, unknown>;
  try {
    b = (await r.json()) as Record<string, unknown>;
  } catch {
    return fail(502, "bad_backend_response", "The backend returned malformed JSON.");
  }
  const decisions = sanitizeDecisions(b?.decisions);
  if (b?.status === "blocked_by_policy") return { status: 200, body: { ok: true, outcome: "blocked_by_policy", decisions } };
  if (b?.status === "dialing" && typeof b.session_id === "string" && UUID.test(b.session_id)) {
    return {
      status: 200,
      body: { ok: true, outcome: "dialing", session_id: b.session_id, call_id: typeof b.call_id === "string" ? b.call_id : null, decisions },
    };
  }
  return fail(502, "bad_backend_response", "The backend response has an unexpected shape.");
}

/** Same-origin JSON only: blocks cross-site form posts and makes any cross-origin script
 *  need a CORS preflight, which this route never grants. */
export function sameOriginJson(headers: Headers, selfOrigin: string): boolean {
  const origin = headers.get("origin");
  const ct = headers.get("content-type") ?? "";
  return origin === selfOrigin && ct.toLowerCase().startsWith("application/json");
}
