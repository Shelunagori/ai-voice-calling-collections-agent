// Pure reducer for the live console. Everything shown is derived from server events;
// the browser never computes or sends authoritative collection state.

export type TranscriptItem = {
  key: string;
  speaker: "caller" | "agent";
  text: string;
  interrupted?: boolean;
  spokenText?: string | null;
  acts?: string;
  intents?: string[];
  realizer?: string;
};

export type PolicyItem = {
  decision_id: string;
  rule: string;
  decision: "ALLOW" | "BLOCK" | "NOT_APPLICABLE";
  reason: string;
  timestamp: string;
  turn_index: number | null;
};

export type AuditItem = { event_id: string; type: string; at: string; turn_index: number | null; data: Record<string, unknown> };
export type LatencyItem = { turn_index: number; input_mode: string; stages: Record<string, number>; providers?: Record<string, string> };
export type LifecycleItem = { from: string; to: string; cause: string; at: number };
export type BargeIn = {
  turn_index: number;
  source: string;
  cancel_latency_ms: number;
  played_s: number;
  interrupted_text: string;
};

export type Collection = {
  identity_status: string;
  identity_attempts: number;
  debt_disclosed: boolean;
  disclosure_allowed: boolean;
  outstanding_balance: number;
  currency: string;
  allowed_min_payment: number;
  max_extension_days: number;
  proposed_amount: number | null;
  proposed_date: string | null;
  promise_status: string;
  readback_delivered: boolean;
  promise: null | {
    promise_id: string;
    amount: number;
    currency: string;
    due_date: string;
    confirmation_turn: number;
    confirmed_at: string;
    policy_decision_ids: string[];
  };
  stop_contact: boolean;
  future_contact_eligible: boolean;
  human_transfer_requested: boolean;
  transfer_reason: string | null;
  transfer_status: string;
  call_status: string;
  ended_reason: string | null;
  phase: string;
  language: string;
};

export type ConsoleState = {
  status: "idle" | "connecting" | "live" | "ended" | "error";
  sessionId?: string;
  inputMode?: string;
  providers?: Record<string, string>;
  voiceState: string;
  lifecycle: LifecycleItem[];
  transcript: TranscriptItem[];
  partial: string;
  collection?: Collection;
  policy: PolicyItem[];
  audit: AuditItem[];
  latency: LatencyItem[];
  bargeIns: BargeIn[];
  notifications: { channel: string; provider: string; delivered: boolean; detail: string }[];
  errors: string[];
  endedReason?: string;
};

export const initialState: ConsoleState = {
  status: "idle",
  voiceState: "IDLE",
  lifecycle: [],
  transcript: [],
  partial: "",
  policy: [],
  audit: [],
  latency: [],
  bargeIns: [],
  notifications: [],
  errors: [],
};

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type ServerEvent = { type: string; [k: string]: any };
export type Action = { kind: "connecting" } | { kind: "event"; event: ServerEvent } | { kind: "socket_closed"; reason?: string } | { kind: "reset" };

export function reduce(state: ConsoleState, action: Action): ConsoleState {
  switch (action.kind) {
    case "reset":
      return initialState;
    case "connecting":
      return { ...initialState, status: "connecting" };
    case "socket_closed":
      return state.status === "ended" ? state : { ...state, status: "ended", endedReason: state.endedReason ?? action.reason ?? "disconnected" };
    case "event":
      return applyEvent(state, action.event);
  }
}

function applyEvent(s: ConsoleState, e: ServerEvent): ConsoleState {
  switch (e.type) {
    case "session.created":
      return { ...s, status: "live", sessionId: e.session_id, inputMode: e.input_mode, providers: e.providers };
    case "lifecycle":
      return { ...s, voiceState: e.to, lifecycle: [...s.lifecycle, { from: e.from, to: e.to, cause: e.cause, at: e.at }] };
    case "state":
      return { ...s, collection: e.collection as Collection, voiceState: e.voice_state ?? s.voiceState };
    case "transcript.partial":
      return { ...s, partial: e.text };
    case "turn.caller":
      return {
        ...s,
        partial: "",
        transcript: [
          ...s.transcript,
          {
            key: `c${e.index}`,
            speaker: "caller",
            text: e.text,
            intents: (e.interpretation?.actions ?? []).map((a: { action: string }) => a.action),
          },
        ],
      };
    case "agent.speaking":
    case "turn.agent": {
      const item: TranscriptItem = {
        key: `a${e.index}`,
        speaker: "agent",
        text: e.text,
        interrupted: e.type === "turn.agent" ? e.interrupted : false,
        spokenText: e.type === "turn.agent" ? e.spoken_text : undefined,
        acts: e.acts,
        realizer: e.realizer,
      };
      const existing = s.transcript.findIndex((t) => t.key === item.key);
      const transcript = existing >= 0 ? s.transcript.map((t, i) => (i === existing ? item : t)) : [...s.transcript, item];
      return { ...s, transcript };
    }
    case "audit": {
      const a: AuditItem = { event_id: e.event_id, type: e.event_type, at: e.at, turn_index: e.turn_index, data: e.data ?? {} };
      const next = { ...s, audit: [...s.audit, a] };
      if (a.type === "policy.decision") {
        const d = a.data as unknown as PolicyItem;
        next.policy = [...s.policy, { ...d, turn_index: a.turn_index }];
      }
      return next;
    }
    case "latency":
      return { ...s, latency: [...s.latency, { turn_index: e.turn_index, input_mode: e.input_mode, stages: e.stages, providers: e.providers }] };
    case "barge_in":
      return { ...s, bargeIns: [...s.bargeIns, e as unknown as BargeIn] };
    case "notification":
      return { ...s, notifications: [...s.notifications, { channel: e.channel, provider: e.provider, delivered: e.delivered, detail: e.detail }] };
    case "error":
      return { ...s, errors: [...s.errors, `${e.provider}: ${e.message}`] };
    case "session.ended":
      return { ...s, status: "ended", endedReason: e.reason };
    default:
      return s;
  }
}

// Stages drawn in the latency waterfall, in pipeline order.
export const WATERFALL: { key: string; label: string }[] = [
  { key: "speech_end_to_final_transcript", label: "STT final" },
  { key: "final_transcript_to_turn_commit", label: "End-of-turn wait" },
  { key: "nlu", label: "Understanding" },
  { key: "policy_apply", label: "Policy + state" },
  { key: "response_ready", label: "Response text" },
  { key: "tts_request_to_first_audio", label: "TTS first audio" },
];

export function turnTotal(stages: Record<string, number>): number | undefined {
  return stages.speech_end_to_first_audio ?? stages.input_to_first_audio ?? stages.turn_commit_to_first_audio;
}

export function percentile(values: number[], p: number): number | undefined {
  if (!values.length) return undefined;
  const s = [...values].sort((a, b) => a - b);
  return s[Math.max(0, Math.ceil((p / 100) * s.length) - 1)];
}
