"""stop-contact at debtor and contact-point scope

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-26

Adds debtors.stop_contact/_at and the contact_points table, and backfills the debtor
flag from any account that already has stop_contact set. Contact points cannot be
backfilled: the dialled number was never persisted.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("debtors", schema=None) as batch_op:
        batch_op.add_column(sa.Column("stop_contact", sa.Boolean(), server_default=sa.false(), nullable=False))
        batch_op.add_column(sa.Column("stop_contact_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "contact_points",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=24), nullable=False),
        sa.Column("stop_contact", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("stop_contact_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_contact_points")),
    )
    # Backfill: an account-level stop-contact becomes a debtor-level one.
    op.execute(
        """
        UPDATE debtors
        SET stop_contact = TRUE,
            stop_contact_at = (
                SELECT MIN(a.stop_contact_at) FROM collection_accounts a
                WHERE a.debtor_id = debtors.id AND a.stop_contact = TRUE
            )
        WHERE id IN (SELECT debtor_id FROM collection_accounts WHERE stop_contact = TRUE)
        """
    )


def downgrade() -> None:
    op.drop_table("contact_points")
    with op.batch_alter_table("debtors", schema=None) as batch_op:
        batch_op.drop_column("stop_contact_at")
        batch_op.drop_column("stop_contact")
