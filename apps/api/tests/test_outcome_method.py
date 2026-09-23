"""An outcome figure has to say how it was arrived at.

Every other number in this system can be chased to something: a payment to a statement, a
commitment to a registry, a claim to a requirement list. The outcome was the exception —
a metric, a value and a unit, and nothing behind it. It was the one figure a reader had to
take on trust, in a product whose whole argument is that they should not have to.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from impactgraph.main import app, session_factory
from impactgraph.persistence import OutcomeRecord

CLAIM_ID = "claim-water-12-200"
OUTCOME_ID = "outcome-water-12-200"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def outcome_node(client: TestClient) -> dict:
    nodes = client.get(f"/claims/{CLAIM_ID}/provenance").json()["nodes"]
    return next(node for node in nodes if node["type"] == "OUTCOME")


def test_the_outcome_carries_its_method_to_the_page(client):
    node = outcome_node(client)
    assert node["method"], "the figure a donor reads has nothing behind it"
    assert "source" in node
    assert "confidencePercent" in node


def test_the_seeded_outcome_does_not_claim_to_have_been_measured():
    """The showcase programme is fictional.

    A method line reading like a real survey instrument would make the demonstration
    assert something nobody measured, which is the failure the seeded operator
    attestation, the console transport and the mock extractor are all careful to avoid.
    """
    assert session_factory is not None
    with session_factory() as session:
        outcome = session.scalar(
            select(OutcomeRecord).where(OutcomeRecord.external_id == OUTCOME_ID)
        )
    assert outcome is not None
    assert "Not measured" in outcome.method
    assert outcome.confidence_percent is None, (
        "a fixture must not carry a confidence, which would be a number nobody computed"
    )


def test_a_confidence_of_none_and_a_confidence_of_zero_stay_distinct():
    """Nought and unknown are different answers.

    A method that produced no confidence and a method that produced a confidence of zero
    mean opposite things, and a column that cannot hold the difference invents one.
    """
    assert session_factory is not None
    with session_factory.begin() as session:
        session.add(
            OutcomeRecord(
                external_id="outcome-confidence-probe",
                program_ref="program-clean-water-kenya-2026",
                metric="probe",
                value=1,
                unit="probe",
                method="probe",
                confidence_percent=0,
            )
        )
    try:
        with session_factory() as session:
            measured_zero = session.scalar(
                select(OutcomeRecord).where(
                    OutcomeRecord.external_id == "outcome-confidence-probe"
                )
            )
            unstated = session.scalar(
                select(OutcomeRecord).where(OutcomeRecord.external_id == OUTCOME_ID)
            )
        assert measured_zero.confidence_percent == 0
        assert unstated.confidence_percent is None
    finally:
        with session_factory.begin() as session:
            row = session.scalar(
                select(OutcomeRecord).where(
                    OutcomeRecord.external_id == "outcome-confidence-probe"
                )
            )
            if row is not None:
                session.delete(row)


def test_attribution_carries_the_qualification_to_the_funder(client):
    """A figure reached by following one contribution is qualified as it is on the claim."""
    trail = client.get("/financial/funding/funding-jane-10000/attribution").json()
    outcomes = [
        outcome
        for allocation in trail["allocations"]
        for transaction in allocation["transactions"]
        for delivery in transaction["deliveries"]
        for outcome in delivery["outcomes"]
    ]
    assert outcomes, "the seeded contribution should reach an outcome"
    assert all(outcome["method"] for outcome in outcomes)
