# AI Voice Collections Agent

A real-time AI voice collections POC that places live PSTN calls, verifies identity, negotiates repayment
within deterministic policy constraints, handles interruptions, and produces a full audit trail.

**Live demo:** https://ai-voice-calling-collections-agent.vercel.app ·
**Telephony (Start Call):** https://ai-voice-calling-collections-agent.vercel.app/telephony ·
**Sessions & audit:** https://ai-voice-calling-collections-agent.vercel.app/sessions ·
**Backend health:** https://ai-voice-calling-collections-agent-production.up.railway.app/ready

![Demo: Japanese browser session (identity, barge-in, promise) and a real PSTN call's audit trail](docs/demo.gif)

*73-second demo: a Japanese browser session with typed input on the deployed stack, then the audit trail of a real PSTN call. Recorded by [`frontend/e2e/record_demo.py`](frontend/e2e/record_demo.py).*

> **Portfolio proof-of-concept.** Synthetic identities and synthetic accounts only. The policy rules are
> *simulated demo rules inspired by regulated collections workflows*. They are not a statement of Japanese
> law, have not been reviewed by counsel and are not certified. Do not use this system to contact real
> debtors.

## Why I built this

- To practise production-style, real-time voice-agent engineering end to end: telephony, streaming speech,
  turn-taking, interruption, state control, observability and evaluation.
- Collections is a good stress test: identity must come before disclosure, payment terms have hard limits,
  a "yes" has to mean consent, and a stop-contact request must actually stop contact.
- The architecture deliberately separates **probabilistic language understanding** from **deterministic
  business authority**.

## Core capabilities

**Live on the deployed stack** (Vercel + Railway, real providers):

| Capability | Where |
|---|---|
| Outbound PSTN calls via Twilio REST, started from the web console | `app/api/telephony.py`, `/telephony` |
| Twilio Media Streams (μ-law 8 kHz ↔ PCM16 16 kHz), signed webhooks, per-call media-stream token | `app/api/telephony.py` |
| Cartesia Ink (`ink-whisper`) streaming STT and Sonic (`sonic-3`) streaming TTS, cancelled on barge-in | `app/providers/cartesia.py` |
| Cloudflare Workers AI (`llama-3.3-70b`) JSON-mode NLU, with a deterministic rules parser as fallback and safety net | `app/domain/understanding.py` |
| Deterministic conversation controller: the only writer of state | `app/domain/controller.py` |
| Identity-before-disclosure (name, then date of birth), including **partial DOB** handling | `controller.py`, `nlu_rules.py` |
| Payment-proposal validation (minimum, ≤ balance, date window, no discounts) | `app/domain/policy.py` |
| Promise-to-pay only after an explicit "yes" to a read-back that was actually played | `controller.py` |
| Stop-contact persisted per debtor **and** per contact point; later calls blocked before dialling (the blocking is verified by automated tests) | `policy.py`, `repository.py` |
| Barge-in: generation invalidation, TTS cancel, transport clear, measured cancel path | `app/voice/session.py` |
| English and Japanese (templates, number/date/era parsing, provider language params) | throughout |
| Audit trail: every policy decision, identity step, barge-in, lifecycle transition | `/sessions` |
| Per-turn latency breakdown (STT final, end-of-turn, NLU, policy, TTS first audio) | `app/voice/latency.py` |
| Browser voice demo (mic via AudioWorklet, or typed input) on the same runtime | `/demo` |
| **Post-trained NLU:** Gemma-2B + LoRA (synthetic data, held-out exact match 0% → **90.6%**, above the 70B zero-shot model), servable via Cloudflare BYO-LoRA | `app/training/`, [results](docs/POST_TRAINING_RESULTS.md) |

**Simulated / demo-only:**

- **Data and policy:** seven synthetic debtors/accounts (A–G) and simulated demo rules (calling window,
  attempt limit, identity attempts).
- **Human transfer:** the transfer state is implemented. A live transfer requires `TWILIO_TRANSFER_NUMBER`;
  without it the transfer is recorded as `SIMULATED`, and the public demo may use simulated transfer.
- **SMS/e-mail promise confirmation:** goes to a mock outbox behind the `Notifier` interface.
- **Evaluation suite (32 scenarios):** runs on mock providers and a virtual clock. The LLM-as-judge is
  optional and has not been run with credentials.

## Architecture

```mermaid
flowchart TD
    R[Operator / reviewer browser] --> V[Next.js on Vercel<br/>server routes hold OPERATOR_TOKEN]
    V --> B[FastAPI on Railway]
    B --> C[Deterministic controller + policy engine]
    B -->|Twilio REST| T[Outbound PSTN call]
    T <-->|Media Streams| B
    B --> STT[Cartesia Ink STT]
    STT --> NLU[Cloudflare Workers AI NLU<br/>typed proposals only]
    NLU --> C
    C --> TTS[Cartesia Sonic TTS]
    TTS -->|audio playback| T
    B --> DB[(PostgreSQL<br/>sessions · transcripts · policy decisions<br/>promises · latency · contact suppression)]
```

One caller turn: audio → VAD + streaming STT → end-of-turn → LLM interpretation into a typed proposal
(no side effects) → `ConversationController.apply()` runs the policy checks and changes state →
template (or guarded LLM rephrase) → streaming TTS. Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) ·
[docs/VOICE_RUNTIME.md](docs/VOICE_RUNTIME.md) · [docs/POLICY_ENGINE.md](docs/POLICY_ENGINE.md).

