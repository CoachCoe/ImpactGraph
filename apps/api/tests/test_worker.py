import io
import json
import logging
from uuid import UUID

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from impactgraph.blockchain import MockBlockchainService
from impactgraph.domain import Role
from impactgraph.observability import configure_logging
from impactgraph.persistence import (
    AuditLogRecord,
    Base,
    BlockchainOperationRecord,
    EvidenceRecord,
    OutboxRecord,
    ProcessedChainEventRecord,
)
from impactgraph.services import (
    ApplicationActor,
    DomainConflictError,
    EvidenceApplicationService,
    mark_evidence_reviewed,
)
from impactgraph.worker import BlockchainOutboxWorker


class FlakyBlockchain(MockBlockchainService):
    def __init__(self) -> None:
        super().__init__()
        self.fail_once = True

    def register_evidence(self, entity_id: str, program_id: str, commitment: str) -> str:
        if self.fail_once:
            self.fail_once = False
            raise ConnectionError("temporary RPC failure")
        return super().register_evidence(entity_id, program_id, commitment)


def factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def pending_registration(
    db_factory: sessionmaker[Session],
    program_id: str | None = "program-clean-water-kenya-2026",
) -> str:
    service = EvidenceApplicationService(chain_id=31337)
    actor = ApplicationActor("operator-1", Role.OPERATOR)
    with db_factory.begin() as db:
        service.create_uploaded(
            db,
            actor=actor,
            evidence_id="ev-worker",
            project_ref="project-water-12",
            evidence_type="INVOICE",
            storage_uri="file:///safe/ev-worker",
            content_hash="sha256:" + "a" * 64,
            mime_type="application/pdf",
            visibility="RESTRICTED",
            correlation_id="corr-worker",
            idempotency_key="create-worker",
        )
        evidence = db.scalar(
            select(EvidenceRecord).where(EvidenceRecord.external_id == "ev-worker")
        )
        assert evidence is not None
        evidence.metadata_json = (
            {} if program_id is None else {"programId": program_id}
        )
        mark_evidence_reviewed(db, "ev-worker")
    with db_factory.begin() as db:
        response = service.request_registration(
            db,
            actor=actor,
            evidence_id="ev-worker",
            correlation_id="corr-worker",
            idempotency_key="register-worker",
        )
    return response["operationId"]


def test_worker_submits_observes_event_and_advances_evidence():
    db_factory = factory()
    operation_id = pending_registration(db_factory)
    worker = BlockchainOutboxWorker(
        session_factory=db_factory,
        blockchain=MockBlockchainService(),
        confirmations_required=1,
    )
    result = worker.run_once()
    assert result.submitted == 1
    assert result.confirmed == 1
    assert result.failed == 0
    with db_factory() as db:
        evidence = db.scalar(
            select(EvidenceRecord).where(EvidenceRecord.external_id == "ev-worker")
        )
        operation = db.get(BlockchainOperationRecord, UUID(operation_id))
        assert evidence is not None and operation is not None
        assert evidence.workflow_status == "REGISTERED_ONCHAIN"
        assert evidence.blockchain_status == "CONFIRMED"
        assert operation.status == "CONFIRMED"
        assert db.scalar(select(func.count()).select_from(ProcessedChainEventRecord)) == 1
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditLogRecord)
                .where(AuditLogRecord.action == "BLOCKCHAIN_TX_CONFIRMED")
            )
            == 1
        )

    second = worker.run_once()
    assert second.submitted == 0
    assert second.confirmed == 0
    with db_factory() as db:
        assert db.scalar(select(func.count()).select_from(ProcessedChainEventRecord)) == 1
        assert db.scalar(select(func.count()).select_from(OutboxRecord)) == 1


class WrongCommitmentBlockchain(MockBlockchainService):
    def register_evidence(self, entity_id: str, program_id: str, commitment: str) -> str:
        return super().register_evidence(entity_id, program_id, "sha256:" + "b" * 64)


