# Post-training plan (not performed)

**No SFT, DPO or RL has been performed for this POC.** This document describes how production call data
could safely improve the language components, and what would gate each step.

## What could be trained

Only the *language* parts: the utterance → typed-proposal interpreter (NLU), the phrasing of approved
replies, and possibly turn-taking classifiers. **Never** the authority layer: identity, disclosure,
payment approval, stop-contact and promise confirmation stay deterministic code, so a model regression cannot
become a compliance regression.

## Data pipeline

1. **Consent and governance.** Only calls where recording/processing consent was captured and the data
   agreement permits model improvement. Honour stop-contact, deletion and retention (transcripts already
   expire via `TRANSCRIPT_RETENTION_DAYS`). Legal review of the lawful basis under APPI (Japan) and any
   creditor contracts before any data leaves production.
2. **PII redaction** before export: names, dates of birth, phone numbers, addresses, account ids, amounts
   tied to identity → typed placeholders (`<NAME>`, `<DOB>`, `<AMOUNT_1>`), with a consistent per-call map so
   structure is preserved. Automated redaction (pattern + NER for EN/JA) followed by a sampled human audit;
   a call is excluded if any unredacted PII is found in the sample.
3. **Labels come from the system, not the model.** Each caller turn already has the controller's final
   state transitions and policy outcomes. Gold NLU labels = the proposal that, after human review, should
   have been produced; corrections from operators (e.g. after a transfer) are the richest source.
4. **Splits without leakage.** Split by *debtor/account* and by *time* (train on weeks ≤ T, evaluate on
   > T), never by turn, so near-duplicate calls from one person never straddle train/eval. Dedupe with
   normalised-text hashing. Keep a frozen, never-trained **holdout** set plus the synthetic regression suite.

## Dataset types

- **Evaluation sets** (first, always): curated turns per category (amounts, dates, corrections, JA numerals,
  noisy ASR, stop-contact phrasings, disputes) with gold proposals. Extends `app/evaluation/cases.py`.
- **SFT**: (context, utterance) → gold `Interpretation` JSON; (plan facts, template) → reviewer-approved
  natural phrasing that passes the output guard.
- **Preference pairs (DPO)**: for phrasing, pairs where reviewers prefer one of two guard-passing replies
  (clarity, empathy, brevity); for NLU, chosen = gold proposal, rejected = observed wrong proposal.
- **Reward / evaluation signals**: deterministic invariant pass rate (hard gate, not a reward to trade off),
  NLU exact-match on amount/date/intent, read-back correction rate, transfer and repeat-contact rates,
  promise-kept rate (lagging, confounded — use for monitoring, not direct optimisation), judge scores.

## Before/after evaluation and rollout

- Offline: holdout NLU accuracy by category and language, guard violation rate (must be 0), synthetic suite
  (must stay 100 %), judge scores with human spot checks.
- Shadow: run the candidate model alongside production on live traffic, compare proposals, no effect on
  calls.
- Canary: small traffic share with automatic rollback.

**Rollback criteria** (any one): any invariant/guard failure; stop-contact or human-request recall below the
current model; NLU accuracy regression beyond the agreed margin in any language/category; increase in
read-back corrections or transfers; latency p95 regression beyond budget.

## Optional synthetic experiment

Not included. A small synthetic SFT experiment would only demonstrate mechanics and would be labelled as
synthetic research, not production evidence; the real-time system was prioritised.
