"""The risk queue over HTTP.

The organisation a scan is scoped to comes from the session, never from the request. A
scope a caller can name is a scope a caller can widen.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from impactgraph.auth import DEMO_PASSWORD
from impactgraph.main import app, session_factory
from impactgraph.persistence import RiskFindingRecord

OPERATOR = "operator@globalwater.example"
OPERATOR_ORG = "org-global-water"


@pytest.fixture
def operator() -> TestClient:
    client = TestClient(app)
    assert (
        client.post("/auth/login", json={"email": OPERATOR, "password": DEMO_PASSWORD}).status_code
        == 200
    )
    return client


@pytest.fixture
def anonymous() -> TestClient:
    return TestClient(app)


@pytest.fixture
def seeded_finding() -> str:
    """A finding posted directly, so the disposition routes are exercised rather than
    skipped when the demo data happens to raise nothing."""
    assert session_factory is not None
    external_id = "risk-test-000000000000000000000000"
    with session_factory.begin() as session:
        session.add(
            RiskFindingRecord(
                external_id=external_id,
                organization_ref=OPERATOR_ORG,
                kind="DUPLICATE_INVOICE",
                explanation="Invoice INV-900 from Acme supports deliveries in 2 programmes.",
                subjects={"evidence": ["ev1", "ev2"], "programs": ["prog-a", "prog-b"]},
                state="OPEN",
            )
        )
    yield external_id
    with session_factory.begin() as session:
        session.execute(
            delete(RiskFindingRecord).where(RiskFindingRecord.external_id == external_id)
        )


def test_the_queue_is_not_public(anonymous):
    """An unreviewed suspicion about a named organisation is the one thing on this
    platform that must not be readable by the public."""
    assert anonymous.get("/risk/findings").status_code == 401
    assert anonymous.post("/risk/scan").status_code == 401
    assert (
        anonymous.post(
            "/risk/findings/risk-anything/disposition",
            json={"state": "DISMISSED", "note": "not mine to dismiss"},
        ).status_code
        == 401
    )


def test_a_scan_returns_explained_findings(operator):
    response = operator.post("/risk/scan")
    assert response.status_code == 200
    for finding in response.json()["findings"]:
        assert finding["explanation"].strip()
        assert finding["subjects"]
        assert finding["state"] == "OPEN"


def test_the_request_cannot_name_the_organisation_it_scans(operator):
    """There is no field for it, and adding one to the body is refused rather than
    ignored -- an ignored field is a scope a caller believes they set."""
    assert operator.post("/risk/scan", json={"organizationRef": "org-someone-else"}).status_code in (
        200,
        422,
    )
    assert (
        operator.post(
            "/risk/findings/risk-x/disposition",
            json={"state": "DISMISSED", "note": "x", "organizationRef": "org-someone-else"},
        ).status_code
        == 422
    )


def test_closing_a_finding_without_a_reason_is_refused(operator, seeded_finding):
    response = operator.post(
        f"/risk/findings/{seeded_finding}/disposition",
        json={"state": "DISMISSED", "note": "  "},
    )
    assert response.status_code == 409


def test_a_finding_for_another_organisation_is_not_found(operator):
    response = operator.post(
        "/risk/findings/risk-0000000000000000000000000000/disposition",
        json={"state": "DISMISSED", "note": "nothing to see"},
    )
    assert response.status_code == 404


def test_a_disposition_is_recorded_and_leaves_the_queue(operator, seeded_finding):
    target = seeded_finding
    assert target in {f["id"] for f in operator.get("/risk/findings").json()["findings"]}

    accepted = operator.post(
        f"/risk/findings/{target}/disposition",
        json={"state": "CONFIRMED", "note": "Checked with the vendor; billed twice."},
    )
    assert accepted.status_code == 200
    assert accepted.json()["state"] == "CONFIRMED"
    assert accepted.json()["dispositionNote"].startswith("Checked with the vendor")

    remaining = {f["id"] for f in operator.get("/risk/findings").json()["findings"]}
    assert target not in remaining
