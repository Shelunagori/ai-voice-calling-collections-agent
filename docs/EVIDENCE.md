# Evidence log (internal)

The recruiter-facing claims in the README come from real PSTN session exports taken from the deployed stack
(`/api/sessions/{id}`; Twilio Media Streams, Cartesia `ink-whisper` / `sonic-3`, Cloudflare
`llama-3.3-70b`). The exports are **not committed**: they contain Twilio call SIDs and internal ids. They are
referenced here by session-id prefix only. All identities and accounts are synthetic, and the
calling-window rule was `NOT_APPLICABLE` (`POLICY_COUNTRY=IN`) on these calls.

| Session | Scenario | Outcome | Used as evidence for |
|---|---|---|---|
| `2fa211b9` | A | `VERIFIED`, `caller_hangup` | The original partial-DOB bug ("1988" read as a payment; 1988-01-01 padding); first latency sample (7 turns); 2 barge-ins. Build **before** the partial-DOB fix |
| `babb58a4` | A | `VERIFIED`, promise `CONFIRMED`, `completed_with_promise` | Identity → partial DOB ("April 1988" → "And what day in April?") → verified → disclosure ¥80,000 → ¥50,000 due 2026-10-27 (min / balance / date-window ALLOW) → read-back delivered → "Yes" → `PROMISE_REQUIRES_EXPLICIT_CONFIRMATION` ALLOW → promise; 3 barge-ins (1.17 / 1.32 / 1.18 ms); 6 voice-turn latencies |
| `b726f944` | E | `VERIFIED`, `stop_contact_requested` | "Please don't call me again." → `STOP_CONTACT` (LLM) → `STOP_CONTACT_HONOURED`; `stop_contact=true`, `future_contact_eligible=false`; spoken acknowledgement. Does **not** show a later blocked call |
| `84f807f3` | E | `FAILED` (2 attempts), `identity_verification_failed` | Negative path: `debt_disclosed=false`, `disclosure_allowed=false`, `IDENTITY_ATTEMPT_LIMIT` BLOCK; DOB grounding notes (`llm_dob_field_not_in_transcript`, `dob_grounded_to_transcript`); 1 barge-in (1.11 ms) |
| `0aad3465` | E | `NAME_CONFIRMED`, `call_completed` (caller hung up) | Partial DOB retained (`year=1990, month=10, day=null`), questions only for missing parts, "3030" → `UNCLEAR`, no disclosure; 1 barge-in (1.39 ms) |
| `3bce735f` | G | `VERIFIED`, `TRANSFER_REQUESTED`, `transferred_to_human` | "I want to speak to a human." → `REQUEST_HUMAN` → `HUMAN_TRANSFER_ON_REQUEST` ALLOW → `transfer_status=SIMULATED` ("no live telephony/transfer number"). Not a live hand-off. The DOB turn used the rules fallback after the LLM failed (NLU 3,876 ms, end-to-end 4.67 s) |

## Aggregates (6 calls, 31 voice turns)

- Speech end → first agent audio: min 1,146 ms, median 1,623 ms, max 4,673 ms; 14 of 31 turns under 1.5 s.
- NLU: 594–1,262 ms (median 905 ms), plus the one 3,876 ms fallback turn.
- Cartesia TTS first audio: 99–178 ms (median 112 ms).
- STT final: ≈315–330 ms on most turns.
- Barge-in internal cancel (detected → TTS stopped): 0.86–1.39 ms, n=7. This excludes the VAD
  sustained-speech window, the network and Twilio's buffer clear.

## Not yet evidenced live

- A later outbound call blocked by debtor/contact-point stop-contact (covered by
  `backend/tests/test_stop_contact_scope.py` with a fake Twilio).
- Live human hand-off (needs `TWILIO_TRANSFER_NUMBER`).
- Scenarios B, C, D and F on PSTN; Japanese on PSTN.

## Open findings from these calls (not fixed)

1. **Split spoken years.** STT produced "October 19 90"; the rules parser read day=19 and grounding discarded
   the LLM's year (`84f807f3`). The result was safe but wrong. Two-digit or split years are not handled.
2. **Slow NLU turn.** One turn's NLU stage took 3,876 ms, above the default `LLM_TIMEOUT_S=2.5`, before the
   rules fallback answered (`3bce735f`). Whether the production value differs, or where the extra time was
   spent, has not been investigated.
