import io
import json
import logging
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from impactgraph.blockchain import MockBlockchainService
from impactgraph.domain import Role
from impactgraph.observability import configure_logging
from impactgraph.persistence import (
    AuditLogRecord,
    BlockchainOperationRecord,
    EvidenceRecord,
    OutboxRecord,
    ProcessedChainEventRecord,
    ProgramRecord,
)
from impactgraph.services import (
    ApplicationActor,
    DomainConflictError,
    EvidenceApplicationService,
    TenantApplicationService,
    mark_evidence_reviewed,
)
from impactgraph.worker import BlockchainOutboxWorker
from tests.support import memory_factory


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
    return memory_factory()


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


class WrongProgramCommitment(MockBlockchainService):
    def create_program(self, entity_id: str, commitment: str) -> str:
        return super().create_program(entity_id, "sha256:" + "c" * 64)


def pending_program(
    db_factory: sessionmaker[Session], program_id: str = "program-new-wells"
) -> str:
    service = TenantApplicationService(chain_id=31337)
    actor = ApplicationActor("org-global-water", Role.OPERATOR)
    with db_factory.begin() as db:
        response = service.create_program(
            db,
            actor=actor,
            program_id=program_id,
            name="New Wells",
            region="Kisumu",
            verification_threshold=1,
            correlation_id="corr-program",
            idempotency_key="program-key",
        )
    return response["operationId"]


def test_a_program_becomes_usable_only_once_its_registry_entity_is_observed():
    db_factory = memory_factory()
    pending_program(db_factory)
    with db_factory() as db:
        program = db.scalar(
            select(ProgramRecord).where(ProgramRecord.slug == "program-new-wells")
        )
        assert program is not None and program.chain_status == "PENDING"

    result = BlockchainOutboxWorker(
        session_factory=db_factory,
        blockchain=MockBlockchainService(),
        confirmations_required=1,
    ).run_once()
    assert result.confirmed == 1
    with db_factory() as db:
        program = db.scalar(
            select(ProgramRecord).where(ProgramRecord.slug == "program-new-wells")
        )
        assert program is not None and program.chain_status == "CONFIRMED"


def test_a_program_is_not_confirmed_by_an_event_committing_to_something_else():
    """Any contract can emit a ProgramCreated. The receipt is checked against the
    commitment this service computed, not accepted because it arrived."""
    db_factory = memory_factory()
    operation_id = pending_program(db_factory)
    result = BlockchainOutboxWorker(
        session_factory=db_factory,
        blockchain=WrongProgramCommitment(),
        confirmations_required=1,
    ).run_once()
    assert result.failed == 1
    with db_factory() as db:
        operation = db.get(BlockchainOperationRecord, UUID(operation_id))
        program = db.scalar(
            select(ProgramRecord).where(ProgramRecord.slug == "program-new-wells")
        )
        assert operation is not None and operation.status == "FAILED"
        assert "different identifier" in (operation.error or "")
        assert program is not None and program.chain_status == "FAILED"


def test_a_failed_program_submission_does_not_reach_for_an_evidence_record():
    """The failure path used to resolve the operation's entity as evidence unconditionally,
    which is a LookupError for every operation whose entity is not evidence."""

    class Unreachable(MockBlockchainService):
        def create_program(self, entity_id: str, commitment: str) -> str:
            raise ConnectionError("temporary RPC failure")

    db_factory = memory_factory()
    pending_program(db_factory)
    result = BlockchainOutboxWorker(
        session_factory=db_factory,
        blockchain=Unreachable(),
        confirmations_required=1,
    ).run_once()
    assert result.failed == 1
    with db_factory() as db:
        program = db.scalar(
            select(ProgramRecord).where(ProgramRecord.slug == "program-new-wells")
        )
        assert program is not None and program.chain_status == "FAILED"


def pending_role_grant(db_factory: sessionmaker[Session], wallet: str) -> str:
    from impactgraph.persistence import OrganizationRecord, UserRecord
    from impactgraph.services import OnboardingApplicationService

    with db_factory.begin() as db:
        organization = OrganizationRecord(
            external_id="org-audit", name="Audit Co", kind="VERIFIER"
        )
        db.add(organization)
        db.flush()
        db.add(
            UserRecord(
                email="check@audit.example",
                display_name="Auditor",
                password_hash="x",
                organization_id=organization.id,
                role=Role.VERIFIER,
                wallet_address=wallet,
            )
        )
    service = OnboardingApplicationService(chain_id=31337)
    with db_factory.begin() as db:
        response = service.grant_verifier_role(
            db,
            actor=ApplicationActor("org-impactgraph", Role.ADMIN),
            user_email="check@audit.example",
            correlation_id="corr-grant",
            idempotency_key="grant-key",
        )
    return response["operationId"]


def test_a_verifier_wallet_grant_is_confirmed_from_the_receipt():
    db_factory = memory_factory()
    wallet = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"
    operation_id = pending_role_grant(db_factory, wallet)
    result = BlockchainOutboxWorker(
        session_factory=db_factory,
        blockchain=MockBlockchainService(),
        confirmations_required=1,
    ).run_once()
    assert result.confirmed == 1
    with db_factory() as db:
        operation = db.get(BlockchainOperationRecord, UUID(operation_id))
        assert operation is not None and operation.status == "CONFIRMED"


