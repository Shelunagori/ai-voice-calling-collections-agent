# Architecture

## Principle

**Language models handle language. Application code owns authority.** The LLM interprets caller speech and
(optionally) rephrases approved replies. It never decides identity, disclosure, payment approval, contact
eligibility, promise-to-pay or transfer. Those are deterministic state transitions.

## Components

| Layer | Module | Responsibility |
|---|---|---|
| Transport | `app/api/ws_browser.py`, `app/api/telephony.py` | Browser WebSocket (PCM16 16 kHz + JSON events); Twilio Media Streams (μ-law 8 kHz ↔ PCM16 16 kHz) |
| Voice runtime | `app/voice/session.py` | One `VoiceSession` per conversation: VAD, STT stream, end-of-turn, barge-in, paced TTS playback, lifecycle, latency |
| Understanding | `app/domain/understanding.py`, `nlu_rules.py` | Transcript → typed `Interpretation` (LLM JSON mode + deterministic rules; rules are the fallback and the caller-rights safety net) |
| Allowed actions | `app/domain/turn_context.py` | Phase → expected slot + allowed actions; enforced on the LLM schema, the rules parser, validation and the controller |
| Controller | `app/domain/controller.py` | Sole writer of `CollectionState`; applies proposals only after policy checks; returns a `ResponsePlan` + effects |
| Policy | `app/domain/policy.py` | Deterministic demo rules; every evaluation is a `PolicyDecision` written to the audit log |
| Responses | `app/domain/responses.py`, `realizer.py` | Bilingual templates from approved facts; optional LLM rephrase; output guard |
| Providers | `app/providers/*` | `LLMProvider`, `STTProvider`, `TTSProvider`, `TelephonyProvider`, `Notifier` interfaces + mocks + Cloudflare/Cartesia/Twilio |
| Persistence | `app/persistence/*`, `alembic/` | Structured state, turns, promises, policy decisions, audit events, latency, evaluation runs |
| Evaluation | `app/evaluation/*` | Regression cases through the real runtime, invariants, judges, audio fixtures |
| UI | `frontend/` | Ops console rendering server events; never sends authoritative state |

## Data flow for one caller turn

1. Audio frames → `EnergyVAD` (speech start/end) and the STT stream (partials/finals).
2. `EndOfTurnDetector` decides the turn is complete (see VOICE_RUNTIME.md).
3. `Understanding.interpret()` → `Interpretation(actions=[ProposedAction...])`. **No side effects**, cancellable.
4. Under the session lock, `ConversationController.apply()` runs the relevant policy checks, mutates state,
   records audit events and returns a `TurnOutcome(plan, decisions, effects)`.
5. The realizer renders the plan (template, or LLM rephrase + guard → template fallback).
6. TTS streams audio; playback is paced and tagged with a generation id.
7. Effects run after (or regardless of) playback: end call, transfer, notification.

## Why typed proposals

```json
{"actions": [{"action": "PROPOSE_PAYMENT", "amount": 30000, "days_from_now": 14}]}
```

The schema (`app/domain/commands.py`) forbids unknown fields, bounds numbers and caps text. There is no
action that sets identity, disclosure or promise state — those transitions exist only inside the controller.
A model that returns `{"action": "SET_IDENTITY_VERIFIED"}` is rejected by validation and the turn falls back
to the rules parser (tested: `llm_invalid_json`).

## Authoritative state

`CollectionState` (`app/domain/models.py`) holds `identity_status`, `debt_disclosed`, balance and envelope,
`proposed_amount/date`, `promise_status`, `readback_delivered`, `promise`, `stop_contact`,
`future_contact_eligible`, `human_transfer_requested`, `transfer_reason/status`, `call_status`, `phase`.
It is persisted as JSON on `voice_sessions.state` and as columns for querying; transcript text lives
separately in `conversation_turns` with an expiry.

## Deployment shape

Single FastAPI process (uvicorn) + PostgreSQL. Sessions are in-process objects bound to a WebSocket, so the
service runs as one replica (see DECISIONS.md D6). The Next.js frontend is a separate static-ish app calling
the backend's public HTTPS/WSS URL.
