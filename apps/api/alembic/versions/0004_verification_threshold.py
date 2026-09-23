"""How many independent verifiers a program's claims require.

One verifier is one point of failure, and the registry has always allowed a claim to
carry several attestations. The default of 1 is what every existing program was already
doing implicitly, so no claim changes state when this lands.
"""
import sqlalchemy as sa

from alembic import op

revision = "0004_verification_threshold"
down_revision = "0003_unsigned_attestations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "programs",
        sa.Column(
            "verification_threshold",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )


def downgrade() -> None:
    op.drop_column("programs", "verification_threshold")
