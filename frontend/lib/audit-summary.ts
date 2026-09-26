// Pure derivations for the reviewer-facing session detail page (browser and phone
// sessions share one layout). Date-of-birth values are masked on screen: the view
// shows *which* parts were given, not the values (they stay in the JSON export).
import { percentile } from "./session";
import type { AuditRow, LatencyRow, Row, SessionDetail, TurnRow } from "./session-detail";

const DOB_KEYS = new Set(["dob", "dob_year", "dob_month", "dob_day", "year", "month", "day"]);

export function maskDobFields(data: Row, eventType = ""): Row {
  const mask = eventType.startsWith("identity.") || "dob" in data || "dob_year" in data;
  if (!mask) return data;
  const out: Row = {};
  for (const [k, v] of Object.entries(data)) out[k] = DOB_KEYS.has(k) && v !== null && v !== undefined ? "•••" : v;
  return out;
}

export function describeAction(a: Row): string {
  const name = String(a.action ?? "?");
  if (name === "PROVIDE_DOB") return "PROVIDE_DOB (complete date)";
  if (name === "PARTIAL_DOB") {
    const parts = (["dob_year", "dob_month", "dob_day"] as const).filter((k) => a[k] !== null && a[k] !== undefined).map((k) => k.slice(4));
    return `PARTIAL_DOB (${parts.join(", ") || "none"})`;
  }
  if (name === "PROPOSE_PAYMENT") {
    const bits = [];
    if (a.amount !== null && a.amount !== undefined) bits.push(`¥${Number(a.amount).toLocaleString("en-US")}`);
    if (a.date) bits.push(String(a.date));
    if (a.days_from_now !== null && a.days_from_now !== undefined) bits.push(`+${a.days_from_now}d`);
    return `PROPOSE_PAYMENT ${bits.join(" · ")}`.trim();
  }
  return name;
}

export type ConversationRow = {
  key: string;
  speaker: "caller" | "agent";
  turn: number | null;
  text: string | null;
  spoken: string | null;
  interrupted: boolean;
  acts: string | null;
  actions: string[];
  source: string | null;
  notes: string[];
};

export function conversation(turns: TurnRow[]): ConversationRow[] {
  return turns.map((t) => ({
    key: `${t.speaker}-${t.seq}`,
    speaker: t.speaker === "caller" ? "caller" : "agent",
    turn: t.controller_turn ?? null,
    text: t.text,
    spoken: t.interrupted ? t.spoken_text : null,
    interrupted: !!t.interrupted,
    acts: t.acts,
    actions: (t.interpretation?.actions ?? []).map(describeAction),
    source: t.interpretation?.source ?? null,
    notes: t.interpretation?.notes ?? [],
  }));
}

export type Tone = "ok" | "bad" | "warn" | "info" | "muted";

const IDENTITY_LABELS: Record<string, [string, Tone]> = {
  "identity.challenge": ["challenge", "muted"],
  "identity.name_confirmed": ["name confirmed", "info"],
  "identity.partial_dob": ["DOB partial", "warn"],
  "identity.invalid_dob": ["DOB invalid", "warn"],
  "identity.failed": ["DOB failed", "bad"],
  "identity.verified": ["verified", "ok"],
  "identity.wrong_party": ["wrong party", "bad"],
  "disclosure.blocked": ["disclosure blocked", "bad"],
  "disclosure.allowed": ["disclosure allowed", "ok"],
};

export type IdentityStep = { id: string; turn: number | null; label: string; tone: Tone; detail: string };

export function identityTimeline(audit: AuditRow[]): IdentityStep[] {
  return audit
    .filter((e) => e.type in IDENTITY_LABELS)
    .map((e) => {
      const [label, tone] = IDENTITY_LABELS[e.type];
      const d = e.data ?? {};
      let detail = "";
      if (e.type === "identity.challenge") detail = String(d.factor ?? "");
      if (e.type === "identity.partial_dob") detail = `missing: ${(d.missing as string[] | undefined)?.join(", ") ?? "?"}`;
      if (e.type === "identity.failed") detail = `attempt ${d.attempts ?? "?"}`;
      if (e.type === "identity.invalid_dob") detail = String(d.reason ?? "");
      if (d.source) detail += `${detail ? " · " : ""}source ${String(d.source)}${d.llm_validation_failed ? " (LLM output failed validation)" : ""}`;
      if (e.type.startsWith("disclosure.") && d.requested) detail = `requested ${String(d.requested)}`;
      return { id: e.id, turn: e.turn_index, label, tone, detail };
    });
}

export type PolicyRow = { id: string; turn: number | null; rule: string; decision: string; reason: string; details: Row | null };

