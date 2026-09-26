# Policy engine (simulated demo policy)

> These rules are **demo policy rules inspired by regulated collections workflows**. They are not a
> statement of Japanese law (e.g. the Money Lending Business Act or the Servicer Act), have not been reviewed
> by counsel, and are not certified. Real deployments need rules authored and approved by compliance.

`app/domain/policy.py` is pure and deterministic. Every evaluation returns a `PolicyDecision`
(`rule`, `decision` = ALLOW | BLOCK | NOT_APPLICABLE, `reason`, `details`, `timestamp`, `session_id`,
`decision_id`) and the controller writes each one to the audit log and `policy_decisions` table.

```json
{"rule": "IDENTITY_REQUIRED_BEFORE_DISCLOSURE", "decision": "BLOCK",
 "reason": "identity status is UNVERIFIED; account details may not be disclosed",
 "timestamp": "2026-10-01T01:00:00+00:00", "session_id": "…"}
```

| Rule | When evaluated | Effect |
|---|---|---|
| `DEMO_CALLING_HOURS_WINDOW` | before an outbound call | with `POLICY_COUNTRY=JP` (default): block outside `POLICY_CALLING_START_HOUR`–`POLICY_CALLING_END_HOUR` in `POLICY_TIMEZONE` (08:00–21:00 Asia/Tokyo). Any other or empty `POLICY_COUNTRY`: `NOT_APPLICABLE` (never a silent ALLOW under the Japanese window). Always `NOT_APPLICABLE` for reviewer-initiated browser sessions |
| `MAX_CONTACT_ATTEMPTS` | before an outbound call | block at ≥ 3 attempts (configurable); runs for every `POLICY_COUNTRY` |
| `STOP_CONTACT_BLOCKS_CONTACT` | before an outbound call | block if the account, **its debtor**, or **the destination number (contact point)** has an active stop-contact request; `details.scopes` names which; runs for every `POLICY_COUNTRY` |
| `IDENTITY_REQUIRED_BEFORE_DISCLOSURE` | greeting, any request for details, every payment evaluation | disclosure only when `identity_status == VERIFIED` |
| `WRONG_PARTY_NO_DISCLOSURE` | caller says they are not the account holder | block disclosure, end politely |
| `IDENTITY_ATTEMPT_LIMIT` | failed date-of-birth check | after 2 failures → `FAILED`, end call |
| `PAYMENT_MIN_AMOUNT` | proposal | amount ≥ approved minimum |
| `PAYMENT_NOT_ABOVE_BALANCE` | proposal | amount ≤ outstanding balance |
| `PAYMENT_DATE_WITHIN_MAX_EXTENSION` | proposal | today ≤ due ≤ today + max extension (debtor's timezone) |
| `NO_DISCOUNT_AUTHORITY` | discount / waiver request | always block (agent has no authority) |
| `PROMISE_REQUIRES_EXPLICIT_CONFIRMATION` | confirmation | explicit affirm **to a read-back that was fully played** |
| `ONE_CONFIRMED_PROMISE_PER_SESSION` | any later proposal | block; also a DB unique constraint |
| `STOP_CONTACT_HONOURED` | caller asks for no contact (any phase, verified or not) | stop_contact = true, future_contact_eligible = false, end call; persisted for the debtor (all accounts) and, on phone calls, the contact point |
| `HUMAN_TRANSFER_ON_REQUEST` | caller asks for a person, disputes the debt, or 3 unclear turns | deterministic transfer state |
| Output guard (`responses.guard`) | every spoken reply | before verification: no amounts or debt vocabulary; after: only approved amounts/dates; never threats or waivers |

## Configuring the contact window

```env
POLICY_COUNTRY=JP              # apply the simulated window (default)
POLICY_TIMEZONE=Asia/Tokyo
POLICY_CALLING_START_HOUR=8
POLICY_CALLING_END_HOUR=21     # exclusive
```

Set `POLICY_COUNTRY=` (empty) on a local/test deployment where outbound demo calls should not be limited
by calling hours. The country is explicit configuration; it is never inferred from IP geolocation.

## Identity

Two steps: name confirmation ("Am I speaking with Haruto Sato?") then a knowledge factor (date of birth,
Gregorian or Japanese era forms). The LLM only *extracts* what the caller said; the controller compares it
with the synthetic record. Denial at the name step → `WRONG_PARTY`.

**Allowed-action contract** (`app/domain/turn_context.py`). The controller's phase defines which caller
actions are valid and which slot is expected. While a date of birth is expected, only `PROVIDE_DOB`,
`PARTIAL_DOB`, wrong-person / purpose / balance questions and the always-valid caller rights (stop contact,
human, goodbye) are accepted — never a payment amount, payment date or consent. The contract is enforced in
code at every layer: the LLM schema enum, the rules parser (identity phases never extract money; payment
phases never extract a DOB), the validation layer and again in `ConversationController.apply()`, which
drops and audits anything out of phase (`nlu.action_out_of_phase`).

**Partial dates of birth.** "1988" or "April 1988" is a `PARTIAL_DOB` with only the parts said; missing parts
are never filled in (no 1988-01-01). Parts are merged across turns and the agent asks only for what is
missing ("And what day in April?"; no digits are spoken before verification). Verification happens only
from a complete, real calendar date; an impossible date (April 31) is `identity.invalid_dob` and does not
count as an attempt. A complete LLM date keeps only the parts the transcript supports. Events:
`identity.partial_dob` (parts, missing, source, `llm_validation_failed`, notes), `identity.invalid_dob`,
`identity.failed`, `identity.verified`. Found on a real PSTN call (session `2fa211b9`), where "1988" had
been read as a ¥1,988 payment proposal by the unconstrained fallback.

## Promise-to-pay

Becomes `CONFIRMED` only when: identity verified; amount and date pass every rule; the read-back
("Just to confirm: you'll pay ¥30,000 on …. Is that correct?") was played (≥ 90 %); the caller explicitly
affirms; and the final confirmation gate re-runs all rules. Persisted with amount, currency, due date,
confirmation turn, timestamp and the ids of the policy decisions that approved it. Consent only answers the
read-back that was *just* asked: any other reply while confirmation is pending (e.g. the no-discount answer)
voids it, so a later "yes" re-triggers the read-back. A model "yes" never overrides an explicit "no" the
deterministic parser heard. Three affirmations without a heard read-back (e.g. TTS failure on a phone call)
escalate to a human instead of looping. Corrections while the
read-back is pending replace only the corrected field. A hang-up with a pending proposal never becomes a
promise.

## Caller-rights intents cannot be dropped

Stop-contact and human-transfer requests are detected by the deterministic parser in addition to the LLM;
either detection is honoured (`understanding.merge`). They take priority over every other branch.
