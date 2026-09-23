"""A payment the bank took back.

A reversal is not an evidence change, so it does not travel the path an integrity failure
takes and would otherwise never reach the claim. A donor would read a verified badge over
a payment that did not happen.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from impactgraph.auth import DEMO_PASSWORD
from impactgraph.main import app, session_factory
from impactgraph.persistence import AuditLogRecord, EvidenceRecord, FinancialTransactionRecord

OPERATOR = "operator@globalwater.example"
TRANSACTION = "ftx-9182"
EVIDENCE = "ev-inv-8291"


@pytest.fixture
def operator() -> TestClient:
    client = TestClient(app)
    assert client.post(
        "/auth/login", json={"email": OPERATOR, "password": DEMO_PASSWORD}
    ).status_code == 200
    return client


def test_the_reconciliation_records_which_payment_it_matched():
    """The messages name it, but a link recovered by reading prose is not a link."""
    assert session_factory is not None
    with session_factory() as session:
        evidence = session.scalar(
            select(EvidenceRecord).where(EvidenceRecord.external_id == EVIDENCE)
        )
        assert evidence.reconciliation["transactionRef"] == TRANSACTION


def test_a_reversed_payment_re_evaluates_what_rested_on_it(operator):
    reversed_ = operator.post(
        f"/financial/transactions/{TRANSACTION}/reversal",
        json={"reason": "The bank returned it as a duplicate."},
    )
    assert reversed_.status_code == 200, reversed_.text
    assert reversed_.json()["alreadyReversed"] is False

    assert session_factory is not None
    with session_factory() as session:
        transaction = session.scalar(
            select(FinancialTransactionRecord).where(
                FinancialTransactionRecord.external_id == TRANSACTION
            )
        )
        assert transaction.settlement == "REVERSED"
        assert transaction.reversed_at is not None


def test_a_reversal_stops_the_money_counting_against_its_allocation(operator):
    """Otherwise a payment that was taken back permanently consumes budget nobody spent."""
    operator.post(
        f"/financial/transactions/{TRANSACTION}/reversal", json={"reason": "Returned."}
    )
    assert session_factory is not None
    with session_factory() as session:
        transaction = session.scalar(
            select(FinancialTransactionRecord).where(
                FinancialTransactionRecord.external_id == TRANSACTION
            )
        )
        assert transaction.match_status == "UNMATCHED"


def test_reversing_twice_is_not_an_error_and_does_not_restate_again(operator):
    first = operator.post(
        f"/financial/transactions/{TRANSACTION}/reversal", json={"reason": "Returned."}
    ).json()
    second = operator.post(
        f"/financial/transactions/{TRANSACTION}/reversal", json={"reason": "Returned."}
    ).json()
    assert first["alreadyReversed"] is False
    assert second["alreadyReversed"] is True
    assert second["claimsRestated"] == []


def test_a_reversal_is_recorded_with_its_reason(operator):
    operator.post(
        f"/financial/transactions/{TRANSACTION}/reversal",
        json={"reason": "The bank returned it as a duplicate."},
    )
    assert session_factory is not None
    with session_factory() as session:
        entry = session.scalar(
            select(AuditLogRecord)
            .where(AuditLogRecord.action == "PAYMENT_REVERSED")
            .order_by(AuditLogRecord.created_at.desc())
        )
        assert entry is not None
        assert "duplicate" in str(entry.metadata_json)


def test_a_payment_that_does_not_exist_is_not_silently_reversed(operator):
    assert operator.post(
        "/financial/transactions/ftx-imaginary/reversal", json={"reason": "x"}
    ).status_code == 404


def test_only_an_operator_or_administrator_may_record_a_reversal():
    anonymous = TestClient(app)
    assert anonymous.post(
        f"/financial/transactions/{TRANSACTION}/reversal", json={"reason": "x"}
    ).status_code == 401


def test_a_verified_claim_falls_when_the_payment_under_it_is_reversed(operator):
    """The behaviour this whole feature is for.

    The policy reads the evidence's reconciliation rather than the transaction's
    settlement, so marking only the transaction left the claim standing over a payment the
    bank had withdrawn -- and the restate call was a no-op nothing could detect.
    """
    from impactgraph.persistence import ClaimRecord

    assert session_factory is not None
    with session_factory.begin() as session:
        session.scalar(
            select(ClaimRecord).where(ClaimRecord.external_id == "claim-water-12-200")
        ).status = "VERIFIED"

    response = operator.post(
        f"/financial/transactions/{TRANSACTION}/reversal",
        json={"reason": "The bank returned it."},
    )
    assert response.status_code == 200, response.text
    assert "claim-water-12-200" in response.json()["claimsRestated"]

    with session_factory() as session:
        claim = session.scalar(
            select(ClaimRecord).where(ClaimRecord.external_id == "claim-water-12-200")
        )
        assert claim.status != "VERIFIED", "a claim outlived the payment supporting it"


def test_the_evidence_says_the_payment_was_taken_back(operator):
    """A reader should see it was matched to a payment later reversed, which is a
    different and more alarming fact than never having been matched at all."""
    operator.post(
        f"/financial/transactions/{TRANSACTION}/reversal", json={"reason": "Returned."}
    )
    assert session_factory is not None
    with session_factory() as session:
        evidence = session.scalar(
            select(EvidenceRecord).where(EvidenceRecord.external_id == EVIDENCE)
        )
        assert evidence.reconciliation["status"] == "CONFLICT"
        assert any(
            check["check"] == "PAYMENT_REVERSED" for check in evidence.reconciliation["checks"]
        )
