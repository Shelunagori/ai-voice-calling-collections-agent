# Voice runtime

`app/voice/session.py` — one `VoiceSession` per conversation, shared by browser, phone and evaluation.

## Lifecycle

States: `IDLE, LISTENING, USER_SPEAKING, PROCESSING, AGENT_SPEAKING, INTERRUPTED, TRANSFER_REQUESTED, ENDED, ERROR`.
Allowed transitions are an explicit table in `app/voice/lifecycle.py`; anything else raises
`IllegalTransition`. Each transition records `(from, to, cause, monotonic time)` and is streamed to the UI.

Typical path: `IDLE → PROCESSING (greeting) → AGENT_SPEAKING → LISTENING → USER_SPEAKING → PROCESSING →
AGENT_SPEAKING → INTERRUPTED → USER_SPEAKING → … → ENDED`.

## VAD vs end-of-turn (separate concerns)

**VAD** (`vad.py`) answers "is there voice energy now?": 20 ms frames, RMS dBFS, a minimum-statistics noise
floor over a 1 s window (stationary noise sets the floor; speech troughs do not), 12 dB SNR margin, 100 ms to
declare speech start (rejects clicks), 240 ms hang to declare speech end, 200 ms calibration at stream start.
Times are *audio* time so behaviour is identical in tests and production.

**End-of-turn** (`turn_detection.py`) answers "has the caller finished?", from silence since speech end and
the transcript so far:

| Situation | Required silence |
|---|---|
| short complete answer ("yes", "はい") on a final transcript | 250 ms |
| default | 600 ms |
| filler-only or trailing continuation ("and", "so", "えーと", "けど") | 1500 ms (thinking pause) |
| energy but no words after 1200 ms | turn discarded as noise (background speech, cough) |
| hard ceiling | 2500 ms |

When VAD reports speech end the runtime sends the STT stream a `finalize` so the provider flushes promptly.
With Cartesia `ink-whisper`, `max_silence_duration_secs` provides provider-side endpointing as well; the
semantic decision above still gates committing the turn. Tested in `tests/test_nlu_turns_lifecycle.py` and
the `voice_*` evaluation cases (noise, thinking pause, short answer, silence timeout).

Silence while `LISTENING` (voice mode) reprompts after 8 s, and ends the call after two reprompts.

## Barge-in

Trigger while `AGENT_SPEAKING`:
- VAD sustained voiced speech ≥ `BARGE_IN_MIN_SPEECH_MS` (default 250 ms), or
- a non-filler STT partial with ≥ 120 ms of voiced speech, or
- typed input / the *Interrupt* button (demo), same code path.

Sequence (`VoiceSession.barge_in`):
1. `barge_in_detected_at` — lifecycle → `INTERRUPTED`.
2. **Invalidate first:** `generation += 1`. The playback loop checks the generation before every send, so a
   chunk already in flight is dropped.
3. `tts_cancel_requested_at` — cancel the playback task. The provider stream is wrapped in
   `contextlib.aclosing`, so the Cartesia adapter immediately sends `{"context_id", "cancel": true}`.
4. Tell the transport to flush: browser `{"type":"audio.clear","generation":n}` (client stops scheduled
   buffers and ignores older generations), Twilio `{"event":"clear"}`.
5. `tts_stopped_at` — after the playback task has actually finished.
6. The agent turn is marked `interrupted` with the approximately played prefix; a `voice.barge_in` audit
   event stores all three timestamps. Structured state is untouched (it was committed before speaking).

**Read-back safety.** If the interrupted utterance was the promise read-back, `readback_delivered` stays
false; a "yes" spoken over it is treated as not-yet-consent and the read-back is repeated
(tests: `test_interrupted_readback_yes_does_not_confirm_promise`, eval `yes_over_readback`).

**Caller keeps talking before the reply starts.** If speech resumes while the turn is still in
understanding (no side effects yet), that task is cancelled and its text is merged into the continuing turn.
Once `apply()` has run, state is kept and the reply is interrupted normally.

**Playback pacing.** Audio is sent at real-time pace with a 250 ms lead, so the server knows what has
actually been played (±lead) and a clear only has to drop ≤ 250 ms of client buffer.

Measured (wall clock, mock TTS, dev container): detect → stopped p50 0.27 ms / p95 0.50 ms (n=50). This is the
in-process cancel path only; network RTT and client/Twilio buffer flush are not included.

## Latency instrumentation

`TurnLatency` marks (monotonic, captured when observed): `speech_end`, `final_transcript`, `turn_commit`,
`nlu_start/done`, `policy_done`, `response_first_token`, `response_ready`, `tts_request`,
`tts_first_audio`, `first_audio_sent`. Derived stages include `speech_end_to_final_transcript`,
`final_transcript_to_turn_commit`, `nlu`, `policy_apply`, `tts_request_to_first_audio`,
`speech_end_to_first_audio`. Stored per turn (`turn_latencies`) with the provider mode; `/api/metrics/latency`
returns p50/p95/count per provider mode; Prometheus exposes histograms.

### Latency budget

Target: speech end → first agent audio < 1.5 s. Contributions (design, **not measurements**):
- VAD hang (240 ms) + end-of-turn rule (250–600 ms, 1500 ms for thinking pauses) — tunable.
- STT finalize → final transcript — provider dependent.
- LLM understanding — one non-streaming JSON-mode call (JSON mode does not stream); bounded by
  `LLM_TIMEOUT_S` with rules fallback. Likely the largest variable cost.
- Response text — templates add ~0 ms; `RESPONSE_MODE=llm` adds a second LLM round trip (off by default).
- TTS time-to-first-audio — provider dependent.

Bottleneck mitigations available but not yet built: start TTS on the template while the LLM runs; skip the
LLM for short yes/no turns the rules parser classifies confidently; stream LLM → sentence-level TTS.
Real numbers will appear in the UI once credentials are configured; they have not been measured here.
