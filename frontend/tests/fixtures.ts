// Synthetic session-detail fixtures shaped like the backend's /api/sessions/{id} response.
// Modelled on the real PSTN call 2fa211b9 but with fake identifiers (no real call SID or number).
import type { SessionDetail } from "../lib/session-detail";

export const PHONE_ID = "2fa211b9-44ed-40c0-bb3d-69ef49df85f1";
export const BROWSER_ID = "11111111-2222-4333-8444-555555555555";

const ev = (i: number, type: string, turn: number, data: Record<string, unknown>) => ({
  id: `e${i}`,
  type,
  turn_index: turn,
  at: `2026-09-26T16:27:${String(10 + i).padStart(2, "0")}`,
  data,
});

export function phoneDetail(): SessionDetail {
  return {
    session: {
      id: PHONE_ID,
      channel: "phone",
      language: "en",
      input_mode: "voice",
      scenario_key: "A",
      call_id: "CA00000000000000000000000000000000",
      call_status: "DISCONNECTED",
      identity_status: "VERIFIED",
      promise_status: "NONE",
      ended_reason: "caller_hangup",
      started_at: "2026-09-26T16:27:39",
      ended_at: "2026-09-26T16:28:55",
      providers: { llm: "cloudflare:@cf/meta/llama-3.3-70b-instruct-fp8-fast", stt: "cartesia:ink-whisper", tts: "cartesia:sonic-3", telephony: "twilio" },
      state: { outstanding_balance: 80000, allowed_min_payment: 20000, max_extension_days: 30, proposed_amount: null, proposed_date: null, future_contact_eligible: true },
    },
    turns: [
      { seq: 0, speaker: "agent", text: "Hello, this is Aoi… Am I speaking with Haruto Sato?", spoken_text: "Hello,", interrupted: true, acts: "GREETING", controller_turn: 0, interpretation: null },
      { seq: 1, speaker: "caller", text: "Yes", spoken_text: null, interrupted: false, acts: null, controller_turn: 1, interpretation: { actions: [{ action: "AFFIRM" }], source: "llm" } },
      { seq: 2, speaker: "caller", text: "April 1988", spoken_text: null, interrupted: false, acts: null, controller_turn: 2, interpretation: { actions: [{ action: "PARTIAL_DOB", dob_year: 1988, dob_month: 4, dob_day: null }], source: "llm+rules", notes: ["llm_partial_dob_normalised"] } },
      { seq: 3, speaker: "agent", text: "Thank you. And what day in April?", spoken_text: null, interrupted: false, acts: "ASK_DOB_PART", controller_turn: 2, interpretation: null },
      { seq: 4, speaker: "caller", text: "12th April 1988", spoken_text: null, interrupted: false, acts: null, controller_turn: 3, interpretation: { actions: [{ action: "PROVIDE_DOB", dob: "1988-04-12" }], source: "llm" } },
    ],
    audit: [
      ev(1, "policy.decision", 0, { rule: "DEMO_CALLING_HOURS_WINDOW", decision: "NOT_APPLICABLE", reason: "no simulated calling-hours window configured for POLICY_COUNTRY=IN", details: { country: "IN" } }),
      ev(2, "voice.barge_in", 0, { source: "vad_sustained_speech", cancel_latency_ms: 0.92, played_s: 0.534, interrupted_text: "Hello, this is Aoi" }),
      ev(3, "identity.name_confirmed", 1, {}),
      ev(4, "identity.partial_dob", 2, { year: 1988, month: 4, day: null, missing: ["day"], source: "llm+rules", llm_validation_failed: false }),
      ev(5, "identity.verified", 3, { factor: "date_of_birth", source: "llm", llm_validation_failed: false }),
      ev(6, "policy.decision", 3, { rule: "IDENTITY_REQUIRED_BEFORE_DISCLOSURE", decision: "ALLOW", reason: "identity verified" }),
      ev(7, "disclosure.allowed", 3, { rule: "IDENTITY_REQUIRED_BEFORE_DISCLOSURE" }),
      ev(8, "voice.lifecycle", 3, { transitions: [{ from: "IDLE", to: "PROCESSING", cause: "session_start", t_ms: 0 }, { from: "PROCESSING", to: "AGENT_SPEAKING", cause: "tts", t_ms: 240 }] }),
      ev(9, "session.ended", 3, { reason: "caller_hangup", call_status: "DISCONNECTED" }),
    ],
    promise: null,
    latency: [
      { turn_index: 1, input_mode: "voice", provider_mode: "llm=cloudflare", stages: { speech_end_to_final_transcript: 320, nlu: 771, tts_request_to_first_audio: 119, speech_end_to_first_audio: 1519 } },
      { turn_index: 2, input_mode: "voice", provider_mode: "llm=cloudflare", stages: { speech_end_to_final_transcript: 300, nlu: 700, speech_end_to_first_audio: 1400 } },
    ],
  };
}

export function browserDetail(): SessionDetail {
  return {
    session: { id: BROWSER_ID, channel: "browser", language: "ja", input_mode: "text", scenario_key: "B", call_status: "COMPLETED", identity_status: "VERIFIED", promise_status: "CONFIRMED", ended_reason: "completed_with_promise", providers: { llm: "mock" }, state: { outstanding_balance: 50000, allowed_min_payment: 10000, max_extension_days: 14 } },
    turns: [{ seq: 0, speaker: "agent", text: "もしもし", spoken_text: null, interrupted: false, acts: "GREETING", controller_turn: 0, interpretation: null }],
    audit: [ev(1, "promise.confirmed", 5, { amount: 30000, due_date: "2026-10-10" })],
    promise: { amount: 30000, due_date: "2026-10-10", confirmation_turn: 5 },
    latency: [],
  };
}
