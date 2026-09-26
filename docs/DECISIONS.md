# Decision log

Decisions taken autonomously while building the POC. Each is reversible.

**D1 — Explicit runtime instead of Pipecat.** The voice loop is ~700 lines of explicit asyncio
(`app/voice/session.py`). Reason: barge-in ordering (invalidate generation → cancel → clear → confirm
stopped), read-back delivery tracking and deterministic virtual-clock testing are the point of the demo and
are easier to show and test without a framework. Pipecat remains a good option for production transports.

**D2 — Rules parser as mock LLM, fallback and safety net.** One deterministic EN/JA parser powers mock mode,
catches LLM failures, and independently detects stop-contact / human requests so they cannot be dropped.

**D3 — Templates by default; LLM phrasing optional and guarded.** `RESPONSE_MODE=template` keeps replies
compliant and fast; `llm` mode rephrases approved text and falls back on any guard violation or any number
not present in the template. Utterances that carry the terms themselves (disclosure, read-back, rejection
reasons, promise confirmation, stop-contact acknowledgement) are never paraphrased.

**D4 — Read-back must be heard.** A promise needs an explicit "yes" *after* ≥ 90 % of the read-back was
played. Otherwise barge-in ("yes" over an unfinished question) could create consent to terms not heard.

**D5 — Energy VAD with minimum-statistics floor.** Dependency-free, deterministic, adequate for a demo.
Production: model VAD (e.g. Silero) plus provider endpointing, tuned on labelled audio.

**D6 — Single replica.** Sessions are in-process and bound to their WebSocket; rate limiting is in-process.
Scaling out needs sticky routing plus shared state (Redis) — documented, not built.

**D7 — SQLite for zero-setup local runs, PostgreSQL in production.** Portable column types; one Alembic
migration verified against both (`alembic check` in tests and CI).

**D8 — No Twilio/Cartesia SDKs.** REST/WebSocket calls with httpx/websockets keep adapters small and
contract-testable; domain code depends only on the provider protocols.

**D9 — Browser demo sessions start from pristine scenario terms.** Reviewer sessions are reproducible;
account-level flags (stop-contact, attempts) gate *outbound* contact, which the operator page shows.

**D10 — Mock TTS is near-silent PCM paced like speech.** Keeps interruption timing realistic without a TTS
key; the browser may voice replies with the Web Speech API, clearly labelled as a local stand-in.

**D11 — Git author.** Commits use the identity of the repository's initial commit (Shailendra Nagori) with a
`Co-Authored-By: Claude` trailer; the machine had no global git identity configured.

**D13 — Browser voice client outside React.** Socket, player and microphone live in
`lib/voice-client.ts` (`VoiceClient` + `SessionManager`), which catches every handler error and reports it
as an inline notice. Found after a production crash: an expression-bodied `useEffect` returned the Promise
that Chrome 14x's `scrollIntoView()` now returns, React called it as a cleanup and the route died. A
test now rejects expression-bodied effects.

**D12 — Evaluation on a virtual clock.** Makes the suite deterministic and fast; wall-clock behaviour is
covered separately (`tests/test_wallclock_barge_in.py`). Latency numbers from the suite are labelled as
virtual/pipeline-only.
