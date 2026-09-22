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

#: Retries before a change is given up on and said to be given up on. Without a cap a
#: permanently unreachable address holds its outbox row for ever.
MAX_DELIVERY_ATTEMPTS = 5

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


def subscribe(session: Session, *, email: str, claim_id: str) -> str | None:
    """Record an interest in a claim. Returns a token to confirm, or None if there is
    nothing to send.

    An address that already follows the claim is left entirely alone. Reissuing its token
    would let anyone who knows the address break the unsubscribe link in the message it
    was already sent.

    An address that unsubscribed has to confirm again. Clearing `unsubscribed_at` while
    keeping `confirmed_at` would let a stranger who knows the address and the claim -- both
    public -- re-enrol someone who opted out, with no message to tell them so. An
    unsubscribe a third party can reverse is not an unsubscribe.
    """
    normalized = email.strip().lower()
    existing = session.scalar(
        select(NotificationSubscriptionRecord).where(
            NotificationSubscriptionRecord.email == normalized,
            NotificationSubscriptionRecord.claim_id == claim_id,
        )
    )
    if existing is not None and existing.confirmed_at is not None and existing.unsubscribed_at is None:
        return None

    token = secrets.token_urlsafe(48)
    if existing is not None:
        existing.token_hash = token_hash(token)
        existing.confirmed_at = None
        existing.unsubscribed_at = None
        return token
    session.add(
        NotificationSubscriptionRecord(
            email=normalized, claim_id=claim_id, token_hash=token_hash(token)
        )
    )
    return token


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
                    .with_for_update(skip_locked=True)
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
            self._retire(outbox_id, "unknown topic")
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
        outstanding = False
        for subscription_id, email in targets:
            outcome = self._deliver_once(
                subscription_id, email, str(outbox_id), claim_id, status
            )
            if outcome == "sent":
                sent += 1
            elif outcome == "failed":
                outstanding = True

        with self.session_factory() as session, session.begin():
            outbox = session.get(OutboxRecord, outbox_id)
            if outbox is None:
                return sent
            if not outstanding:
                outbox.processed_at = datetime.now(UTC)
                return sent
            # Someone has not been told. Marking this processed because the attempt
            # happened would lose every message sent during a transport outage.
            outbox.attempts += 1
            if outbox.attempts >= MAX_DELIVERY_ATTEMPTS:
                outbox.processed_at = datetime.now(UTC)
                log.error(
                    "notification.abandoned",
                    claim_id=claim_id,
                    status=status,
                    attempts=outbox.attempts,
                )
        return sent

    def _retire(self, outbox_id, reason: str) -> None:
        with self.session_factory() as session, session.begin():
            outbox = session.get(OutboxRecord, outbox_id)
            if outbox is not None:
                outbox.processed_at = datetime.now(UTC)
        log.warning("notification.retired", reason=reason)

    def _deliver_once(
        self, subscription_id, email: str, event_key: str, claim_id: str, status: str
    ) -> str:
        """Send once, and say which of sent, already or failed happened.

        A delivery row is a claim on the work, not proof it was done. Treating its mere
        existence as delivered means a crash between claiming and sending loses the
        message for ever: the retry loses the insert and sends nothing. `sent_at` is what
        distinguishes the two.
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
            with self.session_factory() as session:
                existing = session.scalar(
                    select(NotificationDeliveryRecord).where(
                        NotificationDeliveryRecord.subscription_id == subscription_id,
                        NotificationDeliveryRecord.event_key == event_key,
                    )
                )
            if existing is not None and existing.sent_at is not None:
                return "already"

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
            self._mark(subscription_id, event_key, error=exc.__class__.__name__)
            log.warning(
                "notification.send_failed",
                claim_id=claim_id,
                error_type=exc.__class__.__name__,
            )
            return "failed"

        self._mark(subscription_id, event_key, sent=True)
        return "sent"

    def _mark(self, subscription_id, event_key: str, *, sent: bool = False, error: str | None = None) -> None:
        with self.session_factory() as session, session.begin():
            record = session.scalar(
                select(NotificationDeliveryRecord).where(
                    NotificationDeliveryRecord.subscription_id == subscription_id,
                    NotificationDeliveryRecord.event_key == event_key,
                )
            )
            if record is None:
                return
            if sent:
                record.sent_at = datetime.now(UTC)
                record.error = None
            else:
                record.error = error