## Design principle: the language model handles language; the application owns authority

- **The LLM interprets.** It returns typed actions (`PROPOSE_PAYMENT`, `PROVIDE_DOB`, `PARTIAL_DOB`,
  `STOP_CONTACT`, …) validated by a strict schema (`app/domain/commands.py`). No action can set identity,
  disclosure, promise or transfer state.
- **The controller decides what is valid now.** Each phase has an allowed-action contract
  (`app/domain/turn_context.py`). While a date of birth is expected, a payment proposal is dropped and
  audited as `nlu.action_out_of_phase`.
- **The policy layer decides what is allowed.** Every check is a `PolicyDecision` in the audit log:
  `IDENTITY_REQUIRED_BEFORE_DISCLOSURE`, `PAYMENT_DATE_WITHIN_MAX_EXTENSION`, `STOP_CONTACT_BLOCKS_CONTACT`, …
- **So the LLM cannot:**
  - disclose the balance before verification (the output guard also blocks numbers and debt words);
  - accept a 60-day extension when policy allows 14;
  - confirm a promise without a heard read-back;
  - skip a stop-contact request (the rules parser detects caller-rights intents independently);
  - start a transfer on its own.
- **Guarded rephrasing.** Replies come from templates by default. An optional LLM rephrasing may not
  introduce any number that is absent from the approved template.

## Post-training (SFT) with measured improvement

The interpreter (caller utterance → typed action JSON) was fine-tuned: `google/gemma-2b-it` + LoRA
(r=8) on 1,136 synthetic rows, evaluated on a **frozen held-out set of 117** with a benchmark that
scores actions and slots exactly and treats a dropped caller-rights intent as a hard failure.

| Provider (held-out n=117) | exact match | latency p50 |
|---|---:|---:|
| Deterministic rules parser | 88.9% | ~0 ms |
| Llama-3.3-70B zero-shot (Cloudflare) | 88.0% | 1,005 ms |
| Gemma-2B-it zero-shot | 0.0% | — |
| **Gemma-2B-it + LoRA v2** (local T4) | **90.6%** | 1.3 s batched |
| Gemma-2B-it + LoRA v2 on Cloudflare BYO-LoRA | 85.5% | 4.1 s |
| Production path (model + rules merge, transcript-evidence grounding): 70B | **92.3%** | 0.94 s |
| Production path: Gemma-2B + LoRA v2 | **92.3%** | 4.1 s |

- v1 (737 rows) scored 82.9%; v2 added 399 rows targeted at v1's held-out failures without touching the
  held-out file (multi-intent 0 → 67%, injection 0 → 100%, JA 79.5 → 86.4%).
- The merge layer that combines the model with the rules parser was found to discard correct model
  readings (split years, spelled amounts); it now grounds numbers against the transcript itself, which
  alone took the 70B production path from 86.3% to 92.3%.
- Honest limits: the raw model still misses one Japanese human-transfer phrasing (the rules safety net
  catches it: caller-rights recall is 100% on the production path); Cloudflare's BYO-LoRA beta serves the adapter at 4.1 s p50, so the latency
  budget is not met on that path and self-hosted serving is the next step; Japanese training rows are not
  native-reviewed. Full write-up: [docs/POST_TRAINING_RESULTS.md](docs/POST_TRAINING_RESULTS.md).

## Engineering findings from real PSTN tests

