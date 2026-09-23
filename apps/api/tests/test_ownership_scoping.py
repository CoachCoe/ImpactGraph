"""Operator mutations and internal reads must be scoped to the caller's organisation.

Both gaps here were role checks standing in for ownership checks. The role was right and
the organisation was never consulted, in a system whose entire trust model rests on
separating who operates from who verifies.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from impactgraph.auth import DEMO_PASSWORD, hash_password
from impactgraph.main import app, session_factory
from impactgraph.persistence import EvidenceRecord, OrganizationRecord, UserRecord

OPERATOR = "operator@globalwater.example"
ADMIN = "admin@impactgraph.example"
OUTSIDER = "operator@otherwater.example"
PROGRAM_ID = "program-clean-water-kenya-2026"
EVIDENCE_ID = "ev-inv-8291"


def sign_in(session: TestClient, email: str) -> None:
    response = session.post("/auth/login", json={"email": email, "password": DEMO_PASSWORD})
    assert response.status_code == 200, response.text


def make_outsider() -> None:
    """An operator with a real account at an organisation that operates nothing here."""
    assert session_factory is not None
    with session_factory.begin() as session:
        if session.scalar(select(UserRecord).where(UserRecord.email == OUTSIDER)):
            return
        if not session.scalar(
            select(OrganizationRecord).where(OrganizationRecord.external_id == "org-other-water")
        ):
            session.add(
                OrganizationRecord(
                    external_id="org-other-water", name="Other Water Co", kind="OPERATOR"
                )
            )
            session.flush()
        organization = session.scalar(
            select(OrganizationRecord).where(OrganizationRecord.external_id == "org-other-water")
        )
        session.add(
            UserRecord(
                email=OUTSIDER,
                display_name="Outsider",
                password_hash=hash_password(DEMO_PASSWORD),
                role="OPERATOR",
                organization_id=organization.id,
            )
        )


def set_visibility(value: str) -> None:
    assert session_factory is not None
    with session_factory.begin() as session:
        record = session.scalar(
            select(EvidenceRecord).where(EvidenceRecord.external_id == EVIDENCE_ID)
        )
        record.visibility = value


def test_an_outside_operator_cannot_write_to_another_programs_ledger():
    """The rows this writes are what evidence reconciles against, and reconciliation is a
    required verification requirement. Without this check an outside operator could push
    another organisation's claim across a policy gate, or block it by exhausting the
    allocation."""
    make_outsider()
    session = TestClient(app)
    sign_in(session, OUTSIDER)
    response = session.post(
        f"/financial/programs/{PROGRAM_ID}/import",
        headers={"Idempotency-Key": "outsider-import-1"},
    )
    assert response.status_code == 403
    assert "another operating organisation" in response.text


def test_the_operating_organisation_may_still_import_its_own_statement():
    session = TestClient(app)
    sign_in(session, OPERATOR)
    response = session.post(
        f"/financial/programs/{PROGRAM_ID}/import",
        headers={"Idempotency-Key": "owner-import-1"},
    )
    assert response.status_code == 200, response.text


def test_an_outside_operator_cannot_read_internal_evidence():
    """Role alone let any operator read every other organisation's internal evidence,
    including the extracted document fields. docs/security.md has always said INTERNAL is
    limited to the operating organisation."""
    make_outsider()
    set_visibility("INTERNAL")
    try:
        session = TestClient(app)
        sign_in(session, OUTSIDER)
        response = session.get(f"/evidence/{EVIDENCE_ID}")
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "EVIDENCE_FORBIDDEN"
    finally:
        set_visibility("PUBLIC")


def test_the_operating_organisation_can_read_its_own_internal_evidence():
    set_visibility("INTERNAL")
    try:
        session = TestClient(app)
        sign_in(session, OPERATOR)
        assert session.get(f"/evidence/{EVIDENCE_ID}").status_code == 200
    finally:
        set_visibility("PUBLIC")


def test_an_administrator_may_still_read_internal_evidence():
    set_visibility("INTERNAL")
    try:
        session = TestClient(app)
        sign_in(session, ADMIN)
        assert session.get(f"/evidence/{EVIDENCE_ID}").status_code == 200
    finally:
        set_visibility("PUBLIC")


def test_public_provenance_does_not_publish_a_restricted_document_field():
    """The graph is public so a reader can see the chain is complete. The contents of a
    document they would be refused directly are a different matter.

    Latent rather than live today, because nothing yet links operator-uploaded evidence to
    a claim. The operator screen already uploads as RESTRICTED, so the moment that linkage
    is automated this becomes a live disclosure.
    """
    from sqlalchemy import select

    from impactgraph.main import session_factory
    from impactgraph.persistence import EvidenceRecord
    from impactgraph.read_model import public_evidence_reference

    assert session_factory is not None
    with session_factory() as db:
        seeded = db.scalar(
            select(EvidenceRecord).where(EvidenceRecord.external_id == "ev-inv-8291")
        )
        assert seeded is not None
        # The showcase evidence is public, so its invoice number is fair game.
        assert seeded.visibility == "PUBLIC"
        assert public_evidence_reference(seeded.external_id, "PUBLIC") == "ev-inv-8291"

    # The seeded identifier carries the invoice number inside it, which is the convention
    # this system establishes, so redacting the label and publishing the id would hide
    # nothing at all.
    redacted = public_evidence_reference("ev-inv-8291", "RESTRICTED")
    assert "8291" not in redacted
    assert redacted.startswith("evidence-")
    assert public_evidence_reference("ev-inv-8291", "RESTRICTED") == redacted, (
        "the reference has to be stable, or the same record looks like several"
    )
