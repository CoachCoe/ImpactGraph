"""Operator mutations and internal reads must be scoped to the caller's organisation.

Both gaps here were role checks standing in for ownership checks. The role was right and
the organisation was never consulted, in a system whose entire trust model rests on
separating who operates from who verifies.
"""

from __future__ import annotations

import pytest
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


def test_a_program_is_owned_by_the_session_not_the_request_body():
    """The owning organisation is never client-supplied.

    An operator who could name it could create a program inside another tenancy and then
    file evidence against it, which is the whole separation this system rests on.
    """
    make_outsider()
    client = TestClient(app)
    sign_in(client, OUTSIDER)
    created = client.post(
        "/programs",
        headers={"Idempotency-Key": "outsider-program"},
        json={"id": "program-outsider-wells", "name": "Outsider Wells", "region": "Nairobi"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["operatorOrgRef"] == "org-other-water"
    # There is no field to say otherwise, and inventing one is refused rather than ignored.
    refused = client.post(
        "/programs",
        headers={"Idempotency-Key": "outsider-program-2"},
        json={
            "id": "program-outsider-two",
            "name": "Outsider Two",
            "region": "Nairobi",
            "operatorOrgRef": "org-global-water",
        },
    )
    assert refused.status_code == 422


def test_an_operator_cannot_create_a_project_under_another_organisations_program():
    make_outsider()
    client = TestClient(app)
    sign_in(client, OUTSIDER)
    response = client.post(
        f"/programs/{PROGRAM_ID}/projects",
        headers={"Idempotency-Key": "outsider-project"},
        json={"id": "project-outsider-1", "name": "Borehole"},
    )
    assert response.status_code == 403, response.text

    owner = TestClient(app)
    sign_in(owner, OPERATOR)
    allowed = owner.post(
        f"/programs/{PROGRAM_ID}/projects",
        headers={"Idempotency-Key": "owner-project"},
        json={"id": "project-owner-1", "name": "Borehole"},
    )
    assert allowed.status_code == 201, allowed.text


def test_a_claim_cannot_be_created_against_a_program_the_registry_has_not_confirmed():
    """Otherwise createClaim is submitted only to revert with UnknownProgram, and the
    operator learns about it from a worker log rather than from the request they made."""
    client = TestClient(app)
    sign_in(client, OPERATOR)
    created = client.post(
        "/programs",
        headers={"Idempotency-Key": "pending-program"},
        json={"id": "program-pending-wells", "name": "Pending Wells", "region": "Kisumu"},
    )
    assert created.json()["chainStatus"] == "PENDING"

    refused = client.post(
        "/claims",
        headers={"Idempotency-Key": "pending-claim"},
        json={
            "id": "claim-pending-1",
            "programId": "program-pending-wells",
            "projectId": "project-owner-1",
            "statement": "100 households reached.",
            "outcomeId": "outcome-pending-1",
        },
    )
    assert refused.status_code == 409
    assert "confirmed" in refused.json()["detail"]


def test_creating_a_program_is_idempotent_on_the_key():
    client = TestClient(app)
    sign_in(client, OPERATOR)
    body = {"id": "program-idempotent-1", "name": "Idempotent", "region": "Kisumu"}
    first = client.post("/programs", headers={"Idempotency-Key": "prog-once"}, json=body)
    second = client.post("/programs", headers={"Idempotency-Key": "prog-once"}, json=body)
    assert first.status_code == 201
    assert second.json() == first.json()
    # A second key for the same identifier is a different request, and the name is taken.
    conflict = client.post("/programs", headers={"Idempotency-Key": "prog-twice"}, json=body)
    assert conflict.status_code == 409


def test_an_operator_is_offered_only_its_own_organisations_programs():
    """The workspace named one project in its source. Replacing that with every tenant's
    projects would be a worse answer than the constant was."""
    make_outsider()
    owner = TestClient(app)
    sign_in(owner, OPERATOR)
    mine = owner.get("/operator/programs")
    assert mine.status_code == 200
    slugs = {item["id"] for item in mine.json()}
    assert PROGRAM_ID in slugs
    assert "program-outsider-wells" not in slugs

    outsider = TestClient(app)
    sign_in(outsider, OUTSIDER)
    theirs = {item["id"] for item in outsider.get("/operator/programs").json()}
    assert PROGRAM_ID not in theirs

    # An administrator is not scoped to one tenant, which is the point of the role.
    admin = TestClient(app)
    sign_in(admin, ADMIN)
    assert PROGRAM_ID in {item["id"] for item in admin.get("/operator/programs").json()}


def test_the_programs_offered_carry_the_projects_evidence_is_filed_under():
    owner = TestClient(app)
    sign_in(owner, OPERATOR)
    showcase = next(
        item for item in owner.get("/operator/programs").json() if item["id"] == PROGRAM_ID
    )
    assert showcase["chainStatus"] == "CONFIRMED"
    assert any(project["id"] == "project-water-12" for project in showcase["projects"])


def test_the_application_reads_an_unseeded_database_without_inventing_anything():
    """The showcase is one example tenant, not a precondition for the app existing.

    Every read used to resolve a hardcoded identifier somewhere, so an empty deployment
    either raised or answered with the seeded program. It should answer that there is
    nothing, which is a different and truthful thing.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from impactgraph.persistence import Base
    from impactgraph.read_model import TransparencyReadRepository

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as empty:
        repository = TransparencyReadRepository(empty)
        assert repository.programs() == []
        assert repository.claims() == []
        # A named record that does not exist is a lookup failure, not an empty answer:
        # "no such claim" and "this claim has no provenance" are different statements.
        with pytest.raises(LookupError):
            repository.program("program-nope")
        with pytest.raises(LookupError):
            repository.claim("claim-nope")