export function policyDecisions(audit: AuditRow[]): PolicyRow[] {
  return audit
    .filter((e) => e.type === "policy.decision")
    .map((e) => ({
      id: e.id,
      turn: e.turn_index,
      rule: String(e.data.rule ?? ""),
      decision: String(e.data.decision ?? ""),
      reason: String(e.data.reason ?? ""),
      details: (e.data.details as Row | undefined) ?? null,
    }));
}

export function decisionTone(d: string): Tone {
  return d === "ALLOW" ? "ok" : d === "BLOCK" ? "bad" : "muted";
}

export type BargeInRow = { id: string; turn: number | null; source: string; cancelMs: number | null; playedS: number | null; interrupted: string };
export type Transition = { from: string; to: string; cause: string; t_ms: number };

export function voiceRuntime(audit: AuditRow[]): { bargeIns: BargeInRow[]; transitions: Transition[] } {
  const bargeIns = audit
    .filter((e) => e.type === "voice.barge_in")
    .map((e) => ({
      id: e.id,
      turn: e.turn_index,
      source: String(e.data.source ?? ""),
      cancelMs: typeof e.data.cancel_latency_ms === "number" ? e.data.cancel_latency_ms : null,
      playedS: typeof e.data.played_s === "number" ? e.data.played_s : null,
      interrupted: String(e.data.interrupted_text ?? ""),
    }));
  const lc = audit.find((e) => e.type === "voice.lifecycle");
  const transitions = Array.isArray(lc?.data.transitions) ? (lc!.data.transitions as Transition[]) : [];
  return { bargeIns, transitions };
}

export const LATENCY_STAGES: { key: string; label: string }[] = [
  { key: "speech_end_to_final_transcript", label: "speech end → final transcript" },
  { key: "final_transcript_to_turn_commit", label: "transcript → turn commit" },
  { key: "nlu", label: "NLU (LLM / rules)" },
  { key: "policy_apply", label: "policy + controller" },
  { key: "tts_request_to_first_audio", label: "TTS first audio" },
  { key: "speech_end_to_first_audio", label: "end-to-end: speech end → first audio" },
];

export const MIN_SAMPLES_FOR_P95 = 5;

export type StageSummary = { key: string; label: string; count: number; p50?: number; p95?: number };

export function latencySummary(rows: LatencyRow[]): StageSummary[] {
  return LATENCY_STAGES.map(({ key, label }) => {
    const vals = rows.map((r) => r.stages?.[key]).filter((v): v is number => typeof v === "number");
    return {
      key,
      label,
      count: vals.length,
      p50: percentile(vals, 50),
      p95: vals.length >= MIN_SAMPLES_FOR_P95 ? percentile(vals, 95) : undefined,
    };
  });
}

export type EndCategory = "completed_with_promise" | "caller_hangup" | "transfer" | "stop_contact" | "error_or_disconnect" | "completed" | "in_progress";

export function endState(s: Row): { category: EndCategory; reason: string; callStatus: string } {
  const reason = String(s.ended_reason ?? "");
  const callStatus = String(s.call_status ?? "");
  let category: EndCategory = "completed";
  if (!reason && !["COMPLETED", "DISCONNECTED", "FAILED", "TRANSFER_REQUESTED"].includes(callStatus)) category = "in_progress";
  else if (s.promise_status === "CONFIRMED" || reason === "completed_with_promise") category = "completed_with_promise";
  else if (s.human_transfer_requested || reason.includes("transfer")) category = "transfer";
  else if (s.stop_contact || reason.startsWith("stop_contact")) category = "stop_contact";
  else if (reason === "caller_hangup" || reason === "caller_ended") category = "caller_hangup";
  else if (callStatus === "FAILED" || /error|unavailable|timeout|disconnect|failed/.test(reason)) category = "error_or_disconnect";
  return { category, reason, callStatus };
}

export function collectionState(d: SessionDetail): Row {
  const st = (d.session.state ?? {}) as Row;
  const rejected = d.audit.filter((e) => e.type === "payment.proposal_rejected").map((e) => e.data);
  return {
    outstanding_balance: st.outstanding_balance,
    allowed_min_payment: st.allowed_min_payment,
    max_extension_days: st.max_extension_days,
    proposed_amount: st.proposed_amount,
    proposed_date: st.proposed_date,
    rejected_proposals: rejected,
    promise_status: d.session.promise_status ?? st.promise_status,
    stop_contact: d.session.stop_contact ?? st.stop_contact,
    future_contact_eligible: st.future_contact_eligible,
    human_transfer_requested: d.session.human_transfer_requested ?? st.human_transfer_requested,
    transfer_status: d.session.transfer_status ?? st.transfer_status,
    transfer_reason: st.transfer_reason,
  };
}
