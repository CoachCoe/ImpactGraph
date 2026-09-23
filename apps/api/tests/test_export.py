"""An export is where inspection stops depending on our good behaviour.

Everything else here is checkable only through pages this application renders, so every
check is one it agreed to show. A file someone can take away and interrogate with their
own tools is a different kind of claim — which is exactly why it must not become the one
route that answers what every other route refuses.
"""

from __future__ import annotations

import csv
import io

import pytest
from fastapi.testclient import TestClient

from impactgraph.main import app

PROGRAM = "program-clean-water-kenya-2026"
CLAIM = "claim-water-12-200"
EVIDENCE = "ev-inv-8291"
ADMIN = "admin@impactgraph.example"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def rows(body: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(body)))


def test_the_money_trail_exports_every_record_type(client):
    response = client.get(f"/export/programs/{PROGRAM}/money-trail.csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]

    kinds = {row["record_type"] for row in rows(response.text)}
    assert {"FUNDING", "ALLOCATION", "PAYMENT", "DELIVERY"} <= kinds


def test_a_payment_nothing_evidences_is_still_in_the_file(client):
    """The seeded statement contains a payment with no invoice on purpose.

    Dropping it would make the export a record of the spending that happened to go well,
    which is the opposite of the argument this product makes.
    """
    exported = rows(client.get(f"/export/programs/{PROGRAM}/money-trail.csv").text)
    statuses = {row["match_status"] for row in exported if row["record_type"] == "PAYMENT"}
    assert "UNMATCHED" in statuses


def test_money_amounts_export_as_minor_units_with_their_currency(client):
    """Never a formatted string. A reader doing arithmetic on this file must not have to
    parse a symbol or guess a locale."""
    exported = rows(client.get(f"/export/programs/{PROGRAM}/money-trail.csv").text)
    funding = next(row for row in exported if row["record_type"] == "FUNDING")
    assert funding["amount_minor"].isdigit()
    assert funding["currency"] == "USD"


def test_outcomes_export_carries_the_method(client):
    exported = rows(client.get(f"/export/programs/{PROGRAM}/outcomes.csv").text)
    assert exported
    assert all(row["method"] for row in exported)
    # Unknown stays empty rather than becoming a zero someone could average.
    assert all(row["confidence_percent"] == "" for row in exported)


def test_provenance_exports_the_edges_the_claim_rests_on(client):
    exported = rows(client.get(f"/export/claims/{CLAIM}/provenance.csv").text)
    assert exported
    assert all(row["claim_id"] == CLAIM for row in exported)
    relationships = {row["relationship"] for row in exported}
    assert "SUPPORTS" in relationships


def test_evidence_exports_the_commitment_needed_to_check_it(client):
    """Without the content hash the file is a spreadsheet of assertions."""
    exported = rows(client.get(f"/export/claims/{CLAIM}/evidence.csv").text)
    assert exported
    row = next(item for item in exported if item["evidence_id"] == EVIDENCE)
    assert row["content_hash"].startswith("sha256:")


def test_an_export_does_not_answer_what_every_other_route_refuses(client, sign_in):
    """Restricted evidence reaches only a reader who could already open it."""
    from sqlalchemy import select

    from impactgraph.main import session_factory
    from impactgraph.persistence import EvidenceRecord

    assert session_factory is not None
    with session_factory.begin() as session:
        record = session.scalar(
            select(EvidenceRecord).where(EvidenceRecord.external_id == EVIDENCE)
        )
        record.visibility = "RESTRICTED"
    try:
        anonymous = rows(client.get(f"/export/claims/{CLAIM}/evidence.csv").text)
        assert all(item["evidence_id"] != EVIDENCE for item in anonymous), (
            "restricted evidence left the building in an anonymous export"
        )

        signed_in = TestClient(app)
        sign_in(signed_in, ADMIN)
        privileged = rows(signed_in.get(f"/export/claims/{CLAIM}/evidence.csv").text)
        assert any(item["evidence_id"] == EVIDENCE for item in privileged)
    finally:
        with session_factory.begin() as session:
            record = session.scalar(
                select(EvidenceRecord).where(EvidenceRecord.external_id == EVIDENCE)
            )
            record.visibility = "PUBLIC"


def test_an_unknown_claim_does_not_export_an_empty_file(client):
    """An empty file reads as 'this claim has no provenance', which is a different and
    much more flattering statement than 'no such claim'."""
    assert client.get("/export/claims/claim-nope/provenance.csv").status_code == 404
    assert client.get("/export/claims/claim-nope/evidence.csv").status_code == 404


def test_the_column_contract_is_stable(client):
    """Anything anyone automates against is an interface whether it was meant to be or
    not, so changing these has to be a deliberate act rather than a side effect."""
    from impactgraph.export import (
        EVIDENCE_COLUMNS,
        MONEY_TRAIL_COLUMNS,
        OUTCOME_COLUMNS,
        PROVENANCE_COLUMNS,
    )

    for path, columns in (
        (f"/export/programs/{PROGRAM}/money-trail.csv", MONEY_TRAIL_COLUMNS),
        (f"/export/programs/{PROGRAM}/outcomes.csv", OUTCOME_COLUMNS),
        (f"/export/claims/{CLAIM}/provenance.csv", PROVENANCE_COLUMNS),
        (f"/export/claims/{CLAIM}/evidence.csv", EVIDENCE_COLUMNS),
    ):
        header = client.get(path).text.splitlines()[0]
        assert header == ",".join(columns), path
