"""A bank connection held on an organisation's behalf.

The first real secret this platform keeps for a customer. The refresh token is stored
sealed under the same key-encryption key that protects evidence at rest, so there is one
key to rotate and one place to lose rather than two.

Scopes are recorded rather than assumed, so a credential that should not be able to move
money can be shown not to be able to.
"""
import sqlalchemy as sa

from alembic import op

revision = "0012_provider_credentials"
down_revision = "0011_settlement_and_rates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_credentials",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("organization_ref", sa.String(160), nullable=False),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("sealed_refresh_token", sa.LargeBinary(), nullable=False),
        sa.Column("scopes", sa.String(400), nullable=False, server_default=""),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_provider_credentials_organization", "provider_credentials", ["organization_ref"]
    )
    op.create_index("ix_provider_credentials_provider", "provider_credentials", ["provider"])
    op.create_unique_constraint(
        "uq_provider_credential_org_provider",
        "provider_credentials",
        ["organization_ref", "provider"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_provider_credential_org_provider", "provider_credentials")
    op.drop_index("ix_provider_credentials_provider", table_name="provider_credentials")
    op.drop_index("ix_provider_credentials_organization", table_name="provider_credentials")
    op.drop_table("provider_credentials")