**A. Partial date of birth.** On the first real call the caller said "Whatever. April. 1988".
- The LLM returned `dob="1988-04"` and schema validation rejected it.
- The unconstrained fallback parser then read "1988" as a ¥1,988 payment proposal.
- On another turn the LLM padded "you 1988." to 1988-01-01, which cost the caller a verification attempt.
- Fixes:
  - phase-aware allowed-action contract;
  - an explicit `PARTIAL_DOB` action (year/month/day, never padded);
  - DOB parts must be supported by the transcript;
  - the fallback parser is constrained to the expected slot.
- After the fix, a real call answered "April 1988" with "And what day in April?" and verified on the next
  turn. In another call the caller never gave the day: the parts were kept, noisy STT output ("3030") was
  treated as unclear, and nothing was disclosed.

**B. Identity-before-disclosure on the negative path.** In one real call the caller gave a date of birth
that did not match the synthetic record, twice. `IDENTITY_ATTEMPT_LIMIT` blocked and the call ended without
disclosing any account detail.
- The call also shows a limit of transcript grounding. STT split the year into "19 90"; the rules parser
  read "19" as the day and discarded the model's year.
- That reading was safe (nothing was invented, and the caller corrected it) but wrong. It is recorded as an
  open parser issue.

**C. Stop-contact scope.** A stop-contact request on Scenario E did not block a later Scenario A call to the
same number. The flag lived on one synthetic account, and every scenario is a different synthetic debtor.
- The request now applies to the debtor (all accounts) and to the **contact point** (the dialled number).
- The number is stored only as a hashed key plus a masked label.
- The call is blocked before a Twilio call is created.
- On PSTN, "Please don't call me again" was recognised as `STOP_CONTACT`, acknowledged, and recorded
  (`stop_contact=true`, `future_contact_eligible=false`). The follow-up blocked call is covered by automated
  tests; no live export of it is recorded yet.

**D. Interruption.** Seven barge-ins were recorded on real calls, all triggered by sustained caller speech
while the agent was talking. After detection, the runtime invalidated the audio generation and stopped TTS
in **0.86–1.39 ms**. That is internal cancellation time only. It is not perceived barge-in latency, which
also includes the VAD's sustained-speech window (default 250 ms), the network, and Twilio's buffer clear.

**E. Latency is not consistently under 1.5 s.**
- Across 31 voice turns on 6 real PSTN calls, speech end → first agent audio had a median of 1.62 s.
  14 of 31 turns were under 1.5 s, and the range was 1.15–4.67 s.
- The 4.67 s outlier was a turn where the LLM call failed and the rules fallback answered.
- Per stage:

  | Stage | Time (31 turns) |
  |---|---|
  | STT final transcript | ≈315–330 ms on most turns |
  | End-of-turn commit | ≈280–330 ms on most turns |
  | Cloudflare NLU | 594–1,262 ms (median 905 ms), one 3,876 ms outlier |
  | Cartesia TTS first audio | 99–178 ms (median 112 ms) |

