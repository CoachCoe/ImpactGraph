"""Asking the people a claim describes whether it happened.

Holding as little as possible about them. A list of aid recipients tied to deliveries is
a targeting aid in a conflict or a hostile administrative setting, so the number is
sealed, the lookup is by salted hash, and no route returns either.

Responses are structured rather than free text: a structured answer can be counted
without being read, and a free-text one cannot be published without exposing whoever
wrote it.
"""
import sqlalchemy as sa

from alembic import op

revision = "0013_beneficiary_confirmation"
down_revision = "0012_provider_credentials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "beneficiary_contacts",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("program_ref", sa.String(160), nullable=False),
        sa.Column("contact_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("sealed_contact", sa.LargeBinary(), nullable=False),
        sa.Column("household_ref", sa.String(160), nullable=False, server_default=""),
        sa.Column("language", sa.String(8), nullable=False, server_default="en"),
        sa.Column("opted_out_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_beneficiary_contacts_program", "beneficiary_contacts", ["program_ref"])
    op.create_index("ix_beneficiary_contacts_hash", "beneficiary_contacts", ["contact_hash"])

    op.create_table(
        "confirmation_rounds",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("external_id", sa.String(160), nullable=False, unique=True),
        sa.Column("delivery_ref", sa.String(160), nullable=False),
        sa.Column("program_ref", sa.String(160), nullable=False),
        sa.Column("seed", sa.String(160), nullable=False),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("population", sa.Integer(), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_confirmation_rounds_delivery", "confirmation_rounds", ["delivery_ref"])
    op.create_index("ix_confirmation_rounds_program", "confirmation_rounds", ["program_ref"])

    op.create_table(
        "confirmation_responses",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("round_ref", sa.String(160), nullable=False),
        sa.Column("contact_hash", sa.String(64), nullable=False),
        sa.Column("answer", sa.String(16), nullable=False),
        sa.Column(
            "responded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("round_ref", "contact_hash", name="uq_confirmation_one_per_person"),
    )
    op.create_index("ix_confirmation_responses_round", "confirmation_responses", ["round_ref"])
    op.create_index("ix_confirmation_responses_hash", "confirmation_responses", ["contact_hash"])
    op.create_index("ix_confirmation_responses_answer", "confirmation_responses", ["answer"])


def downgrade() -> None:
    op.drop_table("confirmation_responses")
    op.drop_table("confirmation_rounds")
    op.drop_table("beneficiary_contacts")
