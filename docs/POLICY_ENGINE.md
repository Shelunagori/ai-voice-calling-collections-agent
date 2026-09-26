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
| `DEMO_CALLING_HOURS_WINDOW` | before an outbound call | block outside 08:00–21:00 Asia/Tokyo (configurable); `NOT_APPLICABLE` for reviewer-initiated browser sessions |
| `MAX_CONTACT_ATTEMPTS` | before an outbound call | block at ≥ 3 attempts (configurable) |
| `STOP_CONTACT_BLOCKS_CONTACT` | before an outbound call | block if the account has a stop-contact flag |
| `IDENTITY_REQUIRED_BEFORE_DISCLOSURE` | greeting, any request for details, every payment evaluation | disclosure only when `identity_status == VERIFIED` |
| `WRONG_PARTY_NO_DISCLOSURE` | caller says they are not the account holder | block disclosure, end politely |
| `IDENTITY_ATTEMPT_LIMIT` | failed date-of-birth check | after 2 failures → `FAILED`, end call |
| `PAYMENT_MIN_AMOUNT` | proposal | amount ≥ approved minimum |
| `PAYMENT_NOT_ABOVE_BALANCE` | proposal | amount ≤ outstanding balance |
| `PAYMENT_DATE_WITHIN_MAX_EXTENSION` | proposal | today ≤ due ≤ today + max extension (debtor's timezone) |
| `NO_DISCOUNT_AUTHORITY` | discount / waiver request | always block (agent has no authority) |
| `PROMISE_REQUIRES_EXPLICIT_CONFIRMATION` | confirmation | explicit affirm **to a read-back that was fully played** |
| `ONE_CONFIRMED_PROMISE_PER_SESSION` | any later proposal | block; also a DB unique constraint |
| `STOP_CONTACT_HONOURED` | caller asks for no contact (any phase, verified or not) | stop_contact = true, future_contact_eligible = false, end call; account flag persisted |
| `HUMAN_TRANSFER_ON_REQUEST` | caller asks for a person, disputes the debt, or 3 unclear turns | deterministic transfer state |
| Output guard (`responses.guard`) | every spoken reply | before verification: no amounts or debt vocabulary; after: only approved amounts/dates; never threats or waivers |

## Identity

Two steps: name confirmation ("Am I speaking with Haruto Sato?") then a knowledge factor (date of birth,
Gregorian or Japanese era forms). The LLM only *extracts* what the caller said; the controller compares it
with the synthetic record. Denial at the name step → `WRONG_PARTY`.

## Promise-to-pay

Becomes `CONFIRMED` only when: identity verified; amount and date pass every rule; the read-back
("Just to confirm: you'll pay ¥30,000 on …. Is that correct?") was played (≥ 90 %); the caller explicitly
affirms; and the final confirmation gate re-runs all rules. Persisted with amount, currency, due date,
confirmation turn, timestamp and the ids of the policy decisions that approved it. Corrections while the
read-back is pending replace only the corrected field. A hang-up with a pending proposal never becomes a
promise.

## Caller-rights intents cannot be dropped

Stop-contact and human-transfer requests are detected by the deterministic parser in addition to the LLM;
either detection is honoured (`understanding.merge`). They take priority over every other branch.
