from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from impactgraph.domain import Role
from impactgraph.persistence import (
    AuditLogRecord,
    Base,
    BlockchainOperationRecord,
    EvidenceRecord,
    IdempotencyRecord,
    OutboxRecord,
)
from impactgraph.services import (
    ApplicationActor,
    AuthorizationError,
    EvidenceApplicationService,
    IdempotencyConflictError,
    mark_evidence_reviewed,
)


def session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_create_evidence_is_idempotent_and_audited():
    db = session()
    service = EvidenceApplicationService()
    actor = ApplicationActor("operator-1", Role.OPERATOR)
    args = {
        "actor": actor,
        "evidence_id": "ev-new",
        "project_ref": "project-water-12",
        "evidence_type": "INVOICE",
        "storage_uri": "file:///safe/ev-new",
        "content_hash": "sha256:" + "a" * 64,
        "mime_type": "application/pdf",
        "visibility": "RESTRICTED",
        "correlation_id": "corr-1",
        "idempotency_key": "idem-create-1",
    }
    with db.begin():
        first = service.create_uploaded(db, **args)
    with db.begin():
        replay = service.create_uploaded(db, **args)
    assert replay == first
    assert db.scalar(select(func.count()).select_from(EvidenceRecord)) == 1
    assert db.scalar(select(func.count()).select_from(AuditLogRecord)) == 1
    assert db.scalar(select(func.count()).select_from(IdempotencyRecord)) == 1


def test_idempotency_key_reuse_with_changed_request_is_rejected():
    db = session()
    service = EvidenceApplicationService()
    actor = ApplicationActor("operator-1", Role.OPERATOR)
    common = {
        "actor": actor,
        "project_ref": "project-water-12",
        "evidence_type": "INVOICE",
        "storage_uri": "file:///safe/evidence",
        "mime_type": "application/pdf",
        "visibility": "RESTRICTED",
        "correlation_id": "corr-1",
        "idempotency_key": "same-key",
    }
    with db.begin():
        service.create_uploaded(db, evidence_id="ev-1", content_hash="sha256:" + "a" * 64, **common)
    try:
        with db.begin():
            service.create_uploaded(
                db, evidence_id="ev-2", content_hash="sha256:" + "b" * 64, **common
            )
    except IdempotencyConflictError:
        pass
    else:
        raise AssertionError("Changed request incorrectly replayed")


def test_registration_atomically_creates_operation_outbox_and_audit():
    db = session()
    service = EvidenceApplicationService()
    actor = ApplicationActor("operator-1", Role.OPERATOR)
    with db.begin():
        service.create_uploaded(
            db,
            actor=actor,
            evidence_id="ev-1",
            project_ref="project-water-12",
            evidence_type="INVOICE",
            storage_uri="file:///safe/ev-1",
            content_hash="sha256:" + "a" * 64,
            mime_type="application/pdf",
            visibility="RESTRICTED",
            correlation_id="corr-create",
            idempotency_key="create-key",
        )
        # The upload route records the program the project belongs to; registration
        # refuses without it, because there would be nothing to commit against onchain.
        evidence = db.scalar(select(EvidenceRecord).where(EvidenceRecord.external_id == "ev-1"))
        evidence.metadata_json = {"programId": "program-clean-water-kenya-2026"}
        mark_evidence_reviewed(db, "ev-1")
    with db.begin():
        response = service.request_registration(
            db,
            actor=actor,
            evidence_id="ev-1",
            correlation_id="corr-register",
            idempotency_key="register-key",
        )
    assert response["workflowStatus"] == "REGISTRATION_PENDING"
    assert db.scalar(select(func.count()).select_from(BlockchainOperationRecord)) == 1
    assert db.scalar(select(func.count()).select_from(OutboxRecord)) == 1
    assert (
        db.scalar(
            select(func.count())
            .select_from(AuditLogRecord)
            .where(AuditLogRecord.action == "EVIDENCE_REGISTRATION_REQUESTED")
        )
        == 1
    )


def test_donor_cannot_manage_evidence():
    db = session()
    service = EvidenceApplicationService()
    try:
        with db.begin():
            service.create_uploaded(
                db,
                actor=ApplicationActor("donor-1", Role.DONOR),
                evidence_id="ev-1",
                project_ref="project-water-12",
                evidence_type="INVOICE",
                storage_uri="file:///safe/ev-1",
                content_hash="sha256:" + "a" * 64,
                mime_type="application/pdf",
                visibility="RESTRICTED",
                correlation_id="corr",
                idempotency_key="key",
            )
    except AuthorizationError:
        pass
    else:
        raise AssertionError("Donor managed evidence")
