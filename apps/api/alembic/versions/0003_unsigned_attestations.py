"""An attestation that was never signed has no wallet.

The seeded operator attestation carried a placeholder wallet and transaction hash and was
marked CONFIRMED, so it asserted an onchain signature that never happened and satisfied a
verification requirement on the strength of it. The honest record has neither, which the
NOT NULL constraint on issuer_wallet previously made impossible to store.
"""
import sqlalchemy as sa

from alembic import op

revision = "0003_unsigned_attestations"
down_revision = "0002_financial_entities"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "attestations",
        "issuer_wallet",
        existing_type=sa.String(length=42),
        nullable=True,
    )


def downgrade() -> None:
    # Rows written without a wallet cannot be represented under the old constraint.
    op.execute("DELETE FROM attestations WHERE issuer_wallet IS NULL")
    op.alter_column(
        "attestations",
        "issuer_wallet",
        existing_type=sa.String(length=42),
        nullable=False,
    )
