# AI Voice Calling Collections Agent

> **Portfolio proof-of-concept.** Synthetic identities and synthetic accounts only. The policy rules are
> *simulated demo rules inspired by regulated collections workflows* — they are not a statement of Japanese
> law, have not been reviewed by counsel, are not certified, and this system must not be used to contact
> real debtors.

## 1. What this demonstrates

An engineer-owned, audio-in → audio-out voice agent for collections calls, built around one principle:

**Language models handle language. Application code owns authority.**

- A real-time voice runtime: streaming audio, VAD separated from semantic end-of-turn detection,
  barge-in that cancels agent audio mid-sentence, explicit lifecycle state machine, per-stage latency marks.
- A deterministic conversation controller and policy engine: identity-before-disclosure, an approved
  negotiation envelope, promise-to-pay that needs an explicit "yes" to a read-back the caller actually heard,
  stop-contact, human transfer, calling hours and attempt limits — every decision audited.
- Provider abstraction: Cloudflare Workers AI (LLM), Cartesia Ink/Sonic (STT/TTS), Twilio Voice + Media
  Streams (PSTN) — each behind an interface with a deterministic mock, so the whole system runs, and CI passes,
  with no paid APIs.
- Evaluation-driven engineering: 30 regression scenarios (EN/JA, typed and synthetic-audio) replayed through
  the real runtime with authoritative invariants, plus an optional LLM-as-judge.
- An operations-console UI (Next.js) that a reviewer in Japan can use in a browser, without a phone number.

## 2. Live demo

- Frontend: https://ai-voice-calling-collections-agent.vercel.app/demo
- Backend health: https://ai-voice-calling-collections-agent-production.up.railway.app/ready

## 3. Architecture

```
 caller ──► transport ──► VAD ──► streaming STT ──► end-of-turn ──► understanding (LLM JSON + rules net)
 (mic/PSTN)  (WS/Twilio)    │                                              │ typed proposal
    ▲                      ▼ barge-in: gen++ · cancel TTS · clear buffer    ▼
    │                                                     ConversationController.apply()  ◄──► policy engine
    │                                                     (authoritative CollectionState)        (audited)
    │                                                              │ approved facts             │
    └── transport ◄── paced playback ◄── streaming TTS ◄── realizer + output guard     audit trail / PostgreSQL
```

More: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · [docs/VOICE_RUNTIME.md](docs/VOICE_RUNTIME.md) ·
[docs/POLICY_ENGINE.md](docs/POLICY_ENGINE.md) · the in-app `/architecture` page.

## 4. Two-minute reviewer walkthrough

1. Open the demo → **Start browser voice demo**. Pick **English** or **日本語**, scenario **A** (Haruto Sato /
   佐藤 陽翔), **Start voice session**. Without speech credentials the demo uses typed input (same runtime).
2. Click the suggested lines: "Yes, this is Haruto" → date of birth (shown on the scenario card) → "I can pay
   30,000 yen in two weeks" → "Yes". Watch *Collection state*: identity → `VERIFIED`, proposal → read-back →
   promise `CONFIRMED`; *Policy decisions* shows every check.
3. **New session**, scenario **C**: ask for 60 days. The policy engine blocks
   `PAYMENT_DATE_WITHIN_MAX_EXTENSION`; a later "yes" does not create a promise.
4. Scenario **F**: type while the agent is speaking (or press *Interrupt agent*). The utterance is cut
   (strike-through), lifecycle shows `AGENT_SPEAKING → INTERRUPTED`, the barge-in pill shows detect→stopped time.
5. Scenario **D** (wrong party) and **E** (stop-contact) show non-disclosure and stop-contact state.
6. **Inspect audit trail** after ending; then **Evaluation → Run evaluation suite**.

Full script: [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md).

## 5. What is actually implemented

