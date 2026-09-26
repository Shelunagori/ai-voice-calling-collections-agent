// Client helper for Start Call. Talks only to this app's /api/operator/calls route; the
// Next.js server attaches OPERATOR_TOKEN. Nothing here is persisted in the browser.
import type { DetailResult } from "./session-detail";

export type PolicyDecision = { rule: string; decision: string; reason: string; details?: Record<string, unknown> };

export type StartCallResult =
  | { kind: "dialing"; sessionId: string; callId: string | null; decisions: PolicyDecision[] }
  | { kind: "blocked"; decisions: PolicyDecision[] }
  | { kind: "error"; code: string; status: number; message: string };

const MESSAGES: Record<string, string> = {
  invalid_request: "Check the number, scenario and language.",
  not_allowlisted: "This number is not on the demo allowlist, so it cannot be dialed.",
  operator_not_configured: "Start Call is not configured on this deployment (OPERATOR_TOKEN missing on the server).",
  operator_token_rejected: "The backend rejected the console's operator token. Check that OPERATOR_TOKEN matches on Vercel and Railway.",
  bad_origin: "The request was refused (not same-origin).",
  duplicate_request: "A call to this number was just started. Wait 30 seconds before trying again.",
  rate_limited: "Call limit reached. Try again later.",
  telephony_disabled: "Telephony is disabled on the backend.",
  unknown_scenario: "The backend does not know this scenario.",
  provider_error: "The telephony provider failed to place the call.",
  backend_unreachable: "The backend is unavailable right now.",
  timeout: "The request timed out.",
  backend_error: "The backend returned an error.",
  bad_backend_response: "The backend returned an unexpected response.",
  network_error: "Network error — check your connection and retry.",
  malformed_response: "The console received a malformed response.",
};

export function callMessage(code: string, detail?: string): string {
  if (code === "provider_error" && detail) return `${MESSAGES.provider_error} (${detail})`;
  return MESSAGES[code] ?? MESSAGES.backend_error;
}

type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

export async function startCall(
  input: { to: string; scenario: string; language: string },
  fetchImpl: FetchLike = fetch,
): Promise<StartCallResult> {
  let r: Response;
  try {
    r = await fetchImpl("/api/operator/calls", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(input),
    });
  } catch {
    return { kind: "error", code: "network_error", status: 0, message: MESSAGES.network_error };
  }
  let b: Record<string, unknown>;
  try {
    b = (await r.json()) as Record<string, unknown>;
  } catch {
    const code = r.ok ? "malformed_response" : "backend_error";
    return { kind: "error", code, status: r.status, message: MESSAGES[code] };
  }
  const decisions = Array.isArray(b.decisions) ? (b.decisions as PolicyDecision[]) : [];
  if (r.ok && b.ok && b.outcome === "blocked_by_policy") return { kind: "blocked", decisions };
  if (r.ok && b.ok && b.outcome === "dialing" && typeof b.session_id === "string") {
    return { kind: "dialing", sessionId: b.session_id, callId: typeof b.call_id === "string" ? b.call_id : null, decisions };
  }
  const code = typeof b.code === "string" ? b.code : r.ok ? "malformed_response" : "backend_error";
  return { kind: "error", code, status: r.status, message: callMessage(code, typeof b.detail === "string" ? b.detail : undefined) };
}

export const TERMINAL_CALL_STATUSES = new Set(["COMPLETED", "DISCONNECTED", "FAILED"]);

/** Live status from the session proxy. Before Twilio connects the media stream the session
 *  does not exist yet (404) — that is reported as "waiting", not as an error. */
export function statusFromDetail(r: DetailResult): { status: string; terminal: boolean; endedReason?: string } {
  if (r.kind === "error") return { status: r.code === "not_found" ? "waiting_for_answer" : `error:${r.code}`, terminal: false };
  const s = r.detail.session;
  const status = String(s.call_status ?? "UNKNOWN");
  return { status, terminal: TERMINAL_CALL_STATUSES.has(status) || !!s.ended_at, endedReason: s.ended_reason ? String(s.ended_reason) : undefined };
}
