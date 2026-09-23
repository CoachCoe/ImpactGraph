from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from .blockchain import digest_bytes, entity_id_bytes
from .domain import BlockchainStatus, EvidenceWorkflowStatus, Role
from .hashing import claim_hash, hash_fields, program_hash
from .notifications import enqueue_claim_status_change
from .persistence import (
    AttestationRecord,
    AuditLogRecord,
    BlockchainOperationRecord,
    ClaimRecord,
    DataProtectionRecord,
    DomainEntityRecord,
    EvidenceRecord,
    FundingRecord,
    IdempotencyRecord,
    OrganizationRecord,
    OutboxRecord,
    ProgramRecord,
    UserRecord,
    as_utc_iso,
    public_funder_name,
)
from .verification import restate_claims_for


class AuthorizationError(PermissionError):
    pass


class IdempotencyConflictError(ValueError):
    pass


class DomainConflictError(ValueError):
    pass


@dataclass(frozen=True)
class ApplicationActor:
    id: str
    role: Role
    wallet: str | None = None


def operating_organization(session: Session, claim: ClaimRecord) -> str | None:
    """The organisation that operates the program a claim belongs to.

    Separation of duties is decided against this rather than a hardcoded identifier, so
    the rule holds for any program rather than only the seeded showcase.
    """
    program = session.scalar(
        select(ProgramRecord).where(ProgramRecord.slug == claim.program_ref)
    )
    return program.operator_org_ref if program else None


