"""How an outcome figure was arrived at.

An outcome carried a metric, a value and a unit, so the one number a donor is asked to
accept was the only thing on the page with nothing behind it. The company states the
principle plainly: outcome measures are the hardest thing to publish honestly, so the
method is published alongside the result.

Existing rows get an empty method rather than an invented one. A blank is honest about
having nothing; a plausible-sounding default would be the system asserting something
nobody measured.
"""
import sqlalchemy as sa

from alembic import op

revision = "0007_outcome_method"
down_revision = "0006_notification_subscriptions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("outcomes", sa.Column("method", sa.String(length=400), nullable=False, server_default=""))
    op.add_column("outcomes", sa.Column("source", sa.String(length=240), nullable=False, server_default=""))
    op.add_column("outcomes", sa.Column("confidence_percent", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("outcomes", "confidence_percent")
    op.drop_column("outcomes", "source")
    op.drop_column("outcomes", "method")
