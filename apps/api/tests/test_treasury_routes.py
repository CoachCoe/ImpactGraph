"""The treasury over HTTP.

The position is public because publishing it is the commitment. The walk of individual
held contributions is not: totals are what was promised, and a paginated list of who gave
what and when is a different thing.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from impactgraph.auth import DEMO_PASSWORD
from impactgraph.main import app, session_factory
from impactgraph.persistence import FundingRecord

OPERATOR = "operator@globalwater.example"
ORG = "org-global-water"
PROGRAM = "program-clean-water-kenya-2026"


@pytest.fixture
def operator() -> TestClient:
    client = TestClient(app)
    assert (
        client.post("/auth/login", json={"email": OPERATOR, "password": DEMO_PASSWORD}).status_code
        == 200
    )
    return client


@pytest.fixture
def held_contribution():
    assert session_factory is not None
    external_id = "funding-held-for-test"
    with session_factory.begin() as session:
        session.add(
            FundingRecord(
                external_id=external_id,
                organization_ref=ORG,
                program_ref=None,
                funder_name="Unrestricted giving, March",
                contributor_count=412,
                amount_minor=41_200,
                currency="USD",
                received_on="2026-03-01",
                source_ref="src-held-test",
            )
        )
    yield external_id
    with session_factory.begin() as session:
        session.execute(delete(FundingRecord).where(FundingRecord.external_id == external_id))


def test_the_position_is_public(held_contribution):
    """Funds held vs deployed is one of the four published commitments. An answer only
    the organisation can see is not the commitment."""
    response = TestClient(app).get(f"/financial/organizations/{ORG}/position")

    assert response.status_code == 200
    usd = next(item for item in response.json()["byCurrency"] if item["currency"] == "USD")
    assert usd["held"]["amountMinor"] == 41_200
    assert usd["received"]["amountMinor"] >= usd["held"]["amountMinor"]
    assert usd["contributors"] >= 412


def test_the_list_of_held_contributions_is_not_public(held_contribution):
    assert TestClient(app).get(f"/financial/organizations/{ORG}/held").status_code == 401


def test_an_operator_sees_their_own_held_money(operator, held_contribution):
    body = operator.get(f"/financial/organizations/{ORG}/held").json()
    entry = next(c for c in body["contributions"] if c["id"] == held_contribution)

    assert entry["contributors"] == 412
    # A roll-up names nobody, because it stands for nobody in particular.
    assert entry["funder"] == "412 individual donors"


def test_an_operator_cannot_list_another_organisations_held_money(operator):
    assert operator.get("/financial/organizations/org-shelter/held").status_code == 403


def test_assigning_held_money_is_recorded_and_then_refused_a_second_time(
    operator, held_contribution
):
    first = operator.post(
        f"/financial/funding/{held_contribution}/assignment", json={"programId": PROGRAM}
    )
    assert first.status_code == 201, first.text
    assert first.json()["programId"] == PROGRAM
    assert first.json()["assignedAt"]

    again = operator.post(
        f"/financial/funding/{held_contribution}/assignment", json={"programId": PROGRAM}
    )
    assert again.status_code == 409, again.text


def test_assignment_is_refused_to_an_anonymous_caller(held_contribution):
    refused = TestClient(app).post(
        f"/financial/funding/{held_contribution}/assignment", json={"programId": PROGRAM}
    )
    assert refused.status_code == 401


def test_the_programme_summary_stays_bounded(operator):
    """It is public and unauthenticated, so the response cannot grow with the ledger."""
    from impactgraph.financial import LEDGER_PAGE

    body = TestClient(app).get(f"/financial/programs/{PROGRAM}").json()

    assert len(body["funding"]) <= LEDGER_PAGE
    assert len(body["transactions"]) <= LEDGER_PAGE
    assert body["fundingCount"] >= len(body["funding"])
    assert body["transactionCount"] >= len(body["transactions"])
