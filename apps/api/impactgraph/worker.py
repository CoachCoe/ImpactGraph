from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from web3 import Web3

from .blockchain import (
    INDEPENDENT_VERIFIER_ATTESTATION,
    BlockchainOperation,
    BlockchainService,
    ReceiptObservation,
    confirm_operation,
    digest_bytes,
    entity_id_bytes,
)
from .domain import BlockchainStatus, ClaimStatus, EvidenceWorkflowStatus
from .hashing import program_hash
from .metrics import chain_operations, outbox_submissions
from .notifications import enqueue_claim_status_change
from .observability import correlation_context, logger
from .persistence import (
    AttestationRecord,
    AuditLogRecord,
    BlockchainOperationRecord,
    ClaimRecord,
    EvidenceRecord,
    OutboxRecord,
    ProcessedChainEventRecord,
    ProgramRecord,
)
from .verification import evaluate_persisted_claim

log = logger("impactgraph.worker")

#: Topics this worker claims. Others belong to a different sender.
BLOCKCHAIN_TOPIC_PREFIX = "blockchain."


@dataclass(frozen=True)
class WorkerResult:
    submitted: int = 0
    confirmed: int = 0
    failed: int = 0


class BlockchainOutboxWorker:
    """Polls durable intent without holding a database transaction across RPC calls."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        blockchain: BlockchainService,
        confirmations_required: int,
    ) -> None:
        self.session_factory = session_factory
        self.blockchain = blockchain
        self.confirmations_required = confirmations_required

    def run_once(self, batch_size: int = 20) -> WorkerResult:
        submitted, submission_failures = self.submit_pending(batch_size)
        confirmed, observation_failures = self.observe_submitted(batch_size)
        return WorkerResult(submitted, confirmed, submission_failures + observation_failures)

    def submit_pending(self, batch_size: int = 20) -> tuple[int, int]:
        with self.session_factory() as session, session.begin():
            ids = list(
                session.scalars(
                    select(OutboxRecord.id)
                    .where(
                        OutboxRecord.processed_at.is_(None),
                        # Claim only what this worker knows how to send. The outbox is
                        # shared, and an unknown topic here is marked failed and its
                        # entity looked up as evidence, which it is not.
                        OutboxRecord.topic.startswith(BLOCKCHAIN_TOPIC_PREFIX),
                    )
                    .order_by(OutboxRecord.created_at)
                    .limit(batch_size)
                    .with_for_update(skip_locked=True)
                )
            )
        results = [self._submit_one(outbox_id) for outbox_id in ids]
        return sum(result is True for result in results), sum(result is False for result in results)

    def _submit_one(self, outbox_id: UUID) -> bool | None:
        with self.session_factory() as session, session.begin():
            outbox = session.get(OutboxRecord, outbox_id)
            if outbox is None or outbox.processed_at is not None:
                return None
            operation = session.get(BlockchainOperationRecord, UUID(outbox.payload["operationId"]))
            if operation is None:
                outbox.attempts += 1
                return None
            if operation.transaction_hash:
                outbox.processed_at = datetime.now(UTC)
                return None
            topic, payload = outbox.topic, dict(outbox.payload)
            correlation_id = outbox.correlation_id

        with correlation_context(correlation_id):
            return self._submit_prepared(outbox_id, topic, payload)

    def _mark_entity_submitted(
        self, session: Session, operation: BlockchainOperationRecord
    ) -> None:
        """Evidence carries a workflow status of its own; a program and a claim do not.

        Both remain PENDING until the receipt is observed, because a submitted transaction
        is not a created entity and nothing may treat it as one.
        """
        if operation.operation_type == "REGISTER_EVIDENCE":
            evidence = self._evidence(session, operation.entity_id)
            evidence.workflow_status = EvidenceWorkflowStatus.REGISTRATION_PENDING
            evidence.blockchain_status = BlockchainStatus.SUBMITTED

    def _mark_entity_failed(
        self, session: Session, operation: BlockchainOperationRecord
    ) -> None:
        """What a failed submission means for the row the operation was created for.

        Dispatched rather than assumed: this used to reach straight for the evidence
        record, which is a LookupError for any operation whose entity is not evidence.
        """
        if operation.operation_type == "REGISTER_EVIDENCE":
            evidence = self._evidence(session, operation.entity_id)
            evidence.workflow_status = EvidenceWorkflowStatus.REGISTRATION_FAILED
            evidence.blockchain_status = BlockchainStatus.FAILED
        elif operation.operation_type == "CREATE_PROGRAM_ENTITY":
            program = session.scalar(
                select(ProgramRecord).where(ProgramRecord.slug == operation.entity_id)
            )
            if program:
                program.chain_status = "FAILED"
        elif operation.operation_type == "CREATE_CLAIM_ENTITY":
            claim = session.scalar(
                select(ClaimRecord).where(ClaimRecord.external_id == operation.entity_id)
            )
            if claim:
                claim.status = "CREATION_FAILED"

    def _submit_prepared(
        self, outbox_id: UUID, topic: str, payload: dict[str, Any]
    ) -> bool | None:
        try:
            if topic == "blockchain.register_evidence":
                transaction_hash = self.blockchain.register_evidence(
                    payload["evidenceId"], payload["programId"], payload["contentHash"]
                )
            elif topic == "blockchain.create_program":
                transaction_hash = self.blockchain.create_program(
                    payload["programId"], payload["commitment"]
                )
            elif topic == "blockchain.create_claim":
                transaction_hash = self.blockchain.create_claim(
                    payload["claimId"], payload["programId"], payload["commitment"]
                )
            else:
                raise ValueError(f"Unsupported outbox topic: {topic}")
        except Exception as exc:  # noqa: BLE001 -- persist every external adapter failure
            with self.session_factory() as session, session.begin():
                outbox = session.get(OutboxRecord, outbox_id)
                operation = session.get(BlockchainOperationRecord, UUID(payload["operationId"]))
                if outbox is None or operation is None:
                    return None
                outbox.attempts += 1
                operation.status, operation.error = BlockchainStatus.FAILED, str(exc)
                self._mark_entity_failed(session, operation)
                self._audit(
                    session,
                    operation,
                    "BLOCKCHAIN_TX_FAILED",
                    {"error": str(exc), "attempt": outbox.attempts},
                )
                entity_id, attempt = operation.entity_id, outbox.attempts
            log.warning(
                "outbox.submission_failed",
                topic=topic,
                entity_id=entity_id,
                error_type=exc.__class__.__name__,
                attempt=attempt,
            )
            outbox_submissions.labels(topic=topic, outcome="failed").inc()
            return False

        with self.session_factory() as session, session.begin():
            outbox = session.get(OutboxRecord, outbox_id)
            operation = session.get(BlockchainOperationRecord, UUID(payload["operationId"]))
            if outbox is None or operation is None:
                return None
            operation.transaction_hash = transaction_hash
            operation.status, operation.error = BlockchainStatus.SUBMITTED, None
            self._mark_entity_submitted(session, operation)
            outbox.processed_at = datetime.now(UTC)
            self._audit(
                session, operation, "BLOCKCHAIN_TX_SUBMITTED", {"transactionHash": transaction_hash}
            )
            entity_id = operation.entity_id
        # After the block, so a rolled-back commit cannot leave a log line claiming a
        # transition that did not happen.
        log.info("outbox.submitted", topic=topic, entity_id=entity_id, transaction_hash=transaction_hash)
        outbox_submissions.labels(topic=topic, outcome="submitted").inc()
        return True

    def observe_submitted(self, batch_size: int = 20) -> tuple[int, int]:
        with self.session_factory() as session:
            ids = list(
                session.scalars(
                    select(BlockchainOperationRecord.id)
                    .where(BlockchainOperationRecord.status == BlockchainStatus.SUBMITTED)
                    .order_by(BlockchainOperationRecord.created_at)
                    .limit(batch_size)
                )
            )
        results = [self._observe_one(operation_id) for operation_id in ids]
        return sum(result == BlockchainStatus.CONFIRMED for result in results), sum(
            result == BlockchainStatus.FAILED for result in results
        )

    def observe_operation(self, operation_id: UUID) -> BlockchainStatus | None:
        return self._observe_one(operation_id)

    def _observe_one(self, operation_id: UUID) -> BlockchainStatus | None:
        with self.session_factory() as session:
            record = session.get(BlockchainOperationRecord, operation_id)
            if (
                record is None
                or record.status != BlockchainStatus.SUBMITTED
                or not record.transaction_hash
            ):
                return None
            operation = BlockchainOperation(
                str(record.id),
                record.entity_id,
                record.operation_type,
                record.expected_event,
                BlockchainStatus(record.status),
                record.transaction_hash,
                record.correlation_id,
                record.confirmations,
                record.error,
            )

        with correlation_context(operation.correlation_id):
            return self._observe_prepared(operation_id, operation)

    def _observe_prepared(
        self, operation_id: UUID, operation: BlockchainOperation
    ) -> BlockchainStatus | None:
        observation = self.blockchain.get_transaction(operation.transaction_hash or "")
        result = confirm_operation(operation, observation, self.confirmations_required)
        if observation is None:
            return result
        decided = self._apply_observation(operation_id, operation, observation, result)
        if decided is None:
            return None
        status, entity_id, operation_type, confirmations = decided
        # The persisted status, not `result`: the onchain checks inside the transaction
        # lower a CONFIRMED receipt to FAILED when the registry committed something other
        # than what was reviewed, and that is the answer every caller wants.
        log.info(
            "chain.operation_observed",
            operation_type=operation_type,
            entity_id=entity_id,
            status=status,
            confirmations=confirmations,
        )
        if status != BlockchainStatus.SUBMITTED:
            # Only the transition. An operation waiting for confirmation depth is re-read
            # on every tick and stays SUBMITTED, so counting each read would measure the
            # polling interval rather than any work done.
            chain_operations.labels(operation_type=operation_type, status=status).inc()
        return BlockchainStatus(status)

    def _apply_observation(
        self,
        operation_id: UUID,
        operation: BlockchainOperation,
        observation: ReceiptObservation,
        result: BlockchainStatus,
    ) -> tuple[str, str, str, int] | None:
        """Persist what the receipt proves, and report the state that was committed.

        Returns after the transaction commits, so a caller that logs the outcome cannot
        announce a transition that rolled back.
        """
        with self.session_factory() as session, session.begin():
            record = session.get(BlockchainOperationRecord, operation_id)
            if record is None or record.status != BlockchainStatus.SUBMITTED:
                return None
            record.confirmations, record.status, record.error = (
                operation.confirmations,
                result,
                operation.error,
            )
            if result == BlockchainStatus.CONFIRMED:
                self._record_events_once(
                    session, record.chain_id, observation.transaction_hash, observation.events
                )
                if record.operation_type == "REGISTER_EVIDENCE":
                    evidence = self._evidence(session, record.entity_id)
                    onchain_error = self._onchain_evidence_mismatch(
                        observation.events, evidence
                    )
                    if onchain_error is not None:
                        record.status, record.error = BlockchainStatus.FAILED, onchain_error
                        evidence.workflow_status, evidence.blockchain_status = (
                            EvidenceWorkflowStatus.REGISTRATION_FAILED,
                            BlockchainStatus.FAILED,
                        )
                        self._audit(session, record, "BLOCKCHAIN_TX_FAILED", {"error": onchain_error})
                        return (
                            record.status,
                            record.entity_id,
                            record.operation_type,
                            record.confirmations,
                        )
                    evidence.workflow_status, evidence.blockchain_status = (
                        EvidenceWorkflowStatus.REGISTERED_ONCHAIN,
                        BlockchainStatus.CONFIRMED,
                    )
                    metadata = dict(evidence.metadata_json)
                    metadata["blockchainReference"] = {
                        "transactionHash": observation.transaction_hash,
                        "blockNumber": observation.block_number,
                        # The chain's clock, not ours: the moment the commitment
                        # became unalterable, which is what the donor is told about.
                        "registeredAt": self.blockchain.block_time(observation.block_number),
                    }
                    metadata["registeredContentHash"] = evidence.content_hash
                    evidence.metadata_json = metadata
                    self._audit(
                        session, record, "BLOCKCHAIN_TX_CONFIRMED", metadata["blockchainReference"]
                    )
                elif record.operation_type == "CREATE_VERIFIER_ATTESTATION":
                    self._confirm_verifier_attestation(
                        session, record, observation
                    )
                elif record.operation_type == "CREATE_PROGRAM_ENTITY":
                    program = session.scalar(
                        select(ProgramRecord).where(ProgramRecord.slug == record.entity_id)
                    )
                    if program is None:
                        raise LookupError("Program record is missing")
                    onchain_error = self._onchain_program_mismatch(observation.events, program)
                    if onchain_error:
                        record.status, record.error = BlockchainStatus.FAILED, onchain_error
                        program.chain_status = "FAILED"
                        self._audit(
                            session, record, "BLOCKCHAIN_TX_FAILED", {"error": onchain_error}
                        )
                        return (
                            record.status,
                            record.entity_id,
                            record.operation_type,
                            record.confirmations,
                        )
                    program.chain_status = "CONFIRMED"
                    self._audit(
                        session,
                        record,
                        "BLOCKCHAIN_TX_CONFIRMED",
                        {"transactionHash": observation.transaction_hash},
                    )
                elif record.operation_type == "CREATE_CLAIM_ENTITY":
                    claim = session.scalar(
                        select(ClaimRecord).where(ClaimRecord.external_id == record.entity_id)
                    )
                    if claim is None:
                        raise LookupError("Claim record is missing")
                    onchain_error = self._onchain_claim_mismatch(observation.events, claim)
                    if onchain_error:
                        record.status, record.error = BlockchainStatus.FAILED, onchain_error
                        claim.status = "CREATION_FAILED"
                        self._audit(
                            session, record, "BLOCKCHAIN_TX_FAILED", {"error": onchain_error}
                        )
                        return (
                            record.status,
                            record.entity_id,
                            record.operation_type,
                            record.confirmations,
                        )
                    self._audit(
                        session,
                        record,
                        "BLOCKCHAIN_TX_CONFIRMED",
                        {"transactionHash": observation.transaction_hash},
                    )
            elif result == BlockchainStatus.FAILED:
                if record.operation_type in {
                    "REGISTER_EVIDENCE",
                    "CREATE_PROGRAM_ENTITY",
                    "CREATE_CLAIM_ENTITY",
                }:
                    self._mark_entity_failed(session, record)
                elif record.operation_type == "CREATE_VERIFIER_ATTESTATION":
                    attestation = session.scalar(
                        select(AttestationRecord).where(
                            AttestationRecord.external_id == record.entity_id
                        )
                    )
                    if attestation:
                        attestation.status = "FAILED"
                self._audit(session, record, "BLOCKCHAIN_TX_FAILED", {"error": operation.error})
            return (
                record.status,
                record.entity_id,
                record.operation_type,
                record.confirmations,
            )

    @staticmethod
    def _evidence(session: Session, evidence_id: str) -> EvidenceRecord:
        evidence = session.scalar(
            select(EvidenceRecord).where(EvidenceRecord.external_id == evidence_id)
        )
        if evidence is None:
            raise LookupError(f"Evidence not found for blockchain operation: {evidence_id}")
        return evidence

    @staticmethod
    def _audit(
        session: Session, operation: BlockchainOperationRecord, action: str, metadata: dict
    ) -> None:
        session.add(
            AuditLogRecord(
                actor_id="system:blockchain-worker",
                action=action,
                entity_type="ATTESTATION"
                if operation.operation_type == "CREATE_VERIFIER_ATTESTATION"
                else "EVIDENCE",
                entity_id=operation.entity_id,
                metadata_json=metadata,
                correlation_id=operation.correlation_id,
            )
        )

    @staticmethod
    def _onchain_attestation_mismatch(
        events: Sequence[dict[str, Any]],
        entity_id: str,
        claim: ClaimRecord,
        attestation: AttestationRecord | None = None,
        sender: str | None = None,
    ) -> str | None:
        """Compare what the chain actually recorded against the claim being verified.

        The database copy of the bundle hash is written by the backend from the same row the
        claim holds, so comparing the two proves nothing about what the wallet signed. The
        binding only becomes real once the value committed onchain is read back.
        """
        expected_entity = Web3.to_hex(entity_id_bytes(entity_id))
        event = next(
            (
                item
                for item in events
                if item.get("event") == "AttestationCreated"
                and item.get("entityId") in {entity_id, expected_entity}
            ),
            None,
        )
        if event is None:
            return "No AttestationCreated event for this attestation was found in the receipt"
        args = event.get("args") or {}
        committed_bundle = args.get("verificationBundleHash")
        if committed_bundle is None:
            return "The AttestationCreated event carried no verification bundle hash"
        if committed_bundle != Web3.to_hex(digest_bytes(claim.verification_bundle_hash)):
            return "The attestation onchain commits to a different verification bundle"
        if args.get("attestationType") != INDEPENDENT_VERIFIER_ATTESTATION:
            return "The attestation onchain is not an independent verifier attestation"
        if attestation is not None:
            expected_wallet = Web3.to_checksum_address(attestation.issuer_wallet)
            issuer = args.get("issuer")
            if not issuer or Web3.to_checksum_address(issuer) != expected_wallet:
                return "The onchain attestation issuer does not match the bound verifier wallet"
            if not sender or Web3.to_checksum_address(sender) != expected_wallet:
                return "The transaction sender does not match the bound verifier wallet"
            if args.get("subjectId") != Web3.to_hex(entity_id_bytes(attestation.subject_id)):
                return "The onchain attestation covers a different subject"
            if args.get("statementHash") != Web3.to_hex(digest_bytes(attestation.statement_hash)):
                return "The onchain attestation commits to a different statement"
        return None

    @staticmethod
    def _onchain_evidence_mismatch(
        events: Sequence[dict[str, Any]], evidence: EvidenceRecord
    ) -> str | None:
        expected_entity = Web3.to_hex(entity_id_bytes(evidence.external_id))
        event = next(
            (
                item
                for item in events
                if item.get("event") == "EvidenceRegistered"
                and item.get("entityId") in {evidence.external_id, expected_entity}
            ),
            None,
        )
        if event is None:
            return "No EvidenceRegistered event for this evidence was found in the receipt"
        args = event.get("args") or {}
        # Defaulting to the showcase program here would bind a registration to whatever
        # program the demo happens to use. Without a program there is nothing to check
        # the event against, so refuse rather than confirm against a guess.
        program_id = (evidence.metadata_json or {}).get("programId")
        if not program_id:
            return "The evidence record has no program to bind this registration to"
        if args.get("programId") != Web3.to_hex(entity_id_bytes(program_id)):
            return "The onchain evidence registration names a different program"
        if args.get("contentHash") != Web3.to_hex(digest_bytes(evidence.content_hash)):
            return "The onchain evidence registration commits to different bytes"
        return None

    @staticmethod
    def _onchain_program_mismatch(
        events: Sequence[dict[str, Any]], program: ProgramRecord
    ) -> str | None:
        """A receipt is not proof that this program was created.

        Any contract can emit a ProgramCreated, and any transaction can carry one for a
        different program, so the event is matched by entity and checked against the
        commitment this service computed rather than taken as an outcome.
        """
        expected_entity = Web3.to_hex(entity_id_bytes(program.slug))
        event = next(
            (
                item
                for item in events
                if item.get("event") == "ProgramCreated"
                and item.get("entityId") in {program.slug, expected_entity}
            ),
            None,
        )
        if event is None:
            return "No ProgramCreated event for this program was found in the receipt"
        args = event.get("args") or {}
        expected = Web3.to_hex(digest_bytes(program_hash(program.slug)))
        if args.get("commitment") != expected:
            return "The onchain program commits to a different identifier"
        return None

    @staticmethod
    def _onchain_claim_mismatch(
        events: Sequence[dict[str, Any]], claim: ClaimRecord
    ) -> str | None:
        expected_entity = Web3.to_hex(entity_id_bytes(claim.external_id))
        event = next(
            (
                item
                for item in events
                if item.get("event") == "ClaimCreated"
                and item.get("entityId") in {claim.external_id, expected_entity}
            ),
            None,
        )
        if event is None:
            return "No ClaimCreated event for this claim was found in the receipt"
        args = event.get("args") or {}
        if args.get("programId") != Web3.to_hex(entity_id_bytes(claim.program_ref)):
            return "The onchain claim names a different program"
        if args.get("claimHash") != Web3.to_hex(digest_bytes(claim.payload_hash)):
            return "The onchain claim commits to a different statement"
        return None

    def _confirm_verifier_attestation(
        self,
        session: Session,
        operation: BlockchainOperationRecord,
        observation: ReceiptObservation,
    ) -> None:
        attestation = session.scalar(
            select(AttestationRecord).where(AttestationRecord.external_id == operation.entity_id)
        )
        if attestation is None:
            raise LookupError("Verifier attestation record is missing")
        claim = session.scalar(
            select(ClaimRecord).where(ClaimRecord.external_id == attestation.subject_id)
        )
        if claim is None:
            raise LookupError("Attested claim is missing")
        if attestation.verification_bundle_hash != claim.verification_bundle_hash:
            operation.status, operation.error, attestation.status = (
                BlockchainStatus.FAILED,
                "Verifier attestation bundle does not match the current claim bundle",
                "FAILED",
            )
            return
        onchain_error = self._onchain_attestation_mismatch(
            observation.events,
            operation.entity_id,
            claim,
            attestation,
            observation.sender,
        )
        if onchain_error is not None:
            operation.status, operation.error, attestation.status = (
                BlockchainStatus.FAILED,
                onchain_error,
                "FAILED",
            )
            return
        attestation.status = "CONFIRMED"
        decision, _, _ = evaluate_persisted_claim(session, claim)
        if decision.status == ClaimStatus.VERIFIED:
            claim.status, claim.verified_at = "VERIFIED", datetime.now(UTC)
            enqueue_claim_status_change(
                session,
                claim_id=claim.external_id,
                status=claim.status,
                correlation_id=operation.correlation_id,
            )
        self._audit(
            session,
            operation,
            "ATTESTATION_CONFIRMED",
            {
                "transactionHash": operation.transaction_hash,
                "blockNumber": observation.block_number,
                "claimStatus": claim.status,
            },
        )
        if claim.status == "VERIFIED":
            session.add(
                AuditLogRecord(
                    actor_id="system:verification-policy",
                    action="CLAIM_VERIFIED",
                    entity_type="CLAIM",
                    entity_id=claim.external_id,
                    metadata_json={
                        "policyVersion": claim.verification_policy_version,
                        "verificationBundleHash": claim.verification_bundle_hash,
                    },
                    correlation_id=operation.correlation_id,
                )
            )

    @staticmethod
    def _record_events_once(
        session: Session, chain_id: int, transaction_hash: str, events: tuple[dict, ...]
    ) -> None:
        for event in events:
            log_index = int(event.get("logIndex", 0))
            existing = session.scalar(
                select(ProcessedChainEventRecord).where(
                    ProcessedChainEventRecord.chain_id == chain_id,
                    ProcessedChainEventRecord.transaction_hash == transaction_hash.lower(),
                    ProcessedChainEventRecord.log_index == log_index,
                )
            )
            if existing is None:
                session.add(
                    ProcessedChainEventRecord(
                        chain_id=chain_id,
                        transaction_hash=transaction_hash.lower(),
                        log_index=log_index,
                        event_name=str(event["event"]),
                    )
                )