- NLU is the largest and most variable stage; mitigations are in
  [VOICE_RUNTIME.md](docs/VOICE_RUNTIME.md#latency-budget).

## Verified end-to-end flows

These are real PSTN calls on the deployed stack (Twilio + Cartesia + Cloudflare), captured as session
exports. The exports are not committed; [docs/EVIDENCE.md](docs/EVIDENCE.md) lists what each one shows.

| Flow | What the call shows |
|---|---|
| Identity verification and promise-to-pay (Scenario A) | The balance was withheld until the DOB was verified, including a partial-DOB step. ¥50,000 due 2026-10-27 passed the minimum, balance and date-window checks. The read-back was played, the caller said "Yes", and the promise was confirmed (`completed_with_promise`). |
| Stop-contact request (Scenario E) | `STOP_CONTACT` was recognised and acknowledged; `stop_contact=true`, `future_contact_eligible=false`, `ended_reason=stop_contact_requested`. |
| Failed identity verification | Two mismatched DOBs led to `IDENTITY_ATTEMPT_LIMIT` BLOCK and `identity_verification_failed`, with no disclosure. |
| Partial-DOB recovery | The agent asked only for the missing parts. The call ended at `NAME_CONFIRMED` with the parts kept and no disclosure. |
| Barge-in / TTS cancellation | Seven VAD-triggered barge-ins across four calls, each with the interrupted text, the played duration and the cancel timestamps in the audit trail. |
| Human-transfer request (Scenario G) | Verified PSTN human-transfer request with simulated transfer completion: `caller_requested_human` → `TRANSFER_REQUESTED`, `transfer_status=SIMULATED` (no `TWILIO_TRANSFER_NUMBER` configured). |

- No PSTN evidence is recorded for scenarios B, C, D and F. They are covered by the browser runtime and
  the evaluation suite.
- Automated checks: backend 239 tests (PostgreSQL + SQLite), frontend 100 tests, evaluation 32/32 (mock
  providers).

## Demo scenarios

| Scenario | Behaviour under test | Key invariant |
|---|---|---|
| A Cooperative payer | Successful promise-to-pay (¥30,000) | Amount and date stay inside the approved envelope |
| B Cannot pay in full | Negotiation within limits | Nothing below the minimum or beyond the window is accepted |
| C Extension beyond policy | 60-day request vs 14-day limit | The promise is never confirmed |
| D Wrong party | Identity / privacy | No debt disclosure |
| E Stop contact | Contact suppression | Future calls to that debtor and number are blocked |
| F Barge-in | Voice runtime | Playback is cancelled; the new turn is handled |
| G Human transfer | Escalation | Deterministic transfer state (simulated without a transfer number) |

Synthetic identities and DOBs are shown on each scenario card. Walkthrough:
[docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md).

## Security and privacy

- **`OPERATOR_TOKEN` is server-only.**
  - Two Next.js route handlers add it for phone-session detail and Start Call.
  - The browser never sends, receives or stores it.
  - A CI step builds with a canary token and scans the client bundle.
- **Railway stays authenticated.** `/api/operator/calls` and phone-session detail reject requests without
  the bearer token.
- **Start Call guards:**
  - same-origin JSON only;
  - fields validated (E.164, scenario, language);
  - duplicate-call and rate guards;
  - the backend's allowlist (`DEMO_CALL_ALLOWED_NUMBERS`), hourly limit and contact policy decide.
- **Twilio:** webhook signatures are validated; the media WebSocket requires a per-call HMAC token;
  telephony routes return 404 unless telephony is configured.
- **Contact numbers** where someone asked to stop are stored as a SHA-256 key with a fixed application salt
  plus a masked label (`+81•••••••678`), not in clear. This is pseudonymisation, not strong protection:
  phone numbers are guessable.
- **Data:** synthetic only. Transcript text expires after `TRANSCRIPT_RETENTION_DAYS` (default 30); raw
  audio is never stored. JSON logs redact token-like values.
- **POC access model:** the public console has no login, so anyone with the URL can view audit detail and
  call allowlisted numbers. A production deployment needs real access control, for example SSO or Vercel
  Deployment Protection (see DECISIONS.md D14).

## Known limitations

- Simulated demo policy, not Japanese legal compliance. No claim of production collections readiness.
- Human transfer is simulated unless `TWILIO_TRANSFER_NUMBER` is configured.
- Latency is not consistently below 1.5 s (median 1.62 s over 31 PSTN turns). These are a few calls, not a
  benchmark.
- Model and provider choices are tuned for POC cost and speed.
- Voice activity detection is a simple energy VAD. Background noise in the browser can trigger or delay
  barge-in and turn-taking; headphones help.
- Demo rate limits and the allowlist are intentionally restrictive; the in-process session state requires a
  single replica.
- Japanese has not been reviewed by a native speaker. Post-training covers the interpreter only, on
  synthetic data ([results](docs/POST_TRAINING_RESULTS.md)); the fine-tuned adapter is not the production
  interpreter yet because of the serving latency finding.

## Measured latency (detail)

| Measurement | Result | Conditions |
|---|---|---|
| PSTN voice turns, speech end → first agent audio | median 1.62 s; 1.15–4.67 s; 14 of 31 under 1.5 s | 31 turns, 6 real calls, 2026-09-26 |
| PSTN stages | STT final ≈320 ms, NLU median 905 ms, TTS first audio median 112 ms | same 31 turns |
| PSTN barge-in, internal cancel (detected → TTS stopped) | 0.86–1.39 ms, n=7 | Excludes VAD detection window, network and Twilio buffer clear |
| Browser voice turn, speech end → first audio | ≈1.43 s, n=1 | Deployed backend; the agent's own TTS audio was replayed as the caller |
| Barge-in cancel path, in-process | p50 0.27 ms, p95 0.50 ms, n=50 | Mock TTS, dev container |
| Typed turn → first audio frame | 2–8 ms | Mock providers; pipeline overhead only |

These figures come from a small number of calls from one location; they are not a benchmark.
`/api/metrics/latency` and the session pages report p50/p95 per provider mode from real sessions.

## Run locally

Requirements: Python 3.12+, Node 22+. No API keys and no database server are needed: SQLite and mock
providers, for development only.

```bash
cd backend && python3.12 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
uvicorn app.main:_app_factory --factory --reload --port 8000

cd frontend && npm ci
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 npm run dev   # http://localhost:3000
```

Full stack with PostgreSQL: `docker compose up --build`.

Checks:

- **Backend:** `ruff check app tests alembic && mypy app && pytest -q && python -m app.evaluation`
- **Frontend:** `npm run lint && npm run typecheck && npm test && npm run check:bundle`

## Deployment configuration

Every variable is documented in [.env.example](.env.example); step-by-step in
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) and [docs/TELEPHONY.md](docs/TELEPHONY.md).

