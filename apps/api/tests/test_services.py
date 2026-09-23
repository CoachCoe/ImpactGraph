import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from impactgraph.domain import Role
from impactgraph.persistence import (
    AuditLogRecord,
    BlockchainOperationRecord,
    EvidenceRecord,
    IdempotencyRecord,
    OutboxRecord,
)
from impactgraph.services import (
    ApplicationActor,
    AuthorizationError,
    DomainConflictError,
    EvidenceApplicationService,
    IdempotencyConflictError,
    mark_evidence_reviewed,
)
from tests.support import memory_session


def session() -> Session:
    return memory_session()


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


def test_evidence_cannot_be_registered_against_a_program_the_registry_lacks():
    """registerEvidence reverts with UnknownProgram, and the revert would surface as a
    worker failure hours later rather than as an answer to the operator's request.

    Before programs could be created through the API every program was on chain by the
    time anything referenced it. Now one can sit in PostgreSQL with its receipt pending.
    """
    db = memory_session(chain_status="PENDING")
    service = EvidenceApplicationService()
    actor = ApplicationActor("operator-1", Role.OPERATOR)
    with db.begin():
        service.create_uploaded(
            db,
            actor=actor,
            evidence_id="ev-pending-program",
            project_ref="project-water-12",
            evidence_type="INVOICE",
            storage_uri="file:///safe/ev-pending",
            content_hash="sha256:" + "a" * 64,
            mime_type="application/pdf",
            visibility="RESTRICTED",
            correlation_id="corr-pending",
            idempotency_key="pending-key",
        )
        evidence = db.scalar(
            select(EvidenceRecord).where(EvidenceRecord.external_id == "ev-pending-program")
        )
        evidence.metadata_json = {"programId": "program-clean-water-kenya-2026"}
        mark_evidence_reviewed(db, "ev-pending-program")

    with db.begin(), pytest.raises(DomainConflictError, match="PENDING on chain"):
        service.request_registration(
            db,
            actor=actor,
            evidence_id="ev-pending-program",
            correlation_id="corr-pending",
            idempotency_key="pending-register",
        )


def test_registration_names_a_program_that_does_not_exist_at_all():
    db = memory_session()
    service = EvidenceApplicationService()
    actor = ApplicationActor("operator-1", Role.OPERATOR)
    with db.begin():
        service.create_uploaded(
            db,
            actor=actor,
            evidence_id="ev-ghost-program",
            project_ref="project-water-12",
            evidence_type="INVOICE",
            storage_uri="file:///safe/ev-ghost",
            content_hash="sha256:" + "a" * 64,
            mime_type="application/pdf",
            visibility="RESTRICTED",
            correlation_id="corr-ghost",
            idempotency_key="ghost-key",
        )
        evidence = db.scalar(
            select(EvidenceRecord).where(EvidenceRecord.external_id == "ev-ghost-program")
        )
        evidence.metadata_json = {"programId": "program-does-not-exist"}
        mark_evidence_reviewed(db, "ev-ghost-program")

    with db.begin(), pytest.raises(DomainConflictError, match="does not exist"):
        service.request_registration(
            db,
            actor=actor,
            evidence_id="ev-ghost-program",
            correlation_id="corr-ghost",
            idempotency_key="ghost-register",
        )
