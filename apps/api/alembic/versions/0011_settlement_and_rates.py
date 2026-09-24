"""Whether a payment settled, and what rate converted it.

A bank reports a payment before it settles and can reverse it afterwards. Only a settled
payment is a fact about the world: a pending one has not happened yet, a reversed one did
not happen, and neither may support a claim. Existing rows are SETTLED because every one
of them came from a fixture that has no other state.

Conversion is recorded as the rate that produced it rather than as a converted number,
because a figure nobody can reproduce is not evidence of anything.
"""
import sqlalchemy as sa

from alembic import op

revision = "0011_settlement_and_rates"
down_revision = "0010_public_proof"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "financial_transactions",
        sa.Column("settlement", sa.String(16), nullable=False, server_default="SETTLED"),
    )
    op.create_index(
        "ix_financial_transactions_settlement", "financial_transactions", ["settlement"]
    )
    for column, type_ in (
        ("reversed_at", sa.DateTime(timezone=True)),
        ("rate_source", sa.String(80)),
        ("rate_numerator", sa.Integer()),
        ("rate_denominator", sa.Integer()),
        ("rate_observed_at", sa.DateTime(timezone=True)),
    ):
        op.add_column("financial_transactions", sa.Column(column, type_, nullable=True))


def downgrade() -> None:
    for column in (
        "rate_observed_at",
        "rate_denominator",
        "rate_numerator",
        "rate_source",
        "reversed_at",
    ):
        op.drop_column("financial_transactions", column)
    op.drop_index("ix_financial_transactions_settlement", table_name="financial_transactions")
    op.drop_column("financial_transactions", "settlement")
