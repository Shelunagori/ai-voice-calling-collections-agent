// SERVER-ONLY. Imported exclusively by route handlers under app/api/operator/**.
// Holds the backend OPERATOR_TOKEN. It is not NEXT_PUBLIC_, so Next.js never inlines it
// into client bundles (checked by scripts/check-client-bundle.mjs and tests).
//
// Flow (no browser login):
//   browser --same-origin fetch--> Next.js route handler (this module)
//           --Authorization: Bearer OPERATOR_TOKEN--> Railway (explicitly listed routes only)
// Only explicitly listed backend routes are proxied (GET /api/sessions/{uuid}).
// The browser never sends, receives or stores the token; backend headers/bodies are not passed through.

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