def test_a_grant_that_names_somebody_else_does_not_confirm_this_one():
    """Rejected by the generic expected-event check before the role guard is reached: the
    event is indexed by the account it names, so a grant to another address is simply not
    the event this operation was waiting for."""

    class GrantsSomeoneElse(MockBlockchainService):
        def grant_verifier_role(self, address: str) -> str:
            return super().grant_verifier_role("0x000000000000000000000000000000000000dEaD")

    db_factory = memory_factory()
    operation_id = pending_role_grant(db_factory, "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC")
    result = BlockchainOutboxWorker(
        session_factory=db_factory,
        blockchain=GrantsSomeoneElse(),
        confirmations_required=1,
    ).run_once()
    assert result.failed == 1
    with db_factory() as db:
        operation = db.get(BlockchainOperationRecord, UUID(operation_id))
        assert operation is not None and "RoleGranted" in (operation.error or "")


def test_a_receipt_carrying_the_wrong_role_or_account_is_refused():
    """What the guard itself rejects, once an event with the right entity has arrived: a
    grant of some other role, or of this role to an address that is not the one asked for.
    """
    from web3 import Web3

    from impactgraph.blockchain import VERIFIER_ROLE

    wallet = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"
    guard = BlockchainOutboxWorker._onchain_role_mismatch

    correct = ({"event": "RoleGranted", "args": {"account": wallet, "role": Web3.to_hex(VERIFIER_ROLE)}},)
    assert guard(correct, wallet) is None
    # Case is not identity: an address is the same address however it is rendered.
    assert guard(correct, wallet.lower()) is None

    operator_role = ({"event": "RoleGranted", "args": {"account": wallet, "role": Web3.to_hex(Web3.keccak(text="OPERATOR"))}},)
    assert "different role" in (guard(operator_role, wallet) or "")

    other = ({"event": "RoleGranted", "args": {"account": "0x000000000000000000000000000000000000dEaD", "role": Web3.to_hex(VERIFIER_ROLE)}},)
    assert "different account" in (guard(other, wallet) or "")

    assert "No RoleGranted" in (guard((), wallet) or "")


def test_the_mock_names_the_same_entity_the_registry_would():
    """This mock was the more forgiving of the two, twice.

    It supplied an entityId directly while the EVM adapter derives one from the event's
    arguments, so a RoleGranted -- which names an account and no entity -- decoded here
    with an entity and on chain without one. The grant succeeded on Anvil and was recorded
    as a missing event. Every event this mock emits now carries the arguments the contract
    declares, and the entity is derived from them by the same function both sides use.
    """
    from web3 import Web3

    from impactgraph.blockchain import MockBlockchainService, entity_id_bytes, entity_id_from_args

    chain = MockBlockchainService(sender="0x" + "1" * 40)
    emitted = {
        "EvidenceRegistered": chain.register_evidence("ev-1", "program-1", "sha256:" + "a" * 64),
        "ProgramCreated": chain.create_program("program-1", "sha256:" + "b" * 64),
        "ClaimCreated": chain.create_claim("claim-1", "program-1", "sha256:" + "c" * 64),
        "OutcomeRecorded": chain.record_outcome("outcome-1", "program-1", "sha256:" + "d" * 64),
        "AttestationCreated": chain.create_attestation(
            "att-1", "claim-1", "sha256:" + "e" * 64, "sha256:" + "f" * 64
        ),
    }
    for entity, transaction in zip(
        ["ev-1", "program-1", "claim-1", "outcome-1", "att-1"], emitted.values(), strict=True
    ):
        event = chain.receipts[transaction].events[0]
        assert event["entityId"] == entity_id_from_args(event["args"]), event["event"]
        assert event["entityId"] == Web3.to_hex(entity_id_bytes(entity)), event["event"]

    # RoleGranted is the exception that started this: it names an account, not an entity.
    wallet = "0x90F79bf6EB2c4f870365E785982E1f101E93b906"
    grant = chain.receipts[chain.grant_verifier_role(wallet)].events[0]
    assert grant["entityId"] == wallet
    assert entity_id_from_args(grant["args"]) == wallet


def test_an_address_matches_however_it_is_capitalised():
    """Checksum capitals are a checksum, not an identity, and the two sides of this
    comparison are rendered by different libraries."""
    from impactgraph.blockchain import BlockchainOperation, MockBlockchainService, confirm_operation
    from impactgraph.domain import BlockchainStatus

    wallet = "0x90F79bf6EB2c4f870365E785982E1f101E93b906"
    chain = MockBlockchainService()
    transaction = chain.grant_verifier_role(wallet)
    operation = BlockchainOperation(
        "op", wallet.lower(), "GRANT_VERIFIER_ROLE", "RoleGranted",
        BlockchainStatus.SUBMITTED, transaction, "corr", 0, None,
    )
    assert confirm_operation(operation, chain.get_transaction(transaction), 1) == "CONFIRMED"
