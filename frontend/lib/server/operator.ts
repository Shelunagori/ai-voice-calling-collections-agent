// SERVER-ONLY. Imported exclusively by route handlers under app/api/operator/**.
// Holds the backend OPERATOR_TOKEN and the console password; neither is NEXT_PUBLIC_,
// so Next.js never inlines them into client bundles (checked by scripts/check-client-bundle.mjs
// and tests/operator-proxy.test.ts).
//
// Flow: browser --(HttpOnly, SameSite=Strict cookie)--> Next.js route handler
//       --(Authorization: Bearer OPERATOR_TOKEN)--> Railway /api/sessions/:id
// The browser only ever holds a signed, expiring session cookie it cannot read.
import { createHash, createHmac, timingSafeEqual } from "node:crypto";

export const OPERATOR_COOKIE = "vca_operator";
export const SESSION_TTL_S = 8 * 60 * 60;

type Env = Record<string, string | undefined>;

export type ServerConfig = { token: string; password: string; backend: string };

export function serverConfig(env: Env = process.env): ServerConfig {
  return {
    token: env.OPERATOR_TOKEN ?? "",
    password: env.OPERATOR_CONSOLE_PASSWORD ?? "",
    backend: (env.BACKEND_API_BASE_URL || env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000").replace(/\/+$/, ""),
  };
}

export function consoleConfigured(c: ServerConfig): boolean {
  return c.token.length > 0 && c.password.length > 0;
}

function signingKey(token: string): Buffer {
  // Derived from the operator token: rotating OPERATOR_TOKEN invalidates every console session.
  return createHmac("sha256", token).update("vca-operator-console-session-v1").digest();
}

function mac(token: string, payload: string): string {
  return createHmac("sha256", signingKey(token)).update(payload).digest("base64url");
}

export function issueSession(token: string, nowMs: number = Date.now()): string {
  const exp = Math.floor(nowMs / 1000) + SESSION_TTL_S;
  return `v1.${exp}.${mac(token, `v1.${exp}`)}`;
}

export function verifySession(value: string | undefined, token: string, nowMs: number = Date.now()): boolean {
  if (!value || !token) return false;
  const m = /^v1\.(\d{1,12})\.([A-Za-z0-9_-]{43})$/.exec(value);
  if (!m) return false;
  if (Number(m[1]) * 1000 <= nowMs) return false;
  const want = Buffer.from(mac(token, `v1.${m[1]}`));
  const got = Buffer.from(m[2]);
  return want.length === got.length && timingSafeEqual(want, got);
}

/** Constant-time comparison of equal-length digests (lengths of the inputs are not leaked). */
export function passwordMatches(given: unknown, expected: string): boolean {
  if (typeof given !== "string" || !expected) return false;
  const a = createHash("sha256").update(given).digest();
  const b = createHash("sha256").update(expected).digest();
  return timingSafeEqual(a, b);
}

// ------------------------------------------------------------------ session-detail proxy

export type ProxyResult = { status: number; body: Record<string, unknown> };

export type ProxyErrorCode =
  | "invalid_session_id"
  | "not_found"
  | "operator_sign_in_required"
  | "operator_not_configured"
  | "operator_token_rejected"
  | "backend_error"
  | "backend_unreachable"
  | "bad_backend_response";

function fail(status: number, code: ProxyErrorCode, detail: string): ProxyResult {
  return { status, body: { ok: false, code, detail } };
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

type FetchLike = (url: string, init: RequestInit) => Promise<Response>;

/**
 * Fetch one session's audit detail from the backend.
 * - Signed-in operator: the server adds `Authorization: Bearer OPERATOR_TOKEN`.
 * - Otherwise: the request is forwarded without credentials; the backend still serves
 *   browser-demo sessions and refuses phone sessions (-> operator_sign_in_required).
 * Backend error bodies and headers are never passed through.
 */
export async function proxySessionDetail(
  id: string,
  opts: { cookie?: string; config?: ServerConfig; fetchImpl?: FetchLike; nowMs?: number; timeoutMs?: number } = {},
): Promise<ProxyResult> {
  const cfg = opts.config ?? serverConfig();
  if (!UUID.test(id)) return fail(400, "invalid_session_id", "Session id must be a UUID.");
  const signedIn = consoleConfigured(cfg) && verifySession(opts.cookie, cfg.token, opts.nowMs);
  const headers: Record<string, string> = { Accept: "application/json" };
  if (signedIn) headers.Authorization = `Bearer ${cfg.token}`;
  const doFetch = opts.fetchImpl ?? (fetch as FetchLike);
  let r: Response;
  try {
    r = await doFetch(`${cfg.backend}/api/sessions/${id}`, {
      headers,
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.timeout(opts.timeoutMs ?? 10_000),
    });
  } catch {
    return fail(504, "backend_unreachable", "The backend could not be reached.");
  }
  if (r.status === 404) return fail(404, "not_found", "Session not found.");
  if (r.status === 401 || r.status === 403) {
    if (signedIn) return fail(502, "operator_token_rejected", "The backend rejected the server's operator token (OPERATOR_TOKEN differs between Vercel and Railway?).");
    if (!consoleConfigured(cfg)) return fail(503, "operator_not_configured", "Phone sessions need operator sign-in, which is not configured on this deployment.");
    return fail(401, "operator_sign_in_required", "Phone-call audit trails require operator sign-in.");
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
  return { status: 200, body: { ok: true, operator: signedIn, detail: data } };
}

// Best-effort login throttle (per server instance).
const attempts = new Map<string, { n: number; reset: number }>();
export function loginAllowed(key: string, nowMs: number = Date.now(), max = 10, windowMs = 60_000): boolean {
  const a = attempts.get(key);
  if (!a || a.reset <= nowMs) {
    attempts.set(key, { n: 1, reset: nowMs + windowMs });
    if (attempts.size > 5000) attempts.clear();
    return true;
  }
  a.n += 1;
  return a.n <= max;
}
