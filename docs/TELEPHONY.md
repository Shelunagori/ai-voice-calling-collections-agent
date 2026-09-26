# Telephony (Twilio)

Status: **implemented behind `TelephonyProvider`, tested with fakes, never exercised against the real PSTN
from this repository.** Disabled by default (`TELEPHONY_ENABLED=false`); the app boots and the browser demo
works without it. While telephony is not active, every `/telephony/*` route returns 404 (and the media
WebSocket closes), so nothing can be spoofed on a default deployment.

## Flow

1. Operator calls `POST /api/operator/calls` with `Authorization: Bearer $OPERATOR_TOKEN` and
   `{"to": "+81…", "scenario": "A", "language": "ja"}`. Checks: telephony active, per-IP rate limit, E.164
   format, number on `DEMO_CALL_ALLOWED_NUMBERS`, then the contact policy (simulated calling window when
   `POLICY_COUNTRY=JP`, otherwise `NOT_APPLICABLE`; attempt limit; account stop-contact). Blocked → `{"status": "blocked_by_policy", decisions}` and nothing is
   dialled. Allowed → Twilio `Calls.json` with status callbacks; attempts are incremented.
2. Twilio requests `POST /telephony/twilio/voice?session_id=…` — `X-Twilio-Signature` validated against
   `TWILIO_WEBHOOK_BASE_URL + path + query`. Response TwiML:
   `<Connect><Stream url="wss://…/telephony/twilio/media">` with `session_id` and an HMAC token parameter.
   Unknown/inbound calls get a fresh verification-first session (scenario A, Japanese). Pending call
   contexts expire after 10 minutes and are capped at 100.
3. `WS /telephony/twilio/media` — on `start`, the HMAC token is verified (constant time) *before* the pending
   entry is consumed (a forged attempt cannot burn a real call's slot), capacity is checked, then a
   `VoiceSession(channel=PHONE, input_mode=voice)` starts. `media` payloads (μ-law 8 kHz) are converted to
   PCM16 16 kHz; agent audio goes back as `media` events; barge-in sends `clear`. `stop` → session ends.
4. `POST /telephony/twilio/status` — signed; de-duplicated by `CallSid:CallStatus:SequenceNumber` in
   `telephony_webhooks` (Twilio retries); terminal statuses end the live session (`call_completed`,
   `call_no-answer`, …).
5. Human transfer: the controller sets the transfer state; with `TWILIO_TRANSFER_NUMBER` the adapter updates
   the live call with `<Dial>`; otherwise the transfer is recorded as `SIMULATED`.
6. If the session ends for any reason other than caller hang-up or transfer, the adapter hangs the call up.

## Setup (human steps)

1. Twilio account, a voice-capable number (`TWILIO_PHONE_NUMBER`). **Trial accounts can only call verified
   caller IDs** and play a trial preamble; do not try to work around this.
2. Deploy the backend on a public HTTPS URL; set `TWILIO_WEBHOOK_BASE_URL` to it exactly (scheme + host, no
   trailing slash) — signature validation depends on it.
3. Set `TELEPHONY_ENABLED=true`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `OPERATOR_TOKEN`,
   `DEMO_CALL_ALLOWED_NUMBERS` (your own verified number), optionally `TWILIO_TRANSFER_NUMBER`.
4. Optional inbound: point the number's Voice webhook to `POST {base}/telephony/twilio/voice`.
5. Place a call from the console's **Telephony** page (`/telephony`): enter an allowlisted E.164 number,
   pick scenario and language, **Start Call**. The Next.js server forwards the request with the server-only
   `OPERATOR_TOKEN` (also set on the frontend); the page shows the policy decisions, session id and Twilio
   call id, polls the session's `call_status` until it is terminal, and links to the session audit page.
   Contact eligibility shows each scenario's debtor and every number where someone asked to stop; a
   stop-contact heard on a call blocks every later call to that number, for every scenario.
   curl against Railway with the bearer token still works. Resetting demo accounts is curl-only
   (`POST /api/operator/reset-demo`). Calling to Japan from a non-Japanese number may need Twilio
   geo-permissions enabled.

## Security

Signatures on every HTTP webhook; per-call HMAC on the media WebSocket; operator bearer token compared in
constant time; allow-listed destinations; credentials only on the server; no credentials in logs (JSON
formatter redacts token-like values).
