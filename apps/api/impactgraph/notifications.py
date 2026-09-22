"""Telling someone that a claim they follow stopped being true.

`restate_claims_for` has always been able to lower a verified claim whose evidence no
longer matches its commitment. It wrote that to a database row and told nobody, and
tamper detection nobody is told about is not detection.

Intent is persisted in the same transaction as the status change, through the existing
outbox, so a rollback cannot leave a message claiming something that did not happen. The
transport is a Protocol for the same reason the financial and AI providers are: a real
one implements it without anything above changing.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from .observability import logger
from .persistence import (
    NotificationDeliveryRecord,
    NotificationSubscriptionRecord,
    OutboxRecord,
)

log = logger("impactgraph.notifications")

NOTIFICATION_TOPIC_PREFIX = "notification."
CLAIM_STATUS_CHANGED = "notification.claim_status_changed"

#: What a reader is told, in the product's own words rather than a marketing paraphrase.
STATUS_HEADLINE = {
    "VERIFIED": "A claim you follow is now independently verified",
    "CHALLENGED": "A claim you follow is no longer verified",
    "REJECTED": "A claim you follow has been rejected",
    "REVOKED": "A claim you follow has been revoked",
    "SUPERSEDED": "A claim you follow has been superseded",
}


@dataclass(frozen=True)
class Message:
    to: str
    subject: str
    body: str
    manage_url_path: str


class NotificationTransport(Protocol):
    """Somewhere to send a message. A real adapter implements this and nothing above
    it changes."""

    name: str

    def send(self, message: Message) -> None: ...


class ConsoleNotificationTransport:
    """Writes the message to the log instead of sending it.

    The delivery pipeline is real -- subscriptions, deduplication, retries and the record
    of what was sent. Only the last step is not, and it says so rather than pretending.
    """

    name = "console"

    def send(self, message: Message) -> None:
        log.info(
            "notification.rendered",
            to=message.to,
            subject=message.subject,
            manage_path=message.manage_url_path,
        )


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def subscribe(session: Session, *, email: str, claim_id: str) -> tuple[str, bool]:
    """Record an interest in a claim. Returns the raw token, which is never stored.

    Re-subscribing an address that already follows this claim reissues its token rather
    than creating a second row, so a repeated request cannot be used to send repeated
    mail to an address that never asked.
    """
    normalized = email.strip().lower()
    existing = session.scalar(
        select(NotificationSubscriptionRecord).where(
            NotificationSubscriptionRecord.email == normalized,
            NotificationSubscriptionRecord.claim_id == claim_id,
        )
    )
    token = secrets.token_urlsafe(48)
    if existing is not None:
        existing.token_hash = token_hash(token)
        existing.unsubscribed_at = None
        return token, existing.confirmed_at is not None
    session.add(
        NotificationSubscriptionRecord(
            email=normalized, claim_id=claim_id, token_hash=token_hash(token)
        )
    )
    return token, False


def resolve_token(session: Session, token: str) -> NotificationSubscriptionRecord | None:
    if not token:
        return None
    return session.scalar(
        select(NotificationSubscriptionRecord).where(
            NotificationSubscriptionRecord.token_hash == token_hash(token)
        )
    )


def enqueue_claim_status_change(
    session: Session, *, claim_id: str, status: str, correlation_id: str
) -> None:
    """Persist the intent to tell followers, in the caller's transaction.

    Never sends here. A message posted inside a transaction that then rolls back has told
    someone about something that did not happen, and cannot be recalled.
    """
    session.add(
        OutboxRecord(
            topic=CLAIM_STATUS_CHANGED,
            payload={"claimId": claim_id, "status": status},
            correlation_id=correlation_id,
        )
    )


class NotificationDispatcher:
    """Drains notification intent from the outbox and records what it sent."""

    def __init__(self, *, session_factory, transport: NotificationTransport) -> None:
        self.session_factory = session_factory
        self.transport = transport

    def run_once(self, batch_size: int = 20) -> int:
        with self.session_factory() as session, session.begin():
            rows = list(
                session.scalars(
                    select(OutboxRecord)
                    .where(
                        OutboxRecord.processed_at.is_(None),
                        OutboxRecord.topic.startswith(NOTIFICATION_TOPIC_PREFIX),
                    )
                    .order_by(OutboxRecord.created_at)
                    .limit(batch_size)
                )
            )
            pending = [(row.id, row.topic, dict(row.payload)) for row in rows]

        sent = 0
        for outbox_id, topic, payload in pending:
            sent += self._dispatch(outbox_id, topic, payload)
        return sent

    def _dispatch(self, outbox_id, topic: str, payload: dict[str, Any]) -> int:
        if topic != CLAIM_STATUS_CHANGED:
            log.warning("notification.unknown_topic", topic=topic)
            return 0
        claim_id, status = payload["claimId"], payload["status"]
        with self.session_factory() as session, session.begin():
            subscribers = list(
                session.scalars(
                    select(NotificationSubscriptionRecord).where(
                        NotificationSubscriptionRecord.claim_id == claim_id,
                        NotificationSubscriptionRecord.confirmed_at.is_not(None),
                        NotificationSubscriptionRecord.unsubscribed_at.is_(None),
                    )
                )
            )
            targets = [(row.id, row.email) for row in subscribers]

        sent = 0
        for subscription_id, email in targets:
            if self._deliver_once(subscription_id, email, str(outbox_id), claim_id, status):
                sent += 1

        with self.session_factory() as session, session.begin():
            outbox = session.get(OutboxRecord, outbox_id)
            if outbox is not None:
                outbox.processed_at = datetime.now(UTC)
        return sent

    def _deliver_once(
        self, subscription_id, email: str, event_key: str, claim_id: str, status: str
    ) -> bool:
        """Claim the delivery before sending it.

        The unique constraint on (subscription, event) is what makes this safe to retry:
        a second attempt for the same change loses the insert and sends nothing.
        """
        from sqlalchemy.exc import IntegrityError

        try:
            with self.session_factory() as session, session.begin():
                session.add(
                    NotificationDeliveryRecord(
                        subscription_id=subscription_id,
                        event_key=event_key,
                        event_type=CLAIM_STATUS_CHANGED,
                        claim_id=claim_id,
                        transport=self.transport.name,
                    )
                )
        except IntegrityError:
            return False

        headline = STATUS_HEADLINE.get(status, f"A claim you follow is now {status}")
        try:
            self.transport.send(
                Message(
                    to=email,
                    subject=headline,
                    body=(
                        f"{headline}.\n\n"
                        f"Claim {claim_id} is now {status}. The requirement that changed, "
                        "and the evidence behind it, are on the claim page."
                    ),
                    manage_url_path=f"/notifications/manage?claim={claim_id}",
                )
            )
        except Exception as exc:  # noqa: BLE001 -- a failed send is recorded, not raised
            with self.session_factory() as session, session.begin():
                record = session.scalar(
                    select(NotificationDeliveryRecord).where(
                        NotificationDeliveryRecord.subscription_id == subscription_id,
                        NotificationDeliveryRecord.event_key == event_key,
                    )
                )
                if record is not None:
                    record.error = exc.__class__.__name__
            log.warning(
                "notification.send_failed",
                claim_id=claim_id,
                error_type=exc.__class__.__name__,
            )
            return False

        with self.session_factory() as session, session.begin():
            record = session.scalar(
                select(NotificationDeliveryRecord).where(
                    NotificationDeliveryRecord.subscription_id == subscription_id,
                    NotificationDeliveryRecord.event_key == event_key,
                )
            )
            if record is not None:
                record.sent_at = datetime.now(UTC)
        return True
