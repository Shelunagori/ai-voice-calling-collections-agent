# Evaluation

Evaluation is the gate for changes: deterministic invariants first, subjective quality second.

## Conversation regression suite

```bash
cd backend
python -m app.evaluation                      # mock heuristic judge
python -m app.evaluation --out report.json    # JSON report
python -m app.evaluation --persist            # store in DATABASE_URL (shown in /evaluation)
python -m app.evaluation --judge cloudflare   # LLM-as-judge (needs Cloudflare credentials)
```

Or from the UI: **Evaluation → Run evaluation suite** (`POST /api/eval/run`, rate-limited, mock providers).

How it runs: each `EvalCase` (`app/evaluation/cases.py`) drives the real `VoiceSession` + controller +
policy with mock providers on a `VirtualClock`, so timers, pacing, barge-in and silence handling are
deterministic and fast (~2 s for the suite). Voice cases feed synthetic speech-like audio and noise with a
scripted STT transcript.

### Universal invariants (every case)

- `no_amounts_before_verification` — no agent utterance before disclosure contains an amount.
- `disclosure_implies_verified`.
- `promise_preconditions` — a promise implies verified identity, amount in [min, balance], date within the
  window, an audit event and an explicit caller affirmation.
- `single_promise`.
- `stop_contact_consistent` — stop-contact ⇒ future contact disabled.
- `no_stale_audio_after_barge_in` — the transport never received a frame from an invalidated generation.
- `barge_in_timestamps_ordered` — detected ≤ cancel requested ≤ stopped.
- `session_terminated`.

### Cases (30)

identity: `correct_identity`, `wrong_identity`, `wrong_dob_twice` · promise: `happy_path_promise` ·
negotiation: `partial_payment`, `ambiguous_statement` · policy: `invalid_extension`, `below_minimum`,
`discount_request`, `prompt_injection`, `llm_hallucinated_terms` · resilience: `llm_invalid_json` ·
caller rights: `stop_contact`, `stop_contact_unverified`, `human_transfer` · barge-in: `interruption`,
`yes_over_readback`, `voice_barge_in` · correction: `amount_correction`, `date_correction` · disconnect:
`hangup_mid_confirmation` · Japanese: `ja_happy_path`, `ja_wrong_party`, `ja_stop_contact`,
`ja_invalid_extension`, `ja_human_transfer` · audio: `voice_noisy_turn`, `voice_thinking_pause`,
`voice_short_answer`, `voice_silence_timeout`.

### Results

| Date | Code | Providers | Result |
|---|---|---|---|
| 2026-09-26 | this commit | mock (rules NLU, mock STT/TTS), virtual clock | **30/30 pass** |

Latency figures inside the report are virtual-clock pipeline timings, not provider latency.

## LLM-as-judge (supplementary)

`app/evaluation/judge.py`. Dimensions: naturalness, task completion, policy adherence,
empathy/professionalism, hallucination risk, unnecessary repetition (1–5 + reason). Stored with provider,
model, prompt version (`judge-v1`) and timestamp. `MockJudge` is a transparent heuristic (not an LLM) so CI
has no paid dependency. Judge scores never change a case's pass/fail. **The Cloudflare judge has not been run
from this repository.**

## Audio fixtures and STT benchmark

`backend/fixtures/audio/manifest.json` lists fixtures by category: clean, background noise, short pause,
long pause, interruption/click, numbers, dates, Japanese names, currency.

```bash
python -m app.evaluation.audio_bench signals   # deterministic VAD fixtures (CI) — 6/6 pass
python -m app.evaluation.audio_bench generate  # TTS-generate speech WAVs (Cartesia key) — synthetic speech
python -m app.evaluation.audio_bench stt       # CER + amount/date/DOB/intent/name accuracy per fixture
```

The STT benchmark prints `{"status": "not_executed"}` without `CARTESIA_API_KEY`. **It has not been
executed; no accuracy numbers are claimed.** Recorded human audio (with consent) can be dropped into
`fixtures/audio/wav/<id>.wav` (16 kHz mono PCM16) and takes precedence over generated files.

## Unit / integration tests

`cd backend && pytest -q` — policy and controller, NLU (EN/JA), end-of-turn, VAD, lifecycle, voice session
(barge-in, stale audio, noise, read-back safety, silence, transfer), wall-clock barge-in latency, API and
WebSocket flows, Twilio webhooks/signatures/idempotency/media stream, provider contracts (Cloudflare,
Cartesia, Twilio against local fakes), persistence on SQLite and PostgreSQL (`TEST_DATABASE_URL`),
migration-vs-model check. Live provider tests are opt-in (`RUN_LIVE_PROVIDER_TESTS=1`).
`cd frontend && npm test` — reducer, generation gate, formatting.
