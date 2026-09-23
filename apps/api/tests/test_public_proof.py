"""What a stranger sees, and who decides.

The provenance graph named a private individual to anyone who asked, and proof pages
would put that in front of a much larger audience. An organisation that funded a
programme is a public fact; a person's giving is their own business, and they are the
only one who can decide to be named.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from impactgraph.auth import DEMO_PASSWORD
from impactgraph.main import app, session_factory
from impactgraph.persistence import REDACTED_FUNDER, FundingRecord

OPERATOR = "operator@globalwater.example"
FUNDING = "funding-jane-10000"
PROGRAM = "program-clean-water-kenya-2026"
CLAIM = "claim-water-12-200"


@pytest.fixture
def anonymous() -> TestClient:
    return TestClient(app)


def operator_client() -> TestClient:
    client = TestClient(app)
    assert client.post(
        "/auth/login", json={"email": OPERATOR, "password": DEMO_PASSWORD}
    ).status_code == 200
    return client


def test_a_stranger_is_not_told_which_person_gave_the_money(anonymous):
    graph = anonymous.get(f"/claims/{CLAIM}/provenance").json()
    titles = [node.get("title") for node in graph["nodes"]]
    assert "Jane Smith" not in titles
    assert REDACTED_FUNDER in titles

    trail = anonymous.get(f"/financial/programs/{PROGRAM}").json()
    assert "Jane Smith" not in str(trail)

    attribution = anonymous.get(f"/financial/funding/{FUNDING}/attribution").json()
    assert attribution["funder"] == REDACTED_FUNDER


def test_the_money_is_still_followable_without_the_name(anonymous):
    """Withholding who gave it does not withhold that it was given. The amount, the date
    and the hashes are what make the trail checkable."""
    attribution = anonymous.get(f"/financial/funding/{FUNDING}/attribution").json()
    assert attribution["funder"] == REDACTED_FUNDER
    assert "Jane Smith" not in str(attribution)
    # The money is still followable: what is withheld is which person it came from.
    assert str(attribution).count("amountMinor") > 0


def test_an_organisation_that_funded_a_programme_stays_named(anonymous):
    trail = anonymous.get(f"/financial/programs/{PROGRAM}").json()
    assert "Institutional funding pool" in str(trail)


def test_an_operator_cannot_decide_that_a_donor_is_named():
    """They know the name already. What they do not have is any route that publishes it."""
    client = operator_client()
    issued = client.post(f"/funding/{FUNDING}/name-consent")
    assert issued.status_code == 201, issued.text
    assert "token" in issued.json()

    # Asking for the link publishes nothing.
    assert (
        TestClient(app).get(f"/financial/funding/{FUNDING}/attribution").json()["funder"]
        == REDACTED_FUNDER
    )

    routes = {route.path for route in app.routes}
    assert not any(
        path.startswith("/funding/") and path.endswith("/publish-name") for path in routes
    ), "an endpoint exists that lets somebody else publish a funder's name"


def test_a_funder_can_name_themselves_and_change_their_mind():
    client = operator_client()
    token = client.post(f"/funding/{FUNDING}/name-consent").json()["token"]

    published = TestClient(app).post(
        f"/funding/name-consent/{token}", json={"publish": True}
    )
    assert published.status_code == 200, published.text
    assert published.json()["shownAs"] == "Jane Smith"
    assert (
        TestClient(app).get(f"/financial/funding/{FUNDING}/attribution").json()["funder"]
        == "Jane Smith"
    )

    withdrawn = TestClient(app).post(
        f"/funding/name-consent/{token}", json={"publish": False}
    )
    assert withdrawn.json()["shownAs"] == REDACTED_FUNDER
    assert (
        TestClient(app).get(f"/financial/funding/{FUNDING}/attribution").json()["funder"]
        == REDACTED_FUNDER
    )


def test_a_link_nobody_was_given_does_not_work():
    refused = TestClient(app).post(
        "/funding/name-consent/not-a-real-token", json={"publish": True}
    )
    assert refused.status_code == 404


def test_the_consent_record_does_not_write_the_name_down_again():
    from impactgraph.persistence import AuditLogRecord

    client = operator_client()
    token = client.post(f"/funding/{FUNDING}/name-consent").json()["token"]
    TestClient(app).post(f"/funding/name-consent/{token}", json={"publish": True})

    assert session_factory is not None
    with session_factory() as session:
        entry = session.scalar(
            select(AuditLogRecord)
            .where(AuditLogRecord.action == "FUNDER_NAME_PUBLISHED")
            .order_by(AuditLogRecord.created_at.desc())
        )
        assert entry is not None
        assert "Jane Smith" not in str(entry.metadata_json)


def test_existing_funding_defaults_to_the_careful_answer():
    """A preference nobody recorded is not a preference to publish."""
    assert session_factory is not None
    with session_factory() as session:
        jane = session.scalar(
            select(FundingRecord).where(FundingRecord.external_id == FUNDING)
        )
        assert jane.funder_is_organisation is False
        assert jane.publish_funder_name is False
