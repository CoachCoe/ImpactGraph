"""A funder asked what their money reached. The answer has to include what it did not.

The seeded portfolio is deliberately awkward: Jane's funding is larger than the allocation
it backs, the allocation is larger than the spend under it, and the institutional pool is
committed beyond what it supplied. All three are states a real program reaches, and none
of them may crash or be quietly rounded away.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from impactgraph.attribution import funding_attribution
from impactgraph.main import app, session_factory

JANE = "funding-jane-10000"
POOL = "funding-institutional-90000"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def trace(funding_ref: str) -> dict:
    assert session_factory is not None
    with session_factory() as session:
        return funding_attribution(session, funding_ref)


def test_a_funder_is_traced_to_the_claim_their_money_reached():
    result = trace(JANE)
    claims = [
        claim["id"]
        for allocation in result["allocations"]
        for transaction in allocation["transactions"]
        for delivery in transaction["deliveries"]
        for outcome in delivery["outcomes"]
        for claim in outcome["claims"]
    ]
    assert "claim-water-12-200" in claims


def test_the_money_that_never_moved_is_reported_beside_the_money_that_did():
    """Jane gave 10,000 and 8,500 was committed. A funder shown only the deployed part
    has been told half the answer."""
    result = trace(JANE)
    assert result["received"]["amountMinor"] == 1000000
    assert result["committed"]["amountMinor"] == 850000
    assert result["uncommitted"]["amountMinor"] == 150000


def test_an_allocation_reports_what_is_still_unspent_within_it():
    result = trace(JANE)
    allocation = result["allocations"][0]
    assert allocation["amount"]["amountMinor"] == 850000
    assert allocation["spent"]["amountMinor"] == 420000
    assert allocation["unspent"]["amountMinor"] == 430000
    assert allocation["overspent"]["amountMinor"] == 0


def test_a_program_committed_beyond_its_funding_reports_that_rather_than_failing():
    """`Money` refuses to hold a negative, which is right for a ledger and wrong for a
    difference. The seeded pool is committed 91,500 against 90,000 supplied."""
    result = trace(POOL)
    assert result["received"]["amountMinor"] == 9000000
    assert result["committed"]["amountMinor"] == 9150000
    assert result["uncommitted"]["amountMinor"] == 0
    assert result["overcommitted"]["amountMinor"] == 150000


def test_the_claim_is_shown_whatever_its_status_says():
    """A transparency product that shows a funder only their successes is not one."""
    result = trace(JANE)
    statuses = [
        claim["status"]
        for allocation in result["allocations"]
        for transaction in allocation["transactions"]
        for delivery in transaction["deliveries"]
        for outcome in delivery["outcomes"]
        for claim in outcome["claims"]
    ]
    assert statuses and all(status for status in statuses)


def test_the_basis_of_the_attribution_is_stated_with_the_numbers():
    method = trace(JANE)["method"]
    assert method["basis"] == "EXACT_BY_FUNDING_RECORD"
    # The limit is named, not just the method.
    assert "more than one contributor" in method["explanation"]


def test_attribution_is_public_and_unknown_funding_is_a_404(client):
    assert client.get(f"/financial/funding/{JANE}/attribution").status_code == 200
    assert client.get("/financial/funding/funding-nope/attribution").status_code == 404
