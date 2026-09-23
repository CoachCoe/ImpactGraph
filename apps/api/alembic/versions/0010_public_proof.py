"""Publishing a claim, and not publishing the people behind it.

An anonymous reader could already see a named individual donor in the provenance graph.
Proof pages put that in front of a much larger audience, so the name of a private
individual is withheld unless that individual asked for it to be shown. Existing rows
default to individual and unpublished, because the careful answer is the one to assume
about people whose preference was never recorded.

A claim is published by opting in, and `published_at` is never cleared: a page that can
be withdrawn once the verdict turns inconvenient is not a record of anything.
"""
import sqlalchemy as sa

from alembic import op

revision = "0010_public_proof"
down_revision = "0009_data_protection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "funding",
        sa.Column(
            "funder_is_organisation", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column(
        "funding",
        sa.Column(
            "publish_funder_name", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column("funding", sa.Column("name_consent_token_hash", sa.String(64), nullable=True))
    op.add_column("claims", sa.Column("published_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("claims", "published_at")
    op.drop_column("funding", "name_consent_token_hash")
    op.drop_column("funding", "publish_funder_name")
    op.drop_column("funding", "funder_is_organisation")
