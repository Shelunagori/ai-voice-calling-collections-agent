"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-09-26 13:08:07.664709
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:

    op.create_table(
        "debtors",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("full_name", sa.String(length=120), nullable=False),
        sa.Column("display_name_ja", sa.String(length=120), server_default="", nullable=False),
        sa.Column("name_aliases", sa.JSON(), nullable=False),
        sa.Column("date_of_birth", sa.Date(), nullable=False),
        sa.Column("phone_e164", sa.String(length=20), nullable=False),
        sa.Column("preferred_language", sa.String(length=5), nullable=False),
        sa.Column("timezone", sa.String(length=40), nullable=False),
        sa.Column("synthetic", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_debtors")),
    )
    op.create_table(
        "evaluation_cases",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("language", sa.String(length=5), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_evaluation_cases")),
    )
    op.create_table(
        "evaluation_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("llm_provider", sa.String(length=32), nullable=False),
        sa.Column("llm_model", sa.String(length=120), nullable=False),
        sa.Column("judge_provider", sa.String(length=32), nullable=False),
        sa.Column("judge_model", sa.String(length=120), nullable=False),
        sa.Column("judge_prompt_version", sa.String(length=32), nullable=False),
        sa.Column("code_version", sa.String(length=64), nullable=False),
        sa.Column("total", sa.Integer(), server_default="0", nullable=False),
        sa.Column("passed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluation_runs")),
    )
    op.create_table(
        "telephony_webhooks",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("call_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_telephony_webhooks")),
    )
    with op.batch_alter_table("telephony_webhooks", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_telephony_webhooks_call_id"), ["call_id"], unique=False)

    op.create_table(
        "collection_accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("debtor_id", sa.Uuid(), nullable=False),
        sa.Column("scenario_key", sa.String(length=8), nullable=False),
        sa.Column("creditor_name", sa.String(length=120), nullable=False),
        sa.Column("outstanding_balance", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("allowed_min_payment", sa.BigInteger(), nullable=False),
        sa.Column("max_extension_days", sa.Integer(), nullable=False),
        sa.Column("discount_authority", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("contact_attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("stop_contact", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("stop_contact_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["debtor_id"], ["debtors.id"], name=op.f("fk_collection_accounts_debtor_id_debtors")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_collection_accounts")),
        sa.UniqueConstraint("scenario_key", name=op.f("uq_collection_accounts_scenario_key")),
    )
    with op.batch_alter_table("collection_accounts", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_collection_accounts_debtor_id"), ["debtor_id"], unique=False)

    op.create_table(
        "evaluation_results",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("case_key", sa.String(length=64), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("invariants", sa.JSON(), nullable=False),
        sa.Column("judge", sa.JSON(), nullable=True),
        sa.Column("transcript", sa.JSON(), nullable=False),
        sa.Column("latency", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_key"], ["evaluation_cases.key"], name=op.f("fk_evaluation_results_case_key_evaluation_cases")
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["evaluation_runs.id"],
            name=op.f("fk_evaluation_results_run_id_evaluation_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluation_results")),
    )
    with op.batch_alter_table("evaluation_results", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_evaluation_results_run_id"), ["run_id"], unique=False)

    op.create_table(
        "voice_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("debtor_id", sa.Uuid(), nullable=False),
        sa.Column("scenario_key", sa.String(length=8), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("language", sa.String(length=5), nullable=False),
        sa.Column("input_mode", sa.String(length=16), nullable=False),
        sa.Column("call_id", sa.String(length=64), nullable=True),
        sa.Column("call_status", sa.String(length=32), nullable=False),
        sa.Column("voice_state", sa.String(length=32), nullable=False),
        sa.Column("identity_status", sa.String(length=32), nullable=False),
        sa.Column("promise_status", sa.String(length=32), nullable=False),
        sa.Column("stop_contact", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("human_transfer_requested", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("transfer_status", sa.String(length=32), server_default="NONE", nullable=False),
        sa.Column("ended_reason", sa.String(length=64), nullable=True),
        sa.Column("providers", sa.JSON(), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("transcript_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"], ["collection_accounts.id"], name=op.f("fk_voice_sessions_account_id_collection_accounts")
        ),
        sa.ForeignKeyConstraint(["debtor_id"], ["debtors.id"], name=op.f("fk_voice_sessions_debtor_id_debtors")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_voice_sessions")),
    )
    with op.batch_alter_table("voice_sessions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_voice_sessions_account_id"), ["account_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_voice_sessions_call_id"), ["call_id"], unique=False)

    op.create_table(
        "call_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=48), nullable=False),
        sa.Column("turn_index", sa.Integer(), nullable=True),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["voice_sessions.id"],
            name=op.f("fk_call_events_session_id_voice_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_call_events")),
    )
    with op.batch_alter_table("call_events", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_call_events_session_id"), ["session_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_call_events_type"), ["type"], unique=False)

    op.create_table(
        "conversation_turns",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("speaker", sa.String(length=8), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("spoken_text", sa.Text(), nullable=True),
        sa.Column("interrupted", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("acts", sa.String(length=200), nullable=True),
        sa.Column("controller_turn", sa.Integer(), nullable=True),
        sa.Column("interpretation", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["voice_sessions.id"],
            name=op.f("fk_conversation_turns_session_id_voice_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversation_turns")),
        sa.UniqueConstraint("session_id", "seq", "speaker", name="uq_turn_session_seq_speaker"),
    )
    op.create_table(
        "payment_promises",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("confirmation_turn", sa.Integer(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("policy_decision_ids", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="CONFIRMED", nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"], ["collection_accounts.id"], name=op.f("fk_payment_promises_account_id_collection_accounts")
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["voice_sessions.id"], name=op.f("fk_payment_promises_session_id_voice_sessions")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payment_promises")),
        sa.UniqueConstraint("session_id", name=op.f("uq_payment_promises_session_id")),
    )
    with op.batch_alter_table("payment_promises", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_payment_promises_account_id"), ["account_id"], unique=False)

    op.create_table(
        "policy_decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("rule", sa.String(length=64), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("turn_index", sa.Integer(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["voice_sessions.id"],
            name=op.f("fk_policy_decisions_session_id_voice_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_policy_decisions")),
    )
    with op.batch_alter_table("policy_decisions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_policy_decisions_rule"), ["rule"], unique=False)
        batch_op.create_index(batch_op.f("ix_policy_decisions_session_id"), ["session_id"], unique=False)

    op.create_table(
        "turn_latencies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("turn_index", sa.Integer(), nullable=False),
        sa.Column("input_mode", sa.String(length=16), nullable=False),
        sa.Column("provider_mode", sa.String(length=64), nullable=False),
        sa.Column("stages", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["voice_sessions.id"],
            name=op.f("fk_turn_latencies_session_id_voice_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_turn_latencies")),
    )
    with op.batch_alter_table("turn_latencies", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_turn_latencies_provider_mode"), ["provider_mode"], unique=False)
        batch_op.create_index(batch_op.f("ix_turn_latencies_session_id"), ["session_id"], unique=False)


def downgrade() -> None:

    with op.batch_alter_table("turn_latencies", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_turn_latencies_session_id"))
        batch_op.drop_index(batch_op.f("ix_turn_latencies_provider_mode"))

    op.drop_table("turn_latencies")
    with op.batch_alter_table("policy_decisions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_policy_decisions_session_id"))
        batch_op.drop_index(batch_op.f("ix_policy_decisions_rule"))

    op.drop_table("policy_decisions")
    with op.batch_alter_table("payment_promises", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_payment_promises_account_id"))

    op.drop_table("payment_promises")
    op.drop_table("conversation_turns")
    with op.batch_alter_table("call_events", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_call_events_type"))
        batch_op.drop_index(batch_op.f("ix_call_events_session_id"))

    op.drop_table("call_events")
    with op.batch_alter_table("voice_sessions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_voice_sessions_call_id"))
        batch_op.drop_index(batch_op.f("ix_voice_sessions_account_id"))

    op.drop_table("voice_sessions")
    with op.batch_alter_table("evaluation_results", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_evaluation_results_run_id"))

    op.drop_table("evaluation_results")
    with op.batch_alter_table("collection_accounts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_collection_accounts_debtor_id"))

    op.drop_table("collection_accounts")
    with op.batch_alter_table("telephony_webhooks", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_telephony_webhooks_call_id"))

    op.drop_table("telephony_webhooks")
    op.drop_table("evaluation_runs")
    op.drop_table("evaluation_cases")
    op.drop_table("debtors")
