"""Database schema (SQLAlchemy Core). Portable types: runs on PostgreSQL and SQLite.

Structured facts (voice_sessions.state, payment_promises, policy_decisions) are stored
separately from transcript text (conversation_turns), and transcripts carry an
explicit expiry. Raw audio is never persisted.
"""

from __future__ import annotations

import sqlalchemy as sa

metadata = sa.MetaData(
    naming_convention={
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)

TS = sa.DateTime(timezone=True)

debtors = sa.Table(
    "debtors",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("full_name", sa.String(120), nullable=False),
    sa.Column("display_name_ja", sa.String(120), nullable=False, server_default=""),
    sa.Column("name_aliases", sa.JSON, nullable=False),
    sa.Column("date_of_birth", sa.Date, nullable=False),
    sa.Column("phone_e164", sa.String(20), nullable=False),
    sa.Column("preferred_language", sa.String(5), nullable=False),
    sa.Column("timezone", sa.String(40), nullable=False),
    sa.Column("synthetic", sa.Boolean, nullable=False, server_default=sa.true()),
    # Stop-contact is a property of the person, not of one account: it gates every
    # account of this debtor (see domain/contact.py for the contact-point scope).
    sa.Column("stop_contact", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("stop_contact_at", TS, nullable=True),
    sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
)

# A phone number at which a real person answered (outbound destination / inbound caller).
# Only a salted hash and a masked label are stored, never the number itself.
contact_points = sa.Table(
    "contact_points",
    metadata,
    sa.Column("key", sa.String(64), primary_key=True),
    sa.Column("label", sa.String(24), nullable=False),
    sa.Column("stop_contact", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("stop_contact_at", TS, nullable=True),
    sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
)

collection_accounts = sa.Table(
    "collection_accounts",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("debtor_id", sa.Uuid, sa.ForeignKey("debtors.id"), nullable=False, index=True),
    sa.Column("scenario_key", sa.String(8), nullable=False, unique=True),
    sa.Column("creditor_name", sa.String(120), nullable=False),
    sa.Column("outstanding_balance", sa.BigInteger, nullable=False),
    sa.Column("currency", sa.String(3), nullable=False),
    sa.Column("allowed_min_payment", sa.BigInteger, nullable=False),
    sa.Column("max_extension_days", sa.Integer, nullable=False),
    sa.Column("discount_authority", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("contact_attempts", sa.Integer, nullable=False, server_default="0"),
    sa.Column("stop_contact", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("stop_contact_at", TS, nullable=True),
    sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
)

voice_sessions = sa.Table(
    "voice_sessions",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("account_id", sa.Uuid, sa.ForeignKey("collection_accounts.id"), nullable=False, index=True),
    sa.Column("debtor_id", sa.Uuid, sa.ForeignKey("debtors.id"), nullable=False),
    sa.Column("scenario_key", sa.String(8), nullable=False),
    sa.Column("channel", sa.String(16), nullable=False),
    sa.Column("language", sa.String(5), nullable=False),
    sa.Column("input_mode", sa.String(16), nullable=False),
    sa.Column("call_id", sa.String(64), nullable=True, index=True),
    sa.Column("call_status", sa.String(32), nullable=False),
    sa.Column("voice_state", sa.String(32), nullable=False),
    sa.Column("identity_status", sa.String(32), nullable=False),
    sa.Column("promise_status", sa.String(32), nullable=False),
    sa.Column("stop_contact", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("human_transfer_requested", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("transfer_status", sa.String(32), nullable=False, server_default="NONE"),
    sa.Column("ended_reason", sa.String(64), nullable=True),
    sa.Column("providers", sa.JSON, nullable=False),
    sa.Column("state", sa.JSON, nullable=False),
    sa.Column("summary", sa.JSON, nullable=True),
    sa.Column("started_at", TS, nullable=False),
    sa.Column("ended_at", TS, nullable=True),
    sa.Column("transcript_expires_at", TS, nullable=False),
)

conversation_turns = sa.Table(
    "conversation_turns",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("session_id", sa.Uuid, sa.ForeignKey("voice_sessions.id", ondelete="CASCADE"), nullable=False),
    sa.Column("seq", sa.Integer, nullable=False),
    sa.Column("speaker", sa.String(8), nullable=False),
    sa.Column("text", sa.Text, nullable=True),  # nulled when the retention window passes
    sa.Column("spoken_text", sa.Text, nullable=True),
    sa.Column("interrupted", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("acts", sa.String(200), nullable=True),
    sa.Column("controller_turn", sa.Integer, nullable=True),
    sa.Column("interpretation", sa.JSON, nullable=True),
    sa.Column("created_at", TS, nullable=False),
    sa.UniqueConstraint("session_id", "seq", "speaker", name="uq_turn_session_seq_speaker"),
)

payment_promises = sa.Table(
    "payment_promises",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    # One confirmed promise per session, enforced by the database as well as the policy.
    sa.Column("session_id", sa.Uuid, sa.ForeignKey("voice_sessions.id"), nullable=False, unique=True),
    sa.Column("account_id", sa.Uuid, sa.ForeignKey("collection_accounts.id"), nullable=False, index=True),
    sa.Column("amount", sa.BigInteger, nullable=False),
    sa.Column("currency", sa.String(3), nullable=False),
    sa.Column("due_date", sa.Date, nullable=False),
    sa.Column("confirmation_turn", sa.Integer, nullable=False),
    sa.Column("confirmed_at", TS, nullable=False),
    sa.Column("policy_decision_ids", sa.JSON, nullable=False),
    sa.Column("status", sa.String(16), nullable=False, server_default="CONFIRMED"),
)

policy_decisions = sa.Table(
    "policy_decisions",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("session_id", sa.Uuid, sa.ForeignKey("voice_sessions.id", ondelete="CASCADE"), nullable=True, index=True),
    sa.Column("rule", sa.String(64), nullable=False, index=True),
    sa.Column("decision", sa.String(16), nullable=False),
    sa.Column("reason", sa.Text, nullable=False),
    sa.Column("details", sa.JSON, nullable=False),
    sa.Column("turn_index", sa.Integer, nullable=True),
    sa.Column("decided_at", TS, nullable=False),
)

call_events = sa.Table(
    "call_events",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column(
        "session_id", sa.Uuid, sa.ForeignKey("voice_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    ),
    sa.Column("type", sa.String(48), nullable=False, index=True),
    sa.Column("turn_index", sa.Integer, nullable=True),
    sa.Column("data", sa.JSON, nullable=False),
    sa.Column("at", TS, nullable=False),
)

turn_latencies = sa.Table(
    "turn_latencies",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column(
        "session_id", sa.Uuid, sa.ForeignKey("voice_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    ),
    sa.Column("turn_index", sa.Integer, nullable=False),
    sa.Column("input_mode", sa.String(16), nullable=False),
    sa.Column("provider_mode", sa.String(64), nullable=False, index=True),
    sa.Column("stages", sa.JSON, nullable=False),
    sa.Column("created_at", TS, nullable=False),
)

telephony_webhooks = sa.Table(
    "telephony_webhooks",
    metadata,
    # Idempotency ledger for provider callbacks (Twilio retries deliveries).
    sa.Column("id", sa.String(128), primary_key=True),
    sa.Column("call_id", sa.String(64), nullable=False, index=True),
    sa.Column("kind", sa.String(32), nullable=False),
    sa.Column("payload", sa.JSON, nullable=False),
    sa.Column("received_at", TS, nullable=False),
)

evaluation_runs = sa.Table(
    "evaluation_runs",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("started_at", TS, nullable=False),
    sa.Column("finished_at", TS, nullable=True),
    sa.Column("llm_provider", sa.String(32), nullable=False),
    sa.Column("llm_model", sa.String(120), nullable=False),
    sa.Column("judge_provider", sa.String(32), nullable=False),
    sa.Column("judge_model", sa.String(120), nullable=False),
    sa.Column("judge_prompt_version", sa.String(32), nullable=False),
    sa.Column("code_version", sa.String(64), nullable=False),
    sa.Column("total", sa.Integer, nullable=False, server_default="0"),
    sa.Column("passed", sa.Integer, nullable=False, server_default="0"),
    sa.Column("summary", sa.JSON, nullable=False),
)

evaluation_cases = sa.Table(
    "evaluation_cases",
    metadata,
    sa.Column("key", sa.String(64), primary_key=True),
    sa.Column("title", sa.String(200), nullable=False),
    sa.Column("category", sa.String(64), nullable=False),
    sa.Column("language", sa.String(5), nullable=False),
    sa.Column("definition", sa.JSON, nullable=False),
)

evaluation_results = sa.Table(
    "evaluation_results",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("run_id", sa.Uuid, sa.ForeignKey("evaluation_runs.id", ondelete="CASCADE"), nullable=False, index=True),
    sa.Column("case_key", sa.String(64), sa.ForeignKey("evaluation_cases.key"), nullable=False),
    sa.Column("passed", sa.Boolean, nullable=False),
    sa.Column("invariants", sa.JSON, nullable=False),
    sa.Column("judge", sa.JSON, nullable=True),
    sa.Column("transcript", sa.JSON, nullable=False),
    sa.Column("latency", sa.JSON, nullable=False),
    sa.Column("created_at", TS, nullable=False),
)
