from __future__ import annotations

from dataclasses import dataclass
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
    DomainEntityRecord,
    EvidenceRecord,
    IdempotencyRecord,
    OutboxRecord,
    ProgramRecord,
)


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