**Backend (Railway)**

- `APP_ENV=production`, `FRONTEND_URL` set to the exact Vercel origin, and
  `DATABASE_URL=${{Postgres.DATABASE_URL}}` from a Railway PostgreSQL service.
- **Durable persistence:** the backend uses PostgreSQL for sessions, transcripts, audit, latency,
  promises and stop-contact suppression, so they survive redeploys. The SQLite fallback is development-only.
  Production/staging refuse to start without PostgreSQL; `ALLOW_EPHEMERAL_DATABASE` is an emergency override,
  not for normal use. `/ready` reports `backend`, `durable` and the migration `revision`.
- Providers:
  - `LLM_PROVIDER=cloudflare` with `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN`;
  - `STT_PROVIDER=cartesia` and `TTS_PROVIDER=cartesia` with `CARTESIA_API_KEY`, `CARTESIA_VOICE_ID` and
    `CARTESIA_VOICE_ID_JA`.
- Telephony: `TELEPHONY_ENABLED=true`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER`,
  `TWILIO_WEBHOOK_BASE_URL`, `DEMO_CALL_ALLOWED_NUMBERS`, `OPERATOR_TOKEN`, and optionally
  `TWILIO_TRANSFER_NUMBER`.
- Policy: `POLICY_COUNTRY`, `POLICY_TIMEZONE`, `POLICY_CALLING_START_HOUR`, `POLICY_CALLING_END_HOUR`.

**Frontend (Vercel)**

- `NEXT_PUBLIC_API_BASE_URL` (public; the backend URL).
- `OPERATOR_TOKEN` (server-only; same value as Railway, never `NEXT_PUBLIC_`).
- Optionally `BACKEND_API_BASE_URL`.

**Twilio**

- `TWILIO_WEBHOOK_BASE_URL` must equal the public backend URL exactly, because signatures depend on it.
- Outbound calls get their voice and status callbacks automatically.
- For inbound calls, point the number's Voice webhook to `POST {base}/telephony/twilio/voice`.
- Trial accounts can only call verified numbers.

## Project structure

```
backend/app/domain/       controller, policy, allowed-action contract, typed commands, rules NLU, responses + guard
backend/app/voice/        session runtime, VAD, end-of-turn, lifecycle, latency
backend/app/providers/    interfaces, mocks, Cloudflare, Cartesia, Twilio
backend/app/api/          REST, browser WebSocket, Twilio webhooks + media stream
backend/app/persistence/  schema, repository, DB recorder        backend/alembic/  migrations
backend/app/evaluation/   32 scenario cases, runner, judges, NLU held-out benchmark
backend/app/training/     dataset generator, prompt format (train == serve)   backend/training/  LoRA trainer, notebook, data, reports
frontend/                 Next.js console: /demo, /telephony, /sessions, /evaluation, /architecture
docs/                     architecture, voice runtime, policy, evaluation, telephony, deployment, decisions
```

**Not built (production evolution):**

- model-based VAD and turn detection;
- streaming LLM → sentence-level TTS;
- a live benchmark with p95 targets;
- Redis-backed sessions for multiple replicas;
- warm human hand-off;
- real SMS/e-mail providers;
- counsel-reviewed consent and recording disclosures;
- native-speaker review;
- self-hosted serving of the post-trained interpreter ([results](docs/POST_TRAINING_RESULTS.md), [plan](docs/POST_TRAINING_PLAN.md)).

Design decisions and trade-offs: [docs/DECISIONS.md](docs/DECISIONS.md).