class AuditService:
    def record(
        self,
        session: Session,
        *,
        actor: ApplicationActor,
        action: str,
        entity_type: str,
        entity_id: str,
        correlation_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> AuditLogRecord:
        entry = AuditLogRecord(
            actor_id=actor.id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            metadata_json=metadata or {},
            correlation_id=correlation_id,
        )
        session.add(entry)
        return entry


class IdempotencyService:
    def replay_or_validate(
        self, session: Session, *, key: str, operation: str, request_hash: str
    ) -> dict[str, Any] | None:
        existing = session.scalar(select(IdempotencyRecord).where(IdempotencyRecord.key == key))
        if existing is None:
            return None
        if existing.operation != operation or existing.request_hash != request_hash:
            raise IdempotencyConflictError(
                "Idempotency key was already used for a different logical request"
            )
        return existing.response_body

    def remember(
        self,
        session: Session,
        *,
        key: str,
        operation: str,
        request_hash: str,
        response_status: int,
        response_body: dict[str, Any],
    ) -> None:
        session.add(
            IdempotencyRecord(
                key=key,
                operation=operation,
                request_hash=request_hash,
                response_status=response_status,
                response_body=response_body,
            )
        )


class EvidenceApplicationService:
    """Coordinates evidence operations; HTTP handlers only translate requests/responses."""

    def __init__(self, chain_id: int = 31337) -> None:
        self.audit = AuditService()
        self.idempotency = IdempotencyService()
        self.chain_id = chain_id

    @staticmethod
    def _operator(actor: ApplicationActor) -> None:
        if actor.role not in {Role.OPERATOR, Role.ADMIN}:
            raise AuthorizationError("Only an operator or administrator may manage evidence")

    def create_uploaded(
        self,
        session: Session,
        *,
        actor: ApplicationActor,
        evidence_id: str,
        project_ref: str,
        evidence_type: str,
        storage_uri: str,
        content_hash: str,
        mime_type: str,
        visibility: str,
        personal_data: bool = False,
        correlation_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self._operator(actor)
        request_hash = hash_fields(
            "create_evidence_request",
            (
                ("evidence_id", evidence_id),
                ("project_ref", project_ref),
                ("content_hash", content_hash),
            ),
        )
        replay = self.idempotency.replay_or_validate(
            session,
            key=idempotency_key,
            operation="CREATE_EVIDENCE",
            request_hash=request_hash,
        )
        if replay is not None:
            return replay
        if session.scalar(select(EvidenceRecord).where(EvidenceRecord.external_id == evidence_id)):
            raise DomainConflictError("Evidence identifier already exists")
        record = EvidenceRecord(
            external_id=evidence_id,
            project_ref=project_ref,
            evidence_type=evidence_type,
            storage_uri=storage_uri,
            content_hash=content_hash,
            mime_type=mime_type,
            visibility=visibility,
            personal_data=personal_data,
            workflow_status=EvidenceWorkflowStatus.UPLOADED,
            analysis_status="NOT_STARTED",
            integrity_status="NOT_CHECKED",
            blockchain_status=BlockchainStatus.NOT_STARTED,
            metadata_json={},
        )
        session.add(record)
        self.audit.record(
            session,
            actor=actor,
            action="EVIDENCE_UPLOADED",
            entity_type="EVIDENCE",
            entity_id=evidence_id,
            correlation_id=correlation_id,
            metadata={"contentHash": content_hash, "mimeType": mime_type},
        )
        response = {
            "id": evidence_id,
            "workflowStatus": EvidenceWorkflowStatus.UPLOADED,
            "analysisStatus": "NOT_STARTED",
            "integrityStatus": "NOT_CHECKED",
            "blockchainStatus": BlockchainStatus.NOT_STARTED,
            "contentHash": content_hash,
        }
        self.idempotency.remember(
            session,
            key=idempotency_key,
            operation="CREATE_EVIDENCE",
            request_hash=request_hash,
            response_status=201,
            response_body=response,
        )
        return response

    def request_registration(
        self,
        session: Session,
        *,
        actor: ApplicationActor,
        evidence_id: str,
        correlation_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self._operator(actor)
        request_hash = hash_fields("register_evidence_request", (("evidence_id", evidence_id),))
        replay = self.idempotency.replay_or_validate(
            session,
            key=idempotency_key,
            operation="REGISTER_EVIDENCE",
            request_hash=request_hash,
        )
        if replay is not None:
            return replay
        evidence = session.scalar(
            select(EvidenceRecord).where(EvidenceRecord.external_id == evidence_id)
        )
        if evidence is None:
            raise LookupError("Evidence not found")
        if evidence.workflow_status not in {
            EvidenceWorkflowStatus.REVIEWED,
            EvidenceWorkflowStatus.REGISTRATION_FAILED,
        }:
            raise DomainConflictError("Evidence must be REVIEWED or REGISTRATION_FAILED before registration")
        # Registration commits the evidence against a program onchain. Without one there
        # is nothing to commit it to, and the previous default silently bound it to the
        # showcase program. Refuse here so the operator gets an actionable error rather
        # than a worker failure.
        program_id = (evidence.metadata_json or {}).get("programId")
        if not program_id:
            raise DomainConflictError(
                "Evidence has no program to register against; re-upload it under a project "
                "that belongs to a program"
            )
        # registerEvidence reverts with UnknownProgram against a registry that has never
        # heard of the program. Before programs could be created through the API every
        # program was on chain by the time anything referenced it; now one can exist in
        # PostgreSQL while its receipt is still pending, and the operator should be told
        # that here rather than have the worker fail the registration later.
        # Personal data must be lawful to hold before its commitment becomes permanent.
        # After registration the hash cannot be withdrawn, so there is no later point at
        # which "we had no basis for this" can be acted on cheaply.
        if evidence.personal_data:
            protection = session.scalar(
                select(DataProtectionRecord).where(
                    DataProtectionRecord.evidence_ref == evidence_id
                )
            )
            if protection is None:
                raise DomainConflictError(
                    "This evidence is declared to contain personal data and has no data "
                    "protection record; one naming a lawful basis and a controller is "
                    "required before its commitment is made permanent"
                )
            if protection.withdrawn_at is not None:
                raise DomainConflictError(
                    "The basis for holding this evidence has been withdrawn or objected to"
                )
        program = session.scalar(select(ProgramRecord).where(ProgramRecord.slug == program_id))
        if program is None:
            raise DomainConflictError(f"Program {program_id!r} does not exist")
        if program.chain_status != "CONFIRMED":
            raise DomainConflictError(
                f"Program {program_id!r} is {program.chain_status} on chain; evidence cannot "
                "be registered until its program is confirmed"
            )

        operation_id = uuid4()
        operation = BlockchainOperationRecord(
            id=operation_id,
            entity_id=evidence_id,
            operation_type="REGISTER_EVIDENCE",
            status=BlockchainStatus.CREATED,
            expected_event="EvidenceRegistered",
            chain_id=self.chain_id,
            confirmations=0,
            correlation_id=correlation_id,
        )
        session.add(operation)
        session.add(
            OutboxRecord(
                topic="blockchain.register_evidence",
                payload={
                    "operationId": str(operation_id),
                    "evidenceId": evidence_id,
                    "contentHash": evidence.content_hash,
                    "programId": evidence.metadata_json.get("programId"),
                },
                correlation_id=correlation_id,
            )
        )
        evidence.workflow_status = EvidenceWorkflowStatus.REGISTRATION_PENDING
        evidence.blockchain_status = BlockchainStatus.CREATED
        self.audit.record(
            session,
            actor=actor,
            action="EVIDENCE_REGISTRATION_REQUESTED",
            entity_type="EVIDENCE",
            entity_id=evidence_id,
            correlation_id=correlation_id,
            metadata={"operationId": str(operation_id)},
        )
        response = {
            "evidenceId": evidence_id,
            "workflowStatus": EvidenceWorkflowStatus.REGISTRATION_PENDING,
            "blockchainStatus": BlockchainStatus.CREATED,
            "operationId": str(operation_id),
        }
        self.idempotency.remember(
            session,
            key=idempotency_key,
            operation="REGISTER_EVIDENCE",
            request_hash=request_hash,
            response_status=202,
            response_body=response,
        )
        return response


def mark_evidence_reviewed(session: Session, evidence_id: str) -> None:
    evidence = session.scalar(
        select(EvidenceRecord).where(EvidenceRecord.external_id == evidence_id)
    )
    if evidence is None:
        raise LookupError("Evidence not found")
    evidence.workflow_status = EvidenceWorkflowStatus.REVIEWED
    evidence.analysis_status = "COMPLETED"


class VerificationApplicationService:
    def __init__(self, chain_id: int, registry_address: str) -> None:
        self.chain_id = chain_id
        self.registry_address = registry_address
        self.audit = AuditService()
        self.idempotency = IdempotencyService()

    def create_verifier_intent(
        self,
        session: Session,
        *,
        actor: ApplicationActor,
        claim_id: str,
        correlation_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if actor.role != Role.VERIFIER or not actor.wallet:
            raise AuthorizationError("Independent verification requires a verifier wallet")
        claim = session.scalar(select(ClaimRecord).where(ClaimRecord.external_id == claim_id))
        if claim is None:
            raise LookupError("Claim not found")
        if actor.id == operating_organization(session, claim):
            raise AuthorizationError("The project operator cannot independently verify its claim")
        if claim.status != "VERIFICATION_PENDING":
            raise DomainConflictError("Claim is not awaiting independent verification")
        request_hash = hash_fields(
            "verifier_intent_request",
            (
                ("claim_id", claim_id),
                ("bundle_hash", claim.verification_bundle_hash or ""),
                ("issuer_wallet", actor.wallet.lower()),
            ),
        )
        replay = self.idempotency.replay_or_validate(
            session,
            key=idempotency_key,
            operation="CREATE_VERIFIER_ATTESTATION_INTENT",
            request_hash=request_hash,
        )
        if replay is not None:
            return replay
        attestation_id = f"att-verifier-{uuid4()}"
        operation_id = uuid4()
        session.add(
            AttestationRecord(
                external_id=attestation_id,
                attestation_type="INDEPENDENT_VERIFIER",
                subject_type="CLAIM",
                subject_id=claim_id,
                issuer_id=actor.id,
                issuer_wallet=actor.wallet,
                statement_hash=claim.payload_hash,
                verification_bundle_hash=claim.verification_bundle_hash or "",
                status="AWAITING_SIGNATURE",
            )
        )
        session.add(
            BlockchainOperationRecord(
                id=operation_id,
                entity_id=attestation_id,
                operation_type="CREATE_VERIFIER_ATTESTATION",
                status=BlockchainStatus.AWAITING_SIGNATURE,
                expected_event="AttestationCreated",
                chain_id=self.chain_id,
                confirmations=0,
                correlation_id=correlation_id,
            )
        )
        self.audit.record(
            session,
            actor=actor,
            action="VERIFICATION_SIGNATURE_REQUESTED",
            entity_type="CLAIM",
            entity_id=claim_id,
            correlation_id=correlation_id,
            metadata={"attestationId": attestation_id, "operationId": str(operation_id)},
        )
        response = {
            "operationId": str(operation_id),
            "attestationId": attestation_id,
            "claimId": claim_id,
            "status": BlockchainStatus.AWAITING_SIGNATURE,
            "chainId": self.chain_id,
            "contractAddress": self.registry_address,
            "functionName": "createAttestation",
            "arguments": {
                "attestationId": "0x" + entity_id_bytes(attestation_id).hex(),
                "subjectId": "0x" + entity_id_bytes(claim_id).hex(),
                "attestationType": 1,
                "statementHash": "0x" + digest_bytes(claim.payload_hash).hex(),
                "verificationBundleHash": "0x"
                + digest_bytes(claim.verification_bundle_hash or "").hex(),
            },
        }
        self.idempotency.remember(
            session,
            key=idempotency_key,
            operation="CREATE_VERIFIER_ATTESTATION_INTENT",
            request_hash=request_hash,
            response_status=201,
            response_body=response,
        )
        return response

    def reject_claim(
        self,
        session: Session,
        *,
        actor: ApplicationActor,
        claim_id: str,
        reason: str,
        correlation_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if actor.role != Role.VERIFIER:
            raise AuthorizationError("Only an independent verifier may reject a claim")
        claim = session.scalar(select(ClaimRecord).where(ClaimRecord.external_id == claim_id))
        if claim is None:
            raise LookupError("Claim not found")
        if actor.id == operating_organization(session, claim):
            raise AuthorizationError("The project operator cannot independently review its claim")
        request_hash = hash_fields(
            "reject_verification_request",
            (("claim_id", claim_id), ("reason", reason.strip()), ("verifier", actor.id)),
        )
        replay = self.idempotency.replay_or_validate(
            session,
            key=idempotency_key,
            operation="REJECT_VERIFICATION_REQUEST",
            request_hash=request_hash,
        )
        if replay is not None:
            return replay
        if claim.status != "VERIFICATION_PENDING":
            raise DomainConflictError("Claim is not awaiting independent verification")
        claim.status = "REJECTED"
        enqueue_claim_status_change(
            session, claim_id=claim_id, status=claim.status, correlation_id=correlation_id
        )
        self.audit.record(
            session,
            actor=actor,
            action="VERIFICATION_REJECTED",
            entity_type="CLAIM",
            entity_id=claim_id,
            correlation_id=correlation_id,
            metadata={"reason": reason.strip()},
        )
        response = {"claimId": claim_id, "status": "REJECTED", "reason": reason.strip()}
        self.idempotency.remember(
            session,
            key=idempotency_key,
            operation="REJECT_VERIFICATION_REQUEST",
            request_hash=request_hash,
            response_status=200,
            response_body=response,
        )
        return response

    def record_wallet_submission(
        self,
        session: Session,
        *,
        actor: ApplicationActor,
        operation_id: UUID,
        transaction_hash: str,
        correlation_id: str,
    ) -> dict[str, Any]:
        if actor.role != Role.VERIFIER or not actor.wallet:
            raise AuthorizationError("Verifier wallet identity is required")
        if len(transaction_hash) != 66 or not transaction_hash.startswith("0x"):
            raise ValueError("Ethereum transaction hash must be 32-byte hex")
        operation = session.get(BlockchainOperationRecord, operation_id)
        if operation is None or operation.operation_type != "CREATE_VERIFIER_ATTESTATION":
            raise LookupError("Verifier blockchain operation not found")
        attestation = session.scalar(
            select(AttestationRecord).where(AttestationRecord.external_id == operation.entity_id)
        )
        if attestation is None or attestation.issuer_wallet.lower() != actor.wallet.lower():
            raise AuthorizationError("Wallet does not own this attestation intent")
        if operation.transaction_hash:
            if operation.transaction_hash.lower() != transaction_hash.lower():
                raise DomainConflictError("Operation already references another transaction")
            return {
                "operationId": str(operation.id),
                "status": operation.status,
                "transactionHash": operation.transaction_hash,
            }
        operation.transaction_hash = transaction_hash.lower()
        operation.status = BlockchainStatus.SUBMITTED
        attestation.transaction_hash = transaction_hash.lower()
        attestation.status = "SUBMITTED"
        self.audit.record(
            session,
            actor=actor,
            action="BLOCKCHAIN_TX_SUBMITTED",
            entity_type="ATTESTATION",
            entity_id=attestation.external_id,
            correlation_id=correlation_id,
            metadata={"transactionHash": transaction_hash.lower()},
        )
        return {
            "operationId": str(operation.id),
            "status": BlockchainStatus.SUBMITTED,
            "transactionHash": transaction_hash.lower(),
        }


#: A program is not a registry entity until its receipt is observed, and the chain is what
#: `registerEvidence` consults. Evidence filed before then would fail in the worker.
PROGRAM_CHAIN_STATUSES = ("NOT_STARTED", "PENDING", "CONFIRMED", "FAILED")


class TenantApplicationService:
    """Creating the things an organisation operates, and the registry entities behind them.

    Programs and claims are registry entities: the contract reverts with UnknownProgram or
    UnknownEntity if they are absent, so a row in PostgreSQL is not enough to make either
    usable. Both are created through the outbox rather than a synchronous transaction, for
    the reason ADR-002 gives -- the domain write and the intent to touch the chain commit
    together or not at all.

    Projects are not registry entities. Nothing in `ImpactRegistry` takes a project, so
    creating one is a database write and says so rather than queueing a no-op.
    """

    def __init__(self, chain_id: int) -> None:
        self.chain_id = chain_id
        self.idempotency = IdempotencyService()
        self.audit = AuditService()

    @staticmethod
    def _operator(actor: ApplicationActor) -> None:
        if actor.role not in {Role.OPERATOR, Role.ADMIN}:
            raise AuthorizationError("Only an operator or administrator may create programs")

    @staticmethod
    def _owned(program: ProgramRecord | None, actor: ApplicationActor) -> ProgramRecord:
        if program is None:
            raise LookupError("Program not found")
        if actor.role != Role.ADMIN and program.operator_org_ref != actor.id:
            raise AuthorizationError("Program belongs to another operating organisation")
        return program

    def create_program(
        self,
        session: Session,
        *,
        actor: ApplicationActor,
        program_id: str,
        name: str,
        region: str,
        verification_threshold: int,
        correlation_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self._operator(actor)
        request_hash = hash_fields(
            "create_program_request",
            (("program_id", program_id), ("name", name), ("region", region)),
        )
        replay = self.idempotency.replay_or_validate(
            session,
            key=idempotency_key,
            operation="CREATE_PROGRAM",
            request_hash=request_hash,
        )
        if replay is not None:
            return replay
        if session.scalar(select(ProgramRecord).where(ProgramRecord.slug == program_id)):
            raise DomainConflictError(f"A program with the identifier {program_id!r} already exists")

        program = ProgramRecord(
            slug=program_id,
            name=name,
            operator_name=actor.id,
            # From the session, never the request body: an operator who could name the
            # owning organisation could create a program inside someone else's tenancy.
            operator_org_ref=actor.id,
            region=region,
            status="ACTIVE",
            chain_status="PENDING",
            verification_threshold=verification_threshold,
        )
        session.add(program)
        operation_id = uuid4()
        session.add(
            BlockchainOperationRecord(
                id=operation_id,
                entity_id=program_id,
                operation_type="CREATE_PROGRAM_ENTITY",
                status=BlockchainStatus.CREATED,
                expected_event="ProgramCreated",
                chain_id=self.chain_id,
                confirmations=0,
                correlation_id=correlation_id,
            )
        )
        session.add(
            OutboxRecord(
                topic="blockchain.create_program",
                payload={
                    "operationId": str(operation_id),
                    "programId": program_id,
                    "commitment": program_hash(program_id),
                },
                correlation_id=correlation_id,
            )
        )
        response = {
            "id": program_id,
            "name": name,
            "region": region,
            "operatorOrgRef": actor.id,
            "chainStatus": "PENDING",
            "operationId": str(operation_id),
        }
        self.audit.record(
            session,
            actor=actor,
            action="PROGRAM_CREATED",
            entity_type="PROGRAM",
            entity_id=program_id,
            metadata={"operationId": str(operation_id)},
            correlation_id=correlation_id,
        )
        self.idempotency.remember(
            session,
            key=idempotency_key,
            operation="CREATE_PROGRAM",
            request_hash=request_hash,
            response_status=201,
            response_body=response,
        )
        return response

    def create_project(
        self,
        session: Session,
        *,
        actor: ApplicationActor,
        program_id: str,
        project_id: str,
        name: str,
        correlation_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self._operator(actor)
        request_hash = hash_fields(
            "create_project_request",
            (("program_id", program_id), ("project_id", project_id), ("name", name)),
        )
        replay = self.idempotency.replay_or_validate(
            session,
            key=idempotency_key,
            operation="CREATE_PROJECT",
            request_hash=request_hash,
        )
        if replay is not None:
            return replay
        program = self._owned(
            session.scalar(select(ProgramRecord).where(ProgramRecord.slug == program_id)), actor
        )
        if session.scalar(
            select(DomainEntityRecord).where(DomainEntityRecord.external_id == project_id)
        ):
            raise DomainConflictError(f"An entity with the identifier {project_id!r} already exists")

        session.add(
            DomainEntityRecord(
                external_id=project_id,
                entity_type="PROJECT",
                program_id=program.id,
                data={"name": name},
            )
        )
        response = {"id": project_id, "name": name, "programId": program_id}
        self.audit.record(
            session,
            actor=actor,
            action="PROJECT_CREATED",
            entity_type="PROJECT",
            entity_id=project_id,
            metadata={"programId": program_id},
            correlation_id=correlation_id,
        )
        self.idempotency.remember(
            session,
            key=idempotency_key,
            operation="CREATE_PROJECT",
            request_hash=request_hash,
            response_status=201,
            response_body=response,
        )
        return response

    def retry_registry_entity(
        self,
        session: Session,
        *,
        actor: ApplicationActor,
        program_id: str,
        correlation_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Queue the registry entity again for a program whose submission failed.

        A failed submission is not a failed program. The chain call fails for transient
        reasons -- a timeout, a nonce collision, a node restart -- and without this the
        program is a tombstone: claims refuse it, evidence refuses it, and the identifier
        is taken so it cannot be created again. Evidence has had this from the start, in
        `request_registration` accepting REGISTRATION_FAILED.
        """
        self._operator(actor)
        request_hash = hash_fields("retry_program_entity_request", (("program_id", program_id),))
        replay = self.idempotency.replay_or_validate(
            session,
            key=idempotency_key,
            operation="RETRY_PROGRAM_ENTITY",
            request_hash=request_hash,
        )
        if replay is not None:
            return replay
        program = self._owned(
            session.scalar(select(ProgramRecord).where(ProgramRecord.slug == program_id)), actor
        )
        if program.chain_status != "FAILED":
            raise DomainConflictError(
                f"Program {program_id!r} is {program.chain_status} on chain; only a failed "
                "registry entity can be retried"
            )

        operation_id = uuid4()
        program.chain_status = "PENDING"
        session.add(
            BlockchainOperationRecord(
                id=operation_id,
                entity_id=program_id,
                operation_type="CREATE_PROGRAM_ENTITY",
                status=BlockchainStatus.CREATED,
                expected_event="ProgramCreated",
                chain_id=self.chain_id,
                confirmations=0,
                correlation_id=correlation_id,
            )
        )
        session.add(
            OutboxRecord(
                topic="blockchain.create_program",
                payload={
                    "operationId": str(operation_id),
                    "programId": program_id,
                    "commitment": program_hash(program_id),
                },
                correlation_id=correlation_id,
            )
        )
        response = {"id": program_id, "chainStatus": "PENDING", "operationId": str(operation_id)}
        self.audit.record(
            session,
            actor=actor,
            action="PROGRAM_ENTITY_RETRIED",
            entity_type="PROGRAM",
            entity_id=program_id,
            correlation_id=correlation_id,
            metadata={"operationId": str(operation_id)},
        )
        self.idempotency.remember(
            session,
            key=idempotency_key,
            operation="RETRY_PROGRAM_ENTITY",
            request_hash=request_hash,
            response_status=202,
            response_body=response,
        )
        return response

    def create_claim(
        self,
        session: Session,
        *,
        actor: ApplicationActor,
        claim_id: str,
        program_id: str,
        project_id: str,
        statement: str,
        outcome_id: str,
        correlation_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self._operator(actor)
        request_hash = hash_fields(
            "create_claim_request",
            (("claim_id", claim_id), ("program_id", program_id), ("statement", statement)),
        )
        replay = self.idempotency.replay_or_validate(
            session,
            key=idempotency_key,
            operation="CREATE_CLAIM",
            request_hash=request_hash,
        )
        if replay is not None:
            return replay
        program = self._owned(
            session.scalar(select(ProgramRecord).where(ProgramRecord.slug == program_id)), actor
        )
        # createClaim reverts with UnknownProgram against a registry that has never heard
        # of the program, so a claim queued before its program is confirmed would be
        # submitted only to fail.
        if program.chain_status != "CONFIRMED":
            raise DomainConflictError(
                f"Program {program_id!r} is {program.chain_status} on chain; "
                "a claim cannot be created until its program is confirmed"
            )
        if session.scalar(select(ClaimRecord).where(ClaimRecord.external_id == claim_id)):
            raise DomainConflictError(f"A claim with the identifier {claim_id!r} already exists")

        payload_hash = claim_hash(claim_id, statement, outcome_id)
        session.add(
            ClaimRecord(
                external_id=claim_id,
                program_ref=program_id,
                project_ref=project_id,
                statement=statement,
                payload_hash=payload_hash,
                status="EVIDENCE_PENDING",
                verification_policy_version="1.0",
            )
        )
        operation_id = uuid4()
        session.add(
            BlockchainOperationRecord(
                id=operation_id,
                entity_id=claim_id,
                operation_type="CREATE_CLAIM_ENTITY",
                status=BlockchainStatus.CREATED,
                expected_event="ClaimCreated",
                chain_id=self.chain_id,
                confirmations=0,
                correlation_id=correlation_id,
            )
        )
        session.add(
            OutboxRecord(
                topic="blockchain.create_claim",
                payload={
                    "operationId": str(operation_id),
                    "claimId": claim_id,
                    "programId": program_id,
                    "commitment": payload_hash,
                },
                correlation_id=correlation_id,
            )
        )
        response = {
            "id": claim_id,
            "programId": program_id,
            "projectId": project_id,
            "statement": statement,
            "status": "EVIDENCE_PENDING",
            "payloadHash": payload_hash,
            "operationId": str(operation_id),
        }
        self.audit.record(
            session,
            actor=actor,
            action="CLAIM_CREATED",
            entity_type="CLAIM",
            entity_id=claim_id,
            metadata={"programId": program_id, "operationId": str(operation_id)},
            correlation_id=correlation_id,
        )
        self.idempotency.remember(
            session,
            key=idempotency_key,
            operation="CREATE_CLAIM",
            request_hash=request_hash,
            response_status=201,
            response_body=response,
        )
        return response


class OnboardingApplicationService:
    """Bringing an organisation onto the platform, and its first person with it.

    Organisations and users existed only in the seed, so a second one needed a code change
    and a database session. Admin-invited rather than self-service: there is no public
    signup, and who may act as an operator or a verifier is not a thing to leave open.

    An operator organisation needs nothing on chain. Every registry write is submitted by
    the backend sender, which already holds OPERATOR_ROLE -- the operator signs nothing. A
    verifier is different: `createAttestation` checks the role of `msg.sender`, and the
    verifier's own wallet is what signs, so that wallet needs VERIFIER_ROLE on the registry
    before it can attest to anything.
    """

    def __init__(self, chain_id: int) -> None:
        self.chain_id = chain_id
        self.idempotency = IdempotencyService()
        self.audit = AuditService()

    @staticmethod
    def _administrator(actor: ApplicationActor) -> None:
        if actor.role != Role.ADMIN:
            raise AuthorizationError("Only an administrator may onboard an organisation")

    def create_organization(
        self,
        session: Session,
        *,
        actor: ApplicationActor,
        organization_id: str,
        name: str,
        kind: str,
        user_email: str,
        user_name: str,
        password_hash: str,
        correlation_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self._administrator(actor)
        request_hash = hash_fields(
            "create_organization_request",
            (("organization_id", organization_id), ("kind", kind), ("user_email", user_email)),
        )
        replay = self.idempotency.replay_or_validate(
            session,
            key=idempotency_key,
            operation="CREATE_ORGANIZATION",
            request_hash=request_hash,
        )
        if replay is not None:
            return replay
        if kind not in {Role.OPERATOR, Role.VERIFIER}:
            raise DomainConflictError("An organisation is onboarded as OPERATOR or VERIFIER")
        email = user_email.strip().lower()
        if session.scalar(
            select(OrganizationRecord).where(OrganizationRecord.external_id == organization_id)
        ):
            raise DomainConflictError(f"Organisation {organization_id!r} already exists")
        if session.scalar(select(UserRecord).where(UserRecord.email == email)):
            raise DomainConflictError(f"A user with the email {email!r} already exists")

        organization = OrganizationRecord(external_id=organization_id, name=name, kind=kind)
        session.add(organization)
        session.flush()
        session.add(
            UserRecord(
                email=email,
                display_name=user_name,
                password_hash=password_hash,
                organization_id=organization.id,
                # The organisation's kind decides it. A client that could name the role
                # could invite itself an administrator.
                role=kind,
                disabled=False,
            )
        )
        response = {
            "id": organization_id,
            "name": name,
            "kind": kind,
            "firstUser": {"email": email, "role": kind},
            # A verifier cannot attest until an administrator grants its proven wallet
            # VERIFIER_ROLE, which cannot happen until that wallet has been proven.
            "chainRoleRequired": kind == Role.VERIFIER,
        }
        self.audit.record(
            session,
            actor=actor,
            action="ORGANIZATION_CREATED",
            entity_type="ORGANIZATION",
            entity_id=organization_id,
            correlation_id=correlation_id,
            metadata={"kind": kind, "firstUser": email},
        )
        self.idempotency.remember(
            session,
            key=idempotency_key,
            operation="CREATE_ORGANIZATION",
            request_hash=request_hash,
            response_status=201,
            response_body=response,
        )
        return response

    def grant_verifier_role(
        self,
        session: Session,
        *,
        actor: ApplicationActor,
        user_email: str,
        correlation_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Queue the registry grant that lets a verifier's wallet attest.

        The address is read from the user record, where it arrives only after the verifier
        has signed a server-issued nonce. Taking it from the request instead would let an
        administrator grant the role to an address nobody has proven control of.
        """
        self._administrator(actor)
        email = user_email.strip().lower()
        request_hash = hash_fields("grant_verifier_role_request", (("user_email", email),))
        replay = self.idempotency.replay_or_validate(
            session,
            key=idempotency_key,
            operation="GRANT_VERIFIER_ROLE",
            request_hash=request_hash,
        )
        if replay is not None:
            return replay
        user = session.scalar(select(UserRecord).where(UserRecord.email == email))
        if user is None:
            raise LookupError("User not found")
        if user.role != Role.VERIFIER:
            raise DomainConflictError("Only a verifier's wallet may be granted VERIFIER_ROLE")
        if not user.wallet_address:
            raise DomainConflictError(
                "This verifier has not proven a wallet yet, and an unproven address must "
                "not be granted a role"
            )

        operation_id = uuid4()
        session.add(
            BlockchainOperationRecord(
                id=operation_id,
                entity_id=user.wallet_address,
                operation_type="GRANT_VERIFIER_ROLE",
                status=BlockchainStatus.CREATED,
                expected_event="RoleGranted",
                chain_id=self.chain_id,
                confirmations=0,
                correlation_id=correlation_id,
            )
        )
        session.add(
            OutboxRecord(
                topic="blockchain.grant_verifier_role",
                payload={"operationId": str(operation_id), "address": user.wallet_address},
                correlation_id=correlation_id,
            )
        )
        response = {
            "email": email,
            "wallet": user.wallet_address,
            "operationId": str(operation_id),
            "status": BlockchainStatus.CREATED,
        }
        self.audit.record(
            session,
            actor=actor,
            action="VERIFIER_ROLE_REQUESTED",
            entity_type="USER",
            entity_id=email,
            correlation_id=correlation_id,
            metadata={"wallet": user.wallet_address, "operationId": str(operation_id)},
        )
        self.idempotency.remember(
            session,
            key=idempotency_key,
            operation="GRANT_VERIFIER_ROLE",
            request_hash=request_hash,
            response_status=202,
            response_body=response,
        )
        return response


#: Article 6. Named rather than assumed: an organisation that delivers the aid it
#: photographs cannot obtain freely given consent from the person receiving it, so
#: ordinary programme imagery is more likely to rest on legitimate interest with an
#: unconditional objection route.
LAWFUL_BASES = (
    "CONSENT",
    "CONTRACT",
    "LEGAL_OBLIGATION",
    "VITAL_INTERESTS",
    "PUBLIC_TASK",
    "LEGITIMATE_INTEREST",
)

#: Article 9 permits none of the Article 6 bases on their own. Explicit consent is the
#: only one of these this system will accept for special-category data; anything else
#: needs a substantial-public-interest condition that is not an engineering decision.
SPECIAL_CATEGORY_BASES = ("CONSENT",)


def strip_derivatives(evidence: EvidenceRecord) -> list[str]:
    """Remove everything that came out of the document, keeping the commitment to it.

    Destroying the storage key is not erasure on its own. The extraction is the document
    restated as fields -- for a household register that is the person's name, their
    household and where they live -- and it sits in a column, served over the API to
    anyone who could read the record. The reconciliation is derived from it and the
    provider metadata carries what the model was asked and answered.

    The content hash stays. It is the commitment, it cannot be withdrawn from the ledger
    anyway, and it reveals nothing about the document it commits to.
    """
    cleared: list[str] = []
    if evidence.extraction is not None:
        evidence.extraction = None
        cleared.append("extraction")
    if evidence.reconciliation is not None:
        evidence.reconciliation = None
        cleared.append("reconciliation")
    metadata = dict(evidence.metadata_json or {})
    if metadata.pop("providerMetadata", None) is not None:
        evidence.metadata_json = metadata
        cleared.append("providerMetadata")
    return cleared


class DataProtectionApplicationService:
    """The record that makes holding an evidence object lawful, and erasure when it stops.

    Erasure is not deletion. The commitment is on a ledger that cannot forget, so what is
    destroyed is the key: the object becomes unrecoverable and the registry keeps saying,
    truthfully, that something was once committed. ADR-011.
    """

    def __init__(self, storage: Any) -> None:
        self.storage = storage
        self.audit = AuditService()

    @staticmethod
    def _operator(actor: ApplicationActor) -> None:
        if actor.role not in {Role.OPERATOR, Role.ADMIN}:
            raise AuthorizationError("Only an operator or administrator may record this")

    def declare(
        self,
        session: Session,
        *,
        actor: ApplicationActor,
        evidence_id: str,
        lawful_basis: str,
        special_category: bool,
        controller_org_ref: str,
        joint_controller_org_ref: str | None,
        subject_reference: str,
        purpose: str,
        retain_until: str | None,
        correlation_id: str,
    ) -> dict[str, Any]:
        self._operator(actor)
        if lawful_basis not in LAWFUL_BASES:
            raise DomainConflictError(f"lawful_basis must be one of {', '.join(LAWFUL_BASES)}")
        if special_category and lawful_basis not in SPECIAL_CATEGORY_BASES:
            raise DomainConflictError(
                "Special-category data cannot rest on "
                f"{lawful_basis}; Article 9 permits no Article 6 basis on its own, and the "
                "only condition this system accepts is explicit consent"
            )
        evidence = session.scalar(
            select(EvidenceRecord).where(EvidenceRecord.external_id == evidence_id)
        )
        if evidence is None:
            raise LookupError("Evidence not found")
        existing = session.scalar(
            select(DataProtectionRecord).where(
                DataProtectionRecord.evidence_ref == evidence_id
            )
        )
        if existing is not None:
            raise DomainConflictError("This evidence already has a data protection record")

        session.add(
            DataProtectionRecord(
                evidence_ref=evidence_id,
                lawful_basis=lawful_basis,
                special_category=special_category,
                controller_org_ref=controller_org_ref,
                joint_controller_org_ref=joint_controller_org_ref,
                subject_reference=subject_reference,
                purpose=purpose,
                captured_by=actor.id,
                retain_until=retain_until,
            )
        )
        evidence.personal_data = True
        self.audit.record(
            session,
            actor=actor,
            action="DATA_PROTECTION_DECLARED",
            entity_type="EVIDENCE",
            entity_id=evidence_id,
            correlation_id=correlation_id,
            metadata={
                "lawfulBasis": lawful_basis,
                "specialCategory": special_category,
                "controller": controller_org_ref,
            },
        )
        return {
            "evidenceId": evidence_id,
            "lawfulBasis": lawful_basis,
            "specialCategory": special_category,
            "controller": controller_org_ref,
            "jointController": joint_controller_org_ref,
            "retainUntil": retain_until,
        }

    def subject_record(
        self,
        session: Session,
        *,
        actor: ApplicationActor,
        subject_reference: str,
    ) -> dict[str, Any]:
        """Everything this system holds about one person.

        A subject access request has a deadline and no allowance for a search that misses
        something, so this is one query against the column the subject is recorded in
        rather than a tour of the codebase performed by hand under time pressure.

        It reports what is held, not the contents. Returning the documents themselves
        would make this endpoint a way to read every restricted object in the system by
        guessing a reference, and a subject is entitled to their data through a verified
        channel rather than through whoever asked first.
        """
        self._operator(actor)
        records = list(
            session.scalars(
                select(DataProtectionRecord)
                .where(DataProtectionRecord.subject_reference == subject_reference)
                .order_by(DataProtectionRecord.created_at)
            )
        )
        if actor.role != Role.ADMIN:
            records = [
                record
                for record in records
                if actor.id in {record.controller_org_ref, record.joint_controller_org_ref}
            ]
        evidence_ids = [record.evidence_ref for record in records]
        evidence = {
            item.external_id: item
            for item in session.scalars(
                select(EvidenceRecord).where(EvidenceRecord.external_id.in_(evidence_ids))
            )
        }
        held = []
        for record in records:
            item = evidence.get(record.evidence_ref)
            held.append(
                {
                    "evidenceId": record.evidence_ref,
                    "lawfulBasis": record.lawful_basis,
                    "specialCategory": record.special_category,
                    "controller": record.controller_org_ref,
                    "jointController": record.joint_controller_org_ref,
                    "purpose": record.purpose,
                    "collectedAt": record.created_at.isoformat() if record.created_at else None,
                    "retainUntil": record.retain_until,
                    "objectedAt": record.withdrawn_at.isoformat() if record.withdrawn_at else None,
                    "erasedAt": record.erased_at.isoformat() if record.erased_at else None,
                    # Named so a subject can be told what happens if they object: the
                    # bytes go, the commitment stays and says only that they once existed.
                    "onchainCommitment": item.content_hash if item else None,
                    "type": item.evidence_type if item else None,
                }
            )
        return {
            "subjectReference": subject_reference,
            "held": held,
            "erasable": [entry["evidenceId"] for entry in held if entry["erasedAt"] is None],
            "note": "Objecting erases the stored object. The onchain commitment cannot be "
            "withdrawn and continues to record only that those bytes were once committed.",
        }

    def erase(
        self,
        session: Session,
        *,
        actor: ApplicationActor,
        evidence_id: str,
        reason: str,
        correlation_id: str,
    ) -> dict[str, Any]:
        """Destroy the key, and re-decide whatever rested on the object.

        A claim keeps whatever badge it had unless something re-evaluates it, so erasing
        the evidence beneath a VERIFIED claim without restating it would leave a donor
        reading a verdict whose support no longer exists.
        """
        self._operator(actor)
        record = session.scalar(
            select(DataProtectionRecord).where(
                DataProtectionRecord.evidence_ref == evidence_id
            )
        )
        if record is None:
            raise LookupError("This evidence has no data protection record to act on")
        evidence = session.scalar(
            select(EvidenceRecord).where(EvidenceRecord.external_id == evidence_id)
        )
        if evidence is None:
            raise LookupError("Evidence not found")

        now = datetime.now(UTC)
        destroyed = self.storage.destroy_key(evidence.storage_uri)
        cleared = strip_derivatives(evidence)
        record.withdrawn_at = record.withdrawn_at or now
        record.erased_at = now
        evidence.integrity_status = "UNRECOVERABLE"
        restated = restate_claims_for(session, evidence_id, correlation_id)
        self.audit.record(
            session,
            actor=actor,
            action="EVIDENCE_ERASED",
            entity_type="EVIDENCE",
            entity_id=evidence_id,
            correlation_id=correlation_id,
            # Never the subject or the contents: an erasure record that quotes what was
            # erased is not an erasure.
            metadata={
                "reason": reason,
                "keyDestroyed": destroyed,
                "derivativesCleared": cleared,
                "claimsRestated": restated,
            },
        )
        return {
            "evidenceId": evidence_id,
            "erasedAt": now.isoformat(),
            "keyDestroyed": destroyed,
            "derivativesCleared": cleared,
            "claimsRestated": restated,
            # The commitment is not withdrawn, and saying so is the honest part.
            "commitment": "The onchain commitment remains; it records that these bytes were "
            "once committed, which is still true.",
        }


class FunderNameService:
    """Whether a funder is named in public, decided by the funder.

    An operator knows the name already and has no endpoint that publishes it. They can
    ask for a consent link and pass it to the person, and the person decides. The link is
    the whole authority, so it is stored hashed and the plaintext is returned once.

    Withdrawable, unlike the publication of a claim. A claim published and then withdrawn
    would make the record editable; a person changing their mind about being named is the
    thing this exists to respect.
    """

    def __init__(self) -> None:
        self.audit = AuditService()

    def issue_consent_link(
        self,
        session: Session,
        *,
        actor: ApplicationActor,
        funding_id: str,
        correlation_id: str,
    ) -> dict[str, Any]:
        if actor.role not in {Role.OPERATOR, Role.ADMIN}:
            raise AuthorizationError("Only an operator or administrator may request this")
        funding = session.scalar(
            select(FundingRecord).where(FundingRecord.external_id == funding_id)
        )
        if funding is None:
            raise LookupError("Funding not found")
        token = secrets.token_urlsafe(48)
        funding.name_consent_token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        self.audit.record(
            session,
            actor=actor,
            action="FUNDER_NAME_CONSENT_REQUESTED",
            entity_type="FUNDING",
            entity_id=funding_id,
            correlation_id=correlation_id,
            metadata={},
        )
        return {
            "fundingId": funding_id,
            "token": token,
            "note": "Send this to the funder. Requesting it does not publish anything, and "
            "there is no way for anyone else to decide this on their behalf.",
        }

    @staticmethod
    def _by_token(session: Session, token: str) -> FundingRecord:
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        funding = session.scalar(
            select(FundingRecord).where(FundingRecord.name_consent_token_hash == digest)
        )
        if funding is None:
            raise LookupError("That link is not valid")
        return funding

    def set_publication(
        self,
        session: Session,
        *,
        token: str,
        publish: bool,
        correlation_id: str,
    ) -> dict[str, Any]:
        funding = self._by_token(session, token)
        funding.publish_funder_name = publish
        session.add(
            AuditLogRecord(
                actor_id="funder",
                action="FUNDER_NAME_PUBLISHED" if publish else "FUNDER_NAME_WITHDRAWN",
                entity_type="FUNDING",
                entity_id=funding.external_id,
                # Never the name: an entry recording a choice about a name should not be
                # a second place the name is written down.
                metadata_json={},
                correlation_id=correlation_id,
            )
        )
        return {
            "fundingId": funding.external_id,
            "published": publish,
            "shownAs": public_funder_name(funding),
        }


class ClaimPublicationService:
    """Deciding that a claim is a public proof, and not being able to take it back.

    An organisation opts in, because no organisation adopts a platform that publishes
    its failures by default. After that the page shows the current status -- including
    CHALLENGED or REVOKED -- and there is no route that unpublishes it. A record that can
    be withdrawn once the verdict turns inconvenient is not a record of anything, and the
    withdrawal would be the one edit this whole system exists to make impossible.
    """

    def __init__(self) -> None:
        self.audit = AuditService()

    def publish(
        self,
        session: Session,
        *,
        actor: ApplicationActor,
        claim_id: str,
        correlation_id: str,
    ) -> dict[str, Any]:
        if actor.role not in {Role.OPERATOR, Role.ADMIN}:
            raise AuthorizationError("Only an operator or administrator may publish a claim")
        claim = session.scalar(select(ClaimRecord).where(ClaimRecord.external_id == claim_id))
        if claim is None:
            raise LookupError("Claim not found")
        program = session.scalar(
            select(ProgramRecord).where(ProgramRecord.slug == claim.program_ref)
        )
        if program is None:
            raise LookupError("The claim's program does not exist")
        if actor.role != Role.ADMIN and program.operator_org_ref != actor.id:
            raise AuthorizationError("Claim belongs to another operating organisation")
        if claim.published_at is not None:
            return {
                "claimId": claim_id,
                "publishedAt": as_utc_iso(claim.published_at),
                "alreadyPublished": True,
            }

        claim.published_at = datetime.now(UTC)
        self.audit.record(
            session,
            actor=actor,
            action="CLAIM_PUBLISHED",
            entity_type="CLAIM",
            entity_id=claim_id,
            correlation_id=correlation_id,
            metadata={"status": claim.status},
        )
        return {
            "claimId": claim_id,
            "publishedAt": as_utc_iso(claim.published_at),
            "alreadyPublished": False,
            "note": "This page now shows whatever the claim's status becomes, including if "
            "it is later challenged or revoked. There is no way to unpublish it.",
        }
