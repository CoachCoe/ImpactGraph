"""Index the question the outbox worker actually asks.

`processed_at IS NULL ORDER BY created_at` runs on every worker tick, and the metrics
scrape asks the same thing. Neither was indexed, so both scanned the whole table and got
slower with every row the system had ever sent.

Partial rather than a plain index on processed_at: only unsubmitted rows are ever looked
for, so this stays the size of the backlog instead of the size of the history.
"""
import sqlalchemy as sa

from alembic import op

revision = "0005_outbox_pending_index"
down_revision = "0004_verification_threshold"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_outbox_pending",
        "outbox",
        ["created_at"],
        postgresql_where=sa.text("processed_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_pending", table_name="outbox")
