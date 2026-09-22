"""Detection nobody is told about is not detection.

`restate_claims_for` could always lower a verified claim whose evidence stopped matching
its commitment. It wrote that to a row and told no one. These tests are about the part
that closes the loop, and about the ways a notification channel goes wrong: telling
someone something that rolled back, telling them twice, or letting one stranger use it to
mail another.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from impactgraph.main import app, session_factory
from impactgraph.notifications import (
    CLAIM_STATUS_CHANGED,
    Message,
    NotificationDispatcher,
    enqueue_claim_status_change,
    subscribe,
)
from impactgraph.persistence import (
    ClaimRecord,
    NotificationDeliveryRecord,
    NotificationSubscriptionRecord,
    OutboxRecord,
)

CLAIM_ID = "claim-water-12-200"
EVIDENCE_ID = "ev-inv-8291"
ADMIN = "admin@impactgraph.example"


class RecordingTransport:
    name = "recording"

    def __init__(self) -> None:
        self.sent: list[Message] = []

    def send(self, message: Message) -> None:
        self.sent.append(message)


class FailingTransport:
    name = "failing"

    def send(self, message: Message) -> None:
        raise ConnectionError("smtp://user:secret@mail.example unreachable")


@pytest.fixture(autouse=True)
def _clear_notifications():
    yield
    assert session_factory is not None
    with session_factory.begin() as session:
        for row in session.scalars(select(NotificationDeliveryRecord)):
            session.delete(row)
        for row in session.scalars(select(NotificationSubscriptionRecord)):
            session.delete(row)
        for row in session.scalars(
            select(OutboxRecord).where(OutboxRecord.topic == CLAIM_STATUS_CHANGED)
        ):
            session.delete(row)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def follow(email: str, claim_id: str = CLAIM_ID, confirm: bool = True) -> str:
    assert session_factory is not None
    with session_factory.begin() as session:
        token, _ = subscribe(session, email=email, claim_id=claim_id)
    if confirm:
        TestClient(app).post("/notifications/confirm", params={"token": token})
    return token


def make_verified() -> None:
    """The seeded claim starts pending, and only a change of status is news."""
    assert session_factory is not None
    with session_factory.begin() as session:
        record = session.scalar(
            select(ClaimRecord).where(ClaimRecord.external_id == CLAIM_ID)
        )
        record.status = "VERIFIED"


def dispatch(transport) -> int:
    return NotificationDispatcher(
        session_factory=session_factory, transport=transport
    ).run_once()


def test_tampering_with_evidence_tells_the_people_following_the_claim(client, sign_in):
    """The whole point, end to end: stored bytes change, the claim falls, a follower hears."""
    make_verified()
    follow("donor@example.com")
    sign_in(client, ADMIN)
    assert client.post(f"/demo/evidence/{EVIDENCE_ID}/tamper").json()["status"] == "MISMATCH"

    transport = RecordingTransport()
    assert dispatch(transport) == 1
    assert transport.sent[0].to == "donor@example.com"
    assert "no longer verified" in transport.sent[0].subject


def test_the_same_change_is_never_sent_twice(client, sign_in):
    """A dispatcher that retries must not tell someone the same news again."""
    make_verified()
    follow("donor@example.com")
    sign_in(client, ADMIN)
    client.post(f"/demo/evidence/{EVIDENCE_ID}/tamper")

    transport = RecordingTransport()
    assert dispatch(transport) == 1
    # Re-run against the same intent: the delivery row already exists.
    assert session_factory is not None
    with session_factory.begin() as session:
        for row in session.scalars(
            select(OutboxRecord).where(OutboxRecord.topic == CLAIM_STATUS_CHANGED)
        ):
            row.processed_at = None
    assert dispatch(transport) == 0
    assert len(transport.sent) == 1


def test_an_unconfirmed_address_is_never_written_to():
    """Otherwise this endpoint is a way to mail a stranger from someone else's domain."""
    follow("never-asked@example.com", confirm=False)
    assert session_factory is not None
    with session_factory.begin() as session:
        enqueue_claim_status_change(
            session, claim_id=CLAIM_ID, status="CHALLENGED", correlation_id="c"
        )
    transport = RecordingTransport()
    assert dispatch(transport) == 0
    assert transport.sent == []


def test_unsubscribing_is_honoured_immediately(client):
    token = follow("donor@example.com")
    assert client.post("/notifications/unsubscribe", params={"token": token}).status_code == 200

    assert session_factory is not None
    with session_factory.begin() as session:
        enqueue_claim_status_change(
            session, claim_id=CLAIM_ID, status="CHALLENGED", correlation_id="c"
        )
    transport = RecordingTransport()
    assert dispatch(transport) == 0


def test_following_twice_does_not_create_a_second_subscription(client):
    for _ in range(3):
        client.post(f"/claims/{CLAIM_ID}/follow", json={"email": "donor@example.com"})
    assert session_factory is not None
    with session_factory() as session:
        rows = list(session.scalars(select(NotificationSubscriptionRecord)))
    assert len(rows) == 1


def test_a_failed_send_is_recorded_without_leaking_the_transport_error(client, sign_in):
    """A transport error can carry credentials in a URL, exactly as an RPC one can."""
    make_verified()
    follow("donor@example.com")
    sign_in(client, ADMIN)
    client.post(f"/demo/evidence/{EVIDENCE_ID}/tamper")

    assert dispatch(FailingTransport()) == 0
    assert session_factory is not None
    with session_factory() as session:
        delivery = session.scalar(select(NotificationDeliveryRecord))
    assert delivery is not None
    assert delivery.sent_at is None
    assert delivery.error == "ConnectionError"
    assert "secret" not in (delivery.error or "")


def test_the_intent_is_written_in_the_transaction_that_changed_the_status(client, sign_in):
    """A message about a status that rolled back cannot be recalled, so it is never sent
    from outside the transaction that decided it."""
    make_verified()
    follow("donor@example.com")
    sign_in(client, ADMIN)
    client.post(f"/demo/evidence/{EVIDENCE_ID}/tamper")

    assert session_factory is not None
    with session_factory() as session:
        intents = list(
            session.scalars(
                select(OutboxRecord).where(OutboxRecord.topic == CLAIM_STATUS_CHANGED)
            )
        )
    assert len(intents) == 1
    assert intents[0].payload["claimId"] == CLAIM_ID
    assert intents[0].payload["status"] == "CHALLENGED"


def test_an_unknown_claim_cannot_be_followed(client):
    assert (
        client.post("/claims/claim-nope/follow", json={"email": "a@example.com"}).status_code
        == 404
    )
