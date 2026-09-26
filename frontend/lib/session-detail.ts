// Client-side loader for the session audit detail. Talks only to this app's own
// /api/operator/* routes (same origin); the server adds OPERATOR_TOKEN, the browser never sees it.

export type Row = Record<string, unknown>;
export type TurnRow = {
  seq: number;
  speaker: string;
  text: string | null;
  interrupted: boolean;
  acts: string | null;
  spoken_text: string | null;
  controller_turn?: number | null;
  interpretation?: { actions?: Row[]; source?: string; notes?: string[] } | null;
  created_at?: string;
};
export type AuditRow = { id: string; type: string; at: string; turn_index: number | null; data: Row };
export type LatencyRow = { turn_index: number; input_mode: string; provider_mode: string; stages: Record<string, number> };
export type SessionDetail = {
  session: Row & { state?: Row; providers?: Record<string, string> };
  turns: TurnRow[];
  audit: AuditRow[];
  promise: Row | null;
  latency: LatencyRow[];
};

export type DetailErrorCode =
  | "invalid_session_id"
  | "not_found"
  | "operator_not_configured"
  | "operator_token_rejected"
  | "backend_error"
  | "backend_unreachable"
  | "bad_backend_response"
  | "network_error"
  | "malformed_response";

export type DetailResult =
  | { kind: "ok"; detail: SessionDetail; operator: boolean }
  | { kind: "error"; code: DetailErrorCode; status: number; message: string };

const MESSAGES: Record<DetailErrorCode, string> = {
  invalid_session_id: "That is not a valid session id.",
  not_found: "Session not found. It may have been purged by the retention policy.",
  operator_not_configured: "Phone-call audit trails need OPERATOR_TOKEN on the frontend server; it is not configured on this deployment.",
  operator_token_rejected: "The backend rejected the console's operator token. Check that OPERATOR_TOKEN is identical on Vercel and Railway.",
  backend_error: "The backend returned an error. Try again shortly.",
  backend_unreachable: "The backend could not be reached. Try again shortly.",
  bad_backend_response: "The backend returned an unexpected response.",
  network_error: "Network error — check your connection and retry.",
  malformed_response: "The console received a malformed response.",
};

export function messageFor(code: DetailErrorCode): string {
  return MESSAGES[code] ?? MESSAGES.backend_error;
}

type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

export async function loadSessionDetail(id: string, fetchImpl: FetchLike = fetch): Promise<DetailResult> {
  let r: Response;
  try {
    r = await fetchImpl(`/api/operator/sessions/${encodeURIComponent(id)}`, { cache: "no-store", credentials: "same-origin" });
  } catch {
    return { kind: "error", code: "network_error", status: 0, message: MESSAGES.network_error };
  }
  let body: unknown;
  try {
    body = await r.json();
  } catch {
    return { kind: "error", code: r.ok ? "malformed_response" : "backend_error", status: r.status, message: MESSAGES[r.ok ? "malformed_response" : "backend_error"] };
  }
  const b = (body ?? {}) as { ok?: boolean; code?: DetailErrorCode; detail?: unknown; operator?: boolean };
  if (r.ok && b.ok && isDetail(b.detail)) return { kind: "ok", detail: b.detail, operator: !!b.operator };
  const code: DetailErrorCode = b.code && b.code in MESSAGES ? b.code : r.ok ? "malformed_response" : "backend_error";
  return { kind: "error", code, status: r.status, message: messageFor(code) };
}

function isDetail(d: unknown): d is SessionDetail {
  if (!d || typeof d !== "object") return false;
  const x = d as Record<string, unknown>;
  return !!x.session && typeof x.session === "object" && Array.isArray(x.turns) && Array.isArray(x.audit) && Array.isArray(x.latency);
}