| Area | Status |
|---|---|
| Conversation controller + typed proposals (`Interpretation`/`ProposedAction`) | Implemented, tested |
| Policy engine (identity, disclosure, min amount, ≤ balance, date window, no discounts, stop-contact, transfer, calling hours, attempt limits, single promise) | Implemented, tested, audited |
| Country-aware outbound calling window (`POLICY_COUNTRY=JP` → simulated Asia/Tokyo 08:00–21:00; empty/other → `NOT_APPLICABLE`, attempt + stop-contact rules still apply) | Implemented, tested |
| Promise-to-pay (verified + valid + read-back fully played + explicit yes + policy) | Implemented, tested; DB-unique per session |
| Output guard (no amounts/debt words before verification; only approved amounts/dates in ¥/円/yen/JPY/spoken forms; no threats/waivers) + LLM rephrasings may not introduce any number absent from the approved template | Implemented, tested |
| Voice runtime: energy VAD with noise floor, semantic end-of-turn, barge-in, generation-tagged paced playback, lifecycle state machine | Implemented, tested (virtual clock + wall clock) |
| Latency instrumentation (per-stage marks, p50/p95 by provider mode) | Implemented |
| English / Japanese (templates, number/date/era parsing, currency formatting, provider language params) | Implemented; **not native-speaker reviewed** |
| Browser demo: mic via AudioWorklet (when STT configured), typed fallback, live console | Implemented |
| Cloudflare Workers AI adapter (JSON mode NLU, SSE streaming, timeouts, bounded retries, error classes) | Implemented, contract-tested against a mock transport; JSON-mode NLU **observed working on the deployed backend** (2026-09-26); SSE rephrasing path not exercised live |
| Cartesia Ink STT / Sonic TTS adapters (WebSocket, cancel on barge-in) | Implemented, contract-tested against local fakes and documented message shapes; **observed working on the deployed backend** (ink-whisper final transcripts, sonic-3 PCM stream) on 2026-09-26 |
| Twilio: outbound call, signed webhooks, idempotent status callbacks, Media Streams transport, `<Dial>` transfer, hangup | Implemented, tested with fakes; **no real PSTN call has been made** |
| Persistence (PostgreSQL/SQLite, Alembic), audit trail, transcript retention purge | Implemented, tested on PostgreSQL 16 and SQLite |
| Observability: JSON logs + correlation ids, Prometheus `/metrics`, `/health`, `/ready` | Implemented |
| Evaluation harness (30 cases), mock heuristic judge, Cloudflare LLM judge, audio fixture harness | Implemented; LLM judge and STT benchmark **not executed** (no credentials) |
| Security: operator bearer token, allow-listed dialling, telephony routes absent unless configured, Twilio signature + per-call media token, rate limits (proxy-appended client IP), payload limits, WS origin check, phone-session audit data operator-only | Implemented |

## 6. What is simulated

- **All data**: seven synthetic debtors/accounts (A–G). Phone numbers are in a synthetic range and are never
  dialled unless an operator adds a number to `DEMO_CALL_ALLOWED_NUMBERS`.
- **Policy**: demo rules only (see [docs/POLICY_ENGINE.md](docs/POLICY_ENGINE.md)).
- **Mock providers** (default): the rules parser is the language layer, mock STT emits scripted transcripts,
  mock TTS produces near-silent PCM paced like speech (the browser can voice replies locally with the Web Speech
  API — labelled as a stand-in).
- **Human transfer** is a deterministic domain state; without Twilio + `TWILIO_TRANSFER_NUMBER` it is recorded
  as `SIMULATED`.
- **SMS/e-mail** confirmations go to a mock outbox behind the `Notifier` interface.

## 7. Optional external providers

| Provider | Enables | Variables |
|---|---|---|
| Cloudflare Workers AI | LLM understanding, optional LLM phrasing, LLM-as-judge | `LLM_PROVIDER=cloudflare`, `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_AI_MODEL` |
| Cartesia | Browser microphone (STT) and real speech (TTS), EN + JA | `STT_PROVIDER=cartesia`, `TTS_PROVIDER=cartesia`, `CARTESIA_API_KEY`, `CARTESIA_VOICE_ID`, `CARTESIA_VOICE_ID_JA` |
| Twilio | Real phone calls | `TELEPHONY_ENABLED=true`, `TWILIO_*`, `OPERATOR_TOKEN`, `DEMO_CALL_ALLOWED_NUMBERS` |

Missing credentials degrade to mocks; the app never crashes for lack of a key. Full reference: [.env.example](.env.example).

## 8. Evaluation methodology

- `python -m app.evaluation` replays 30 scripted callers through the **real** `VoiceSession` + controller +
  policy on a **virtual clock** with mock providers. Every case checks universal safety invariants
  (no amounts before verification, promise preconditions, single promise, stop-contact consistency, no stale
  audio after barge-in, ordered barge-in timestamps, clean termination) plus case expectations.
- Categories: identity, wrong party, failed verification, promise, partial payment, invalid extension,
  below-minimum, discount, prompt injection, hallucinated LLM terms, invalid LLM JSON, stop-contact
  (verified/unverified), transfer, typed and audio barge-in, "yes" over an unfinished read-back, ambiguity,
  amount/date correction, hang-up mid-confirmation, Japanese flows, noisy audio, thinking pause, short answer,
  silence timeout.
- Judge scores (mock heuristic in CI; Cloudflare LLM judge opt-in) are **supplementary** and never override
  invariants. Details: [docs/EVALUATION.md](docs/EVALUATION.md).

Latest local result (this commit, mock providers): **30/30 cases pass**; VAD signal fixtures **6/6**.

## 9. Measured latency

What has actually been measured, and on what:

| Measurement | Result | Conditions |
|---|---|---|
| Barge-in cancel path (detect → TTS task stopped → transport cleared), in-process | p50 0.27 ms, p95 0.50 ms, n=50 | wall clock, mock TTS, dev container; excludes network and client buffer flush |
| Typed turn → first audio frame sent | 2–8 ms | mock providers (rules NLU, template realizer, mock TTS); pipeline overhead only |
| Voice short answer ("yes"): speech end → turn commit | ≈260 ms | virtual clock; VAD hang (240 ms) + quick end-of-turn rule |
| **Live** voice turn, speech end → first agent audio | **≈1.43 s** (STT final 312 ms + end-of-turn wait 297 ms + Cloudflare NLU 727 ms + policy <1 ms + Cartesia TTS first audio 96 ms) | **n=1**, deployed Railway backend, real Cartesia ink-whisper / sonic-3 + Cloudflare llama-3.3-70b, 2026-09-26; the "caller" audio was the agent's own TTS greeting streamed back as microphone input, not a human voice |
| **Live** typed turn → first agent audio | 873 ms (NLU 767 ms, TTS first audio 105 ms) | n=1, same deployment |
| **Live** TTS time-to-first-audio | 410 ms on a cold connection, 96–140 ms warm | n=4, same deployment |

These live figures are single samples from one location, not p50/p95; they show the pipeline works end
to end and where the time goes (LLM understanding is the largest stage). The UI and
`/api/metrics/latency` report p50/p95 per provider mode from real sessions; mock-mode figures are labelled
as pipeline-only. Expected bottlenecks and the budget are discussed in
[docs/VOICE_RUNTIME.md](docs/VOICE_RUNTIME.md#latency-budget).

## 10. Limitations

- Live provider checks so far are a handful of manual WebSocket probes against the deployed backend
  (Cartesia STT/TTS, Cloudflare NLU) and one real PSTN call placed by the owner (2026-09-26), which exposed
  the partial-DOB bug fixed by the allowed-action contract; no automated live benchmark.
- Energy VAD is a deliberately simple, dependency-free baseline; production would use a model VAD and the
  provider's endpointing, tuned on labelled call audio.
- The rules parser covers common EN/JA phrasings; free-form speech needs the LLM path.
- Japanese output has not been validated by a native speaker; multilingual support is technical, and
  native-quality review is a separate production requirement.
- Single-process session state (WebSockets are stateful): run one replica, or add sticky routing + a shared
  store before scaling out. Rate limiting is in-process.
- Human transfer to a live agent needs a Twilio number; browser sessions simulate it.
- No production post-training has been performed ([plan](docs/POST_TRAINING_PLAN.md)).

## 11. Local setup

Requirements: Python 3.12+, Node 22+. No API keys, no database server needed.

```bash
# backend (SQLite file DB, mock providers)
cd backend
python3.12 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:_app_factory --factory --reload --port 8000

# frontend
cd frontend
npm ci
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 npm run dev   # http://localhost:3000
```

Full stack with PostgreSQL: `docker compose up --build`.

Checks: `cd backend && ruff check app tests alembic && mypy app && pytest -q && python -m app.evaluation`
· `cd frontend && npm run lint && npm run typecheck && npm test && npm run build`.

## 12. Deployment

Railway (backend + PostgreSQL) and Railway or Vercel (frontend). Everything is prepared — Dockerfiles,
`railway.json`, `/ready` health check, `PORT` handling, migrations on start, graceful shutdown. The live
instance was deployed by the repository owner; the assistant that wrote this code has not deployed it.
Phone-session audit detail and the **Telephony → Start Call** page need the server-only `OPERATOR_TOKEN` on
the frontend (see DEPLOYMENT.md); there is no browser login and the token never reaches the browser. Step-by-step: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).
Telephony setup: [docs/TELEPHONY.md](docs/TELEPHONY.md).

## 13. Project structure

```
backend/
  app/domain/        controller, policy engine, typed commands, rules NLU, responses + guard, scenarios, audit
  app/voice/         session runtime, VAD, end-of-turn, lifecycle, latency, audio utils
  app/providers/     interfaces, mocks, Cloudflare, Cartesia, Twilio, factory
  app/api/           REST, browser WebSocket, telephony webhooks + media stream, evaluation API
  app/persistence/   schema, repository, DB recorder      alembic/  migrations
  app/evaluation/    cases, runner, judges, audio benchmark
  tests/             unit, policy, state machine, API, provider contracts, persistence, runtime
frontend/            Next.js console: /, /demo, /architecture, /evaluation, /sessions, /telephony
docs/                architecture, runtime, policy, evaluation, telephony, deployment, post-training, decisions
```

## Implemented vs planned / production evolution

**Implemented** is everything in §5 marked implemented. **Planned / production evolution** (not built):
model-based VAD and semantic turn models; real-provider latency benchmarking and tuning; streaming LLM →
sentence-level TTS; Redis-backed session routing and rate limits for multiple replicas; live Twilio transfer
queue with warm hand-off context; SMS/e-mail providers; consent capture and recording disclosures reviewed by
counsel; native-speaker review of Japanese; production post-training loop per
[docs/POST_TRAINING_PLAN.md](docs/POST_TRAINING_PLAN.md).
