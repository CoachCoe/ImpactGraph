"""Tell someone when a claim they follow stops being true.

Detection already existed: `restate_claims_for` lowers a verified claim whose evidence no
longer matches its commitment. It reached a database row and nobody else, and tamper
detection that no one is told about is not detection.
"""
import sqlalchemy as sa

from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PGUUID

revision = "0006_notification_subscriptions"
down_revision = "0005_outbox_pending_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notification_subscriptions",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("claim_id", sa.String(length=160), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unsubscribed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("email", "claim_id", name="uq_subscription_email_claim"),
    )
    op.create_index(
        op.f("ix_notification_subscriptions_email"), "notification_subscriptions", ["email"]
    )
    op.create_index(
        op.f("ix_notification_subscriptions_claim_id"),
        "notification_subscriptions",
        ["claim_id"],
    )
    op.create_index(
        op.f("ix_notification_subscriptions_token_hash"),
        "notification_subscriptions",
        ["token_hash"],
        unique=True,
    )

    op.create_table(
        "notification_deliveries",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column(
            "subscription_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("notification_subscriptions.id"),
            nullable=False,
        ),
        sa.Column("event_key", sa.String(length=200), nullable=False),
        sa.Column("event_type", sa.String(length=48), nullable=False),
        sa.Column("claim_id", sa.String(length=160), nullable=False),
        sa.Column("transport", sa.String(length=48), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.String(length=200), nullable=True),
        sa.UniqueConstraint("subscription_id", "event_key", name="uq_delivery_once"),
    )
    op.create_index(
        op.f("ix_notification_deliveries_subscription_id"),
        "notification_deliveries",
        ["subscription_id"],
    )
    op.create_index(
        op.f("ix_notification_deliveries_event_key"), "notification_deliveries", ["event_key"]
    )
    op.create_index(
        op.f("ix_notification_deliveries_claim_id"), "notification_deliveries", ["claim_id"]
    )


def downgrade() -> None:
    op.drop_table("notification_deliveries")
    op.drop_table("notification_subscriptions")
