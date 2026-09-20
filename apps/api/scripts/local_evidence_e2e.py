"""PostgreSQL → outbox → Anvil → receipt/event → PostgreSQL smoke test."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select

from impactgraph.blockchain import EvmBlockchainService, entity_id_bytes
from impactgraph.config import Settings
from impactgraph.database import create_session_factory
from impactgraph.domain import Role
from impactgraph.hashing import hash_fields, sha256_bytes
from impactgraph.persistence import EvidenceRecord
from impactgraph.services import (
    ApplicationActor,
    EvidenceApplicationService,
    mark_evidence_reviewed,
)
from impactgraph.worker import BlockchainOutboxWorker


def main() -> None:
    settings = Settings.from_env()
    if settings.chain_id != 31337:
        raise RuntimeError("This smoke test is restricted to local chain 31337")
    if not settings.registry_address or not settings.evm_sender_address:
        raise RuntimeError("IMPACT_REGISTRY_ADDRESS and EVM_SENDER_ADDRESS are required")
    chain = EvmBlockchainService.from_foundry_artifact(
        rpc_url=settings.rpc_url,
        contract_address=settings.registry_address,
        artifact_path=settings.contract_artifact_path,
        sender=settings.evm_sender_address,
    )
    program_id = "program-clean-water-kenya-2026"
    if not chain.contract.functions.entityExists(entity_id_bytes(program_id)).call():
        transaction_hash = chain.create_program(
            program_id, hash_fields("program", (("id", program_id),))
        )
        chain.web3.eth.wait_for_transaction_receipt(transaction_hash)

    run_id = uuid4().hex[:12]
    evidence_id = f"ev-local-e2e-{run_id}"
    factory = create_session_factory(settings.database_url)
    service = EvidenceApplicationService(chain_id=settings.chain_id)
    actor = ApplicationActor("operator-global-water", Role.OPERATOR)
    with factory.begin() as session:
        service.create_uploaded(
            session,
            actor=actor,
            evidence_id=evidence_id,
            project_ref="project-water-12",
            evidence_type="INVOICE",
            storage_uri=f"file:///demo/{evidence_id}",
            content_hash=sha256_bytes(f"local-e2e:{run_id}".encode()),
            mime_type="text/plain",
            visibility="RESTRICTED",
            correlation_id=f"corr-{run_id}",
            idempotency_key=f"create-{run_id}",
        )
        evidence = session.scalar(
            select(EvidenceRecord).where(EvidenceRecord.external_id == evidence_id)
        )
        assert evidence is not None
        evidence.metadata_json = {"programId": program_id}
        mark_evidence_reviewed(session, evidence_id)
    with factory.begin() as session:
        service.request_registration(
            session,
            actor=actor,
            evidence_id=evidence_id,
            correlation_id=f"corr-{run_id}",
            idempotency_key=f"register-{run_id}",
        )
    result = BlockchainOutboxWorker(
        session_factory=factory,
        blockchain=chain,
        confirmations_required=settings.confirmations_required,
    ).run_once()
    with factory() as session:
        evidence = session.scalar(
            select(EvidenceRecord).where(EvidenceRecord.external_id == evidence_id)
        )
        assert evidence is not None
        if evidence.workflow_status != "REGISTERED_ONCHAIN":
            raise RuntimeError(f"Unexpected final evidence state: {evidence.workflow_status}")
        print(
            {
                "evidenceId": evidence_id,
                "worker": result,
                "workflowStatus": evidence.workflow_status,
                "blockchainStatus": evidence.blockchain_status,
                "blockchainReference": evidence.metadata_json["blockchainReference"],
            }
        )


if __name__ == "__main__":
    main()