def test_worker_rejects_evidence_event_with_wrong_content_commitment():
    db_factory = factory()
    operation_id = pending_registration(db_factory)
    result = BlockchainOutboxWorker(
        session_factory=db_factory,
        blockchain=WrongCommitmentBlockchain(),
        confirmations_required=1,
    ).run_once()
    assert result.failed == 1
    with db_factory() as db:
        operation = db.get(BlockchainOperationRecord, UUID(operation_id))
        evidence = db.scalar(
            select(EvidenceRecord).where(EvidenceRecord.external_id == "ev-worker")
        )
        assert operation is not None and operation.status == "FAILED"
        assert "different bytes" in (operation.error or "")
        assert evidence is not None and evidence.workflow_status == "REGISTRATION_FAILED"


def test_failed_submission_is_recoverable_without_duplicate_logical_evidence():
    db_factory = factory()
    pending_registration(db_factory)
    worker = BlockchainOutboxWorker(
        session_factory=db_factory,
        blockchain=FlakyBlockchain(),
        confirmations_required=1,
    )
    failed = worker.run_once()
    assert failed == failed.__class__(submitted=0, confirmed=0, failed=1)
    with db_factory() as db:
        evidence = db.scalar(
            select(EvidenceRecord).where(EvidenceRecord.external_id == "ev-worker")
        )
        assert evidence is not None
        assert evidence.workflow_status == "REGISTRATION_FAILED"
        assert db.scalar(select(func.count()).select_from(EvidenceRecord)) == 1

    recovered = worker.run_once()
    assert recovered.submitted == 1
    assert recovered.confirmed == 1
    with db_factory() as db:
        evidence = db.scalar(
            select(EvidenceRecord).where(EvidenceRecord.external_id == "ev-worker")
        )
        assert evidence is not None
        assert evidence.workflow_status == "REGISTERED_ONCHAIN"
        assert db.scalar(select(func.count()).select_from(EvidenceRecord)) == 1


def test_registration_is_refused_when_the_evidence_has_no_program():
    """Fail closed. Both of these previously defaulted to the showcase program.

    With a default, evidence carrying no program was registered against whichever program
    the demo happened to use, and the onchain check then compared the event to that guess
    rather than to the record being registered.
    """
    db_factory = factory()
    with pytest.raises(DomainConflictError, match="no program to register against"):
        pending_registration(db_factory, program_id=None)


def test_the_worker_also_refuses_an_event_it_cannot_bind_to_a_program():
    """Defence in depth: the service guard above is not the only thing holding this."""
    db_factory = factory()
    operation_id = pending_registration(db_factory)
    with db_factory.begin() as db:
        evidence = db.scalar(
            select(EvidenceRecord).where(EvidenceRecord.external_id == "ev-worker")
        )
        # Simulate the metadata being lost between registration and confirmation.
        evidence.metadata_json = {}
    result = BlockchainOutboxWorker(
        session_factory=db_factory,
        blockchain=MockBlockchainService(),
        confirmations_required=1,
    ).run_once()
    assert result.failed == 1
    with db_factory() as db:
        operation = db.get(BlockchainOperationRecord, UUID(operation_id))
        assert operation is not None and operation.status == "FAILED"
        assert "no program to bind" in (operation.error or "")


def test_onchain_mismatch_is_logged_with_the_persisted_status():
    """The receipt confirms, but it commits bytes nobody reviewed.

    `confirm_operation` returns CONFIRMED for a receipt carrying the expected event; only
    the onchain comparison inside the transaction lowers it. This path used to return
    before reaching the log, so the one observation an operator most needs to see -- the
    registry committing something other than what was reviewed -- produced no line at
    all, and the line it would have produced carried the pre-comparison status.
    """
    db_factory = factory()
    pending_registration(db_factory)
    worker = BlockchainOutboxWorker(
        session_factory=db_factory,
        blockchain=WrongCommitmentBlockchain(),
        confirmations_required=1,
    )

    root = logging.getLogger()
    previous = root.handlers[:]
    buffer = io.StringIO()
    configure_logging(stream=buffer)
    try:
        result = worker.run_once()
    finally:
        root.handlers = previous

    assert result.confirmed == 0
    assert result.failed == 1
    with db_factory() as db:
        evidence = db.scalar(
            select(EvidenceRecord).where(EvidenceRecord.external_id == "ev-worker")
        )
        assert evidence is not None
        assert evidence.blockchain_status == "FAILED"

    observed = [
        json.loads(line)
        for line in buffer.getvalue().strip().splitlines()
        if '"chain.operation_observed"' in line
    ]
    assert observed, "the observation should be logged even when it fails the onchain check"
    assert observed[-1]["status"] == "FAILED"
