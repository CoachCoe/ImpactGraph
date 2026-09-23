from __future__ import annotations

import argparse
import json
import os
import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from prometheus_client import start_http_server
from sqlalchemy import select
from web3 import Web3

from .auth import (
    DEMO_PASSWORD,
    DEMO_USERS,
    demo_accounts_permitted,
    seed_demo_accounts,
)
from .blockchain import (
    EvmBlockchainService,
    commitment_from_receipt,
    digest_bytes,
    entity_id_bytes,
)
from .config import PUBLIC_CHAIN_IDS, Settings, _decode_encryption_key
from .credentials import seal, unseal
from .database import create_session_factory
from .demo import INVOICE_BYTES, store
from .domain import BlockchainStatus
from .evidence import FileEvidenceStorage
from .hashing import claim_hash, hash_fields, program_hash, sha256_bytes
from .metrics import REGISTRY
from .notifications import ConsoleNotificationTransport, NotificationDispatcher
from .observability import configure_logging, logger
from .persistence import (
    BlockchainOperationRecord,
    EvidenceRecord,
    ProcessedChainEventRecord,
    ProviderCredentialRecord,
)
from .read_model import (
    CLAIM_ID,
    CLAIM_STATEMENT,
    EVIDENCE_ID,
    OUTCOME_ID,
    PROGRAM_ID,
    reset_read_model,
    seed_read_model,
)
from .retention import RetentionResult, RetentionWorker
from .worker import BlockchainOutboxWorker

log = logger("impactgraph.worker")


def seed() -> None:
    settings = Settings.from_env()
    storage = FileEvidenceStorage(
        settings.evidence_storage_path,
        settings.evidence_encryption_key,
        settings.evidence_key_path,
    )
    invoice_uri = storage.uri_for("ev-inv-8291")
    storage.overwrite(invoice_uri, INVOICE_BYTES)
    store.reset()
    database_seeded = False
    refusal: str | None = None
    registration = None
    if settings.persistence_mode == "postgres":
        factory = create_session_factory(settings.database_url)
        refusal = demo_accounts_permitted(settings)
        with factory.begin() as session:
            seed_read_model(session, storage)
            if refusal is None:
                seed_demo_accounts(session)
        database_seeded = True
        registration = register_seeded_evidence(settings)
    print(
        json.dumps(
            {
                "demoRunId": "clean-water-kenya-2026-local-001",
                "claimStatus": store.claim["status"],
                "invoiceHash": sha256_bytes(INVOICE_BYTES),
                "storage": invoice_uri,
                "databaseSeeded": database_seeded,
                "evidenceRegistration": registration or seeded_registration_note(settings),
                "accounts": {
                    "password": DEMO_PASSWORD,
                    "emails": [email for email, *_ in DEMO_USERS],
                }
                if database_seeded and refusal is None
                else None,
                "accountsSkipped": refusal,
            },
            indent=2,
        )
    )


def demo_reset(local_only: bool) -> None:
    settings = Settings.from_env()
    if not local_only or settings.demo_mode == "sepolia":
        raise RuntimeError("Demo reset is restricted to an explicitly selected local environment")
    storage = FileEvidenceStorage(
        settings.evidence_storage_path,
        settings.evidence_encryption_key,
        settings.evidence_key_path,
    )
    if settings.persistence_mode == "postgres":
        factory = create_session_factory(settings.database_url)
        with factory.begin() as session:
            reset_read_model(session, storage)
    store.reset()
    # Undo any tampering so the demo is repeatable from a known state.
    storage.overwrite(storage.uri_for("ev-inv-8291"), INVOICE_BYTES)
    print(
        json.dumps(
            {
                "reset": True,
                "demoRunId": "clean-water-kenya-2026-local-001",
                "claimStatus": "VERIFICATION_PENDING",
                "databaseReset": settings.persistence_mode == "postgres",
            },
            indent=2,
        )
    )


def reconcile_chain() -> None:
    """Compare persisted blockchain operations against what the chain actually reports."""
    settings = Settings.from_env()
    if settings.persistence_mode != "postgres":
        raise RuntimeError("Chain reconciliation requires PERSISTENCE_MODE=postgres")
    if not settings.registry_address:
        raise RuntimeError("IMPACT_REGISTRY_ADDRESS is required for chain reconciliation")
    chain = EvmBlockchainService.from_foundry_artifact(
        rpc_url=settings.rpc_url,
        contract_address=settings.registry_address,
        artifact_path=settings.contract_artifact_path,
        sender=settings.evm_sender_address or None,
    )
    factory = create_session_factory(settings.database_url)
    buckets: dict[str, list[dict[str, Any]]] = {
        "submittedButUnconfirmed": [],
        "confirmedButNotIndexed": [],
        "failed": [],
        "missingExpectedEvent": [],
        "unexpectedEvents": [],
    }
    with factory() as session:
        operations = list(session.scalars(select(BlockchainOperationRecord)))
        indexed = {
            (row.transaction_hash, row.log_index)
            for row in session.scalars(select(ProcessedChainEventRecord))
        }
    for operation in operations:
        entry = {
            "operationId": str(operation.id),
            "entityId": operation.entity_id,
            "status": str(operation.status),
            "transactionHash": operation.transaction_hash,
        }
        if operation.status == BlockchainStatus.FAILED:
            buckets["failed"].append({**entry, "error": operation.error})
            continue
        if not operation.transaction_hash:
            continue
        observation = chain.get_transaction(operation.transaction_hash)
        if observation is None:
            if operation.status == BlockchainStatus.SUBMITTED:
                buckets["submittedButUnconfirmed"].append(
                    {**entry, "reason": "No receipt is visible for the recorded hash"}
                )
            continue
        names = {event.get("event") for event in observation.events}
        if operation.expected_event not in names:
            buckets["missingExpectedEvent"].append(
                {**entry, "expectedEvent": operation.expected_event, "observed": sorted(names)}
            )
        unexpected = sorted(names - {operation.expected_event})
        if unexpected:
            buckets["unexpectedEvents"].append({**entry, "observed": unexpected})
        if (
            operation.status == BlockchainStatus.SUBMITTED
            and observation.confirmations >= settings.confirmations_required
        ):
            buckets["submittedButUnconfirmed"].append(
                {**entry, "reason": "Confirmed onchain but not yet advanced locally"}
            )
        if operation.status == BlockchainStatus.CONFIRMED and any(
            (observation.transaction_hash, event.get("logIndex")) not in indexed
            for event in observation.events
        ):
            buckets["confirmedButNotIndexed"].append(
                {**entry, "reason": "Confirmed locally but an emitted event was never indexed"}
            )
    drift = sum(len(rows) for rows in buckets.values())
    print(
        json.dumps(
            {
                "chainId": settings.chain_id,
                "mode": settings.demo_mode,
                "operationsInspected": len(operations),
                **buckets,
                "result": "No drift found"
                if drift == 0
                else f"{drift} operation(s) need attention",
            },
            indent=2,
        )
    )


def _chain_entity_arguments() -> dict[str, str]:
    """The onchain identifiers and commitments for the showcase entities.

    Single source of truth for both bootstrap transports. Python owns these values because
    they use the canonical framed encoding in `hashing.py`, which Solidity cannot compute.
    """
    return {
        "BOOTSTRAP_PROGRAM_ID": Web3.to_hex(entity_id_bytes(PROGRAM_ID)),
        "BOOTSTRAP_PROGRAM_COMMITMENT": Web3.to_hex(
            digest_bytes(hash_fields("program", (("id", PROGRAM_ID),)))
        ),
        "BOOTSTRAP_CLAIM_ID": Web3.to_hex(entity_id_bytes(CLAIM_ID)),
        "BOOTSTRAP_CLAIM_COMMITMENT": Web3.to_hex(
            digest_bytes(claim_hash(CLAIM_ID, CLAIM_STATEMENT, OUTCOME_ID))
        ),
        "BOOTSTRAP_EVIDENCE_ID": Web3.to_hex(entity_id_bytes(EVIDENCE_ID)),
        "BOOTSTRAP_EVIDENCE_COMMITMENT": Web3.to_hex(
            digest_bytes(sha256_bytes(INVOICE_BYTES))
        ),
    }


def chain_args() -> None:
    """Print the bootstrap arguments as shell exports for the Foundry script.

    Used for networks where accounts are not unlocked, so the transaction has to be signed
    by Forge with DEPLOYER_PRIVATE_KEY rather than sent through the backend adapter. The
    backend deliberately holds no key material.
    """
    for name, value in _chain_entity_arguments().items():
        print(f'export {name}="{value}"')




def deploy_registry(expected_address: str | None = None) -> None:
    """Deploy ImpactRegistry to a local chain from the compiled artifact.

    The self-contained demo stack has no Foundry, but the API image already ships the
    artifact, and it carries the creation bytecode. Deploying from Python keeps the demo
    to one image and one command.

    Local chains only. A public network deployment is an irreversible, funded action and
    goes through scripts/deploy-sepolia.sh, which asks for confirmation.
    """
    settings = Settings.from_env()
    if settings.demo_mode != "local" or settings.chain_id in PUBLIC_CHAIN_IDS:
        raise RuntimeError(
            f"Refusing to deploy to chain {settings.chain_id} in {settings.demo_mode!r} mode. "
            "Public networks go through scripts/deploy-sepolia.sh."
        )
    if not settings.evm_sender_address:
        raise RuntimeError("EVM_SENDER_ADDRESS is required; the local chain must hold the key")

    artifact = json.loads(settings.contract_artifact_path.read_text())
    web3 = Web3(Web3.HTTPProvider(settings.rpc_url))
    if not web3.is_connected():
        raise RuntimeError(f"Cannot reach the chain at {settings.rpc_url}")
    sender = Web3.to_checksum_address(settings.evm_sender_address)

    # Restarting the stack must not redeploy. Without this the sender's nonce has moved
    # on, the deterministic address no longer matches, and the run fails on a chain that
    # was in fact perfectly usable.
    if expected_address:
        existing = web3.eth.get_code(Web3.to_checksum_address(expected_address))
        if existing and existing != b"\x00":
            print(
                json.dumps(
                    {
                        "chainId": settings.chain_id,
                        "registry": Web3.to_checksum_address(expected_address),
                        "action": "already deployed",
                    },
                    indent=2,
                )
            )
            return

    contract = web3.eth.contract(abi=artifact["abi"], bytecode=artifact["bytecode"]["object"])
    transaction_hash = contract.constructor(sender).transact({"from": sender})
    receipt = web3.eth.wait_for_transaction_receipt(transaction_hash)
    address = Web3.to_checksum_address(receipt["contractAddress"])

    registry = web3.eth.contract(address=address, abi=artifact["abi"])
    operator_role = registry.functions.OPERATOR_ROLE().call()
    grant = registry.functions.grantRole(operator_role, sender).transact({"from": sender})
    web3.eth.wait_for_transaction_receipt(grant)

    if expected_address and address.lower() != expected_address.lower():
        # The demo stack pins the address so the API can be configured before deployment.
        # A mismatch means the chain was not fresh, and every service would point at
        # nothing.
        raise RuntimeError(
            f"Deployed to {address} but the stack expects {expected_address}. "
            "The chain is not in its initial state; reset it and try again."
        )
    print(
        json.dumps(
            {
                "chainId": settings.chain_id,
                "registry": address,
                "deployer": sender,
                "transactionHash": Web3.to_hex(transaction_hash),
            },
            indent=2,
        )
    )


def register_seeded_evidence(
    settings: Settings, chain: Any | None = None
) -> dict[str, str] | None:
    """Register the seeded invoice on the configured registry and record the real receipt.

    The seed cannot fabricate a transaction hash: integrity verification reads the
    commitment back from the registry when one is configured, so an invented reference
    would either fail or, worse, be believed. Returns None when there is nothing to
    register against, or no key to sign with -- `seeded_registration_note` explains which.

    `chain` is injectable so the registration path can be exercised without a node.
    """
    if not settings.registry_address or not settings.evm_sender_address:
        return None
    chain = chain or EvmBlockchainService.from_foundry_artifact(
        rpc_url=settings.rpc_url,
        contract_address=settings.registry_address,
        artifact_path=settings.contract_artifact_path,
        sender=settings.evm_sender_address,
    )
    factory = create_session_factory(settings.database_url)
    with factory.begin() as session:
        record = session.scalar(
            select(EvidenceRecord).where(EvidenceRecord.external_id == EVIDENCE_ID)
        )
        if record is None:
            return None
        metadata = dict(record.metadata_json or {})
        if metadata.get("blockchainReference"):
            return metadata["blockchainReference"]
        # The database row is not evidence that the chain is empty. Seeding a fresh
        # database against a surviving chain would otherwise re-register a commitment the
        # registry already holds, and evidence uniqueness makes that revert.
        existing = (
            chain.find_evidence_registration(EVIDENCE_ID)
            if chain.entity_exists(EVIDENCE_ID)
            else None
        )
        if existing is not None:
            observation = existing
            transaction_hash = existing.transaction_hash
        else:
            transaction_hash = chain.register_evidence(
                EVIDENCE_ID, PROGRAM_ID, record.content_hash
            )
            observation = chain.get_transaction(transaction_hash)
        block_number = observation.block_number if observation else 0
        reference = {
            "transactionHash": transaction_hash,
            "blockNumber": block_number,
            # The chain's clock, not ours: this is when the commitment became unalterable.
            "registeredAt": chain.block_time(block_number) if block_number else None,
        }
        metadata["blockchainReference"] = reference
        record.metadata_json = metadata
        record.blockchain_status = "CONFIRMED"
        return reference


def seeded_registration_note(settings: Settings) -> str:
    """Why the seed did not register, and what to do about it."""
    if not settings.registry_address:
        return "no registry configured; the seeded commitment is not onchain"
    return (
        "the backend holds no signing key, which is correct on a public network. "
        "Register with the operator wallet: "
        "eval \"$(python -m impactgraph.cli chain-args)\" && "
        "forge script script/RegisterSeedEvidence.s.sol:RegisterSeedEvidence "
        "--rpc-url $RPC_URL --broadcast, then "
        "python -m impactgraph.cli record-evidence-registration --transaction-hash 0x..."
    )


def record_evidence_registration(transaction_hash: str) -> None:
    """Record an operator-signed evidence registration after verifying it on chain.

    On a public network the backend holds no key, so the operator signs the registration
    themselves through Foundry. This records the result -- but only after reading the
    receipt back and confirming it really registered this evidence with this commitment.
    A transaction hash supplied by a human is exactly the kind of input that must not be
    taken on trust.
    """
    settings = Settings.from_env()
    if not settings.registry_address:
        raise RuntimeError("IMPACT_REGISTRY_ADDRESS is required")
    chain = EvmBlockchainService.from_foundry_artifact(
        rpc_url=settings.rpc_url,
        contract_address=settings.registry_address,
        artifact_path=settings.contract_artifact_path,
    )
    observation = chain.get_transaction(transaction_hash)
    if observation is None:
        raise RuntimeError(f"No receipt is visible for {transaction_hash}")
    if not observation.success:
        raise RuntimeError(f"{transaction_hash} reverted; nothing was registered")

    committed = commitment_from_receipt(observation, EVIDENCE_ID)
    if committed is None:
        raise RuntimeError(
            f"{transaction_hash} carries no EvidenceRegistered event for {EVIDENCE_ID} "
            "emitted by the configured registry"
        )
    expected = sha256_bytes(INVOICE_BYTES)
    if committed != expected:
        raise RuntimeError(
            f"{transaction_hash} committed {committed}, but the seeded evidence hashes "
            f"to {expected}"
        )

    factory = create_session_factory(settings.database_url)
    with factory.begin() as session:
        record = session.scalar(
            select(EvidenceRecord).where(EvidenceRecord.external_id == EVIDENCE_ID)
        )
        if record is None:
            raise RuntimeError("Seeded evidence is missing; run the seed first")
        metadata = dict(record.metadata_json or {})
        metadata["blockchainReference"] = {
            "transactionHash": observation.transaction_hash,
            "blockNumber": observation.block_number,
        }
        record.metadata_json = metadata
        record.blockchain_status = "CONFIRMED"
    print(
        json.dumps(
            {
                "evidenceId": EVIDENCE_ID,
                "transactionHash": observation.transaction_hash,
                "blockNumber": observation.block_number,
                "confirmations": observation.confirmations,
                "commitment": committed,
            },
            indent=2,
        )
    )


def bootstrap_chain() -> None:
    """Create the onchain entities and roles the golden path depends on.

    Deployment alone leaves the registry empty, so registerEvidence reverts with
    UnknownProgram and createAttestation reverts with UnknownEntity. Idempotent.

    This transport uses an unlocked sender and is for local nodes only. Signed networks go
    through `make chain-args` and the Foundry bootstrap script.
    """
    settings = Settings.from_env()
    if settings.demo_mode == "sepolia":
        raise RuntimeError(
            "Refusing to bootstrap Sepolia implicitly; use the explicit deployment procedure "
            "in docs/sepolia-deployment.md"
        )
    if not settings.registry_address or not settings.evm_sender_address:
        raise RuntimeError("IMPACT_REGISTRY_ADDRESS and EVM_SENDER_ADDRESS are required")
    chain = EvmBlockchainService.from_foundry_artifact(
        rpc_url=settings.rpc_url,
        contract_address=settings.registry_address,
        artifact_path=settings.contract_artifact_path,
        sender=settings.evm_sender_address,
    )
    actions: list[dict[str, str]] = []

    def _await(label: str, transaction_hash: str) -> None:
        chain.web3.eth.wait_for_transaction_receipt(transaction_hash)
        actions.append({"action": label, "transactionHash": transaction_hash})

    if chain.entity_exists(PROGRAM_ID):
        actions.append({"action": "program", "transactionHash": "already present"})
    else:
        _await(
            "program",
            chain.create_program(PROGRAM_ID, program_hash(PROGRAM_ID)),
        )
    if chain.entity_exists(CLAIM_ID):
        actions.append({"action": "claim", "transactionHash": "already present"})
    else:
        _await(
            "claim",
            chain.create_claim(
                CLAIM_ID, PROGRAM_ID, claim_hash(CLAIM_ID, CLAIM_STATEMENT, OUTCOME_ID)
            ),
        )

    verifier = settings.verifier_wallet_address
    if not verifier:
        actions.append(
            {"action": "verifier role", "transactionHash": "skipped: VERIFIER_WALLET_ADDRESS unset"}
        )
    elif verifier.lower() == settings.evm_sender_address.lower():
        raise RuntimeError(
            "VERIFIER_WALLET_ADDRESS must differ from EVM_SENDER_ADDRESS; the operator cannot "
            "independently verify its own claim"
        )
    elif chain.has_verifier_role(verifier):
        actions.append({"action": "verifier role", "transactionHash": "already granted"})
    else:
        _await("verifier role", chain.grant_verifier_role(verifier))

    print(
        json.dumps(
            {
                "chainId": settings.chain_id,
                "registry": settings.registry_address,
                "outcomeId": OUTCOME_ID,
                "verifierWallet": verifier or None,
                "actions": actions,
            },
            indent=2,
        )
    )


def worker_once() -> None:
    configure_logging()
    settings = Settings.from_env()
    if not settings.registry_address:
        raise RuntimeError("IMPACT_REGISTRY_ADDRESS is required for the EVM worker")
    blockchain = EvmBlockchainService.from_foundry_artifact(
        rpc_url=settings.rpc_url,
        contract_address=settings.registry_address,
        artifact_path=settings.contract_artifact_path,
        sender=settings.evm_sender_address or None,
    )
    result = BlockchainOutboxWorker(
        session_factory=create_session_factory(settings.database_url),
        blockchain=blockchain,
        confirmations_required=settings.confirmations_required,
    ).run_once()
    print(
        json.dumps(
            {"submitted": result.submitted, "confirmed": result.confirmed, "failed": result.failed},
            indent=2,
        )
    )


def worker_loop(interval_seconds: float) -> None:
    """Run the outbox worker continuously.

    The API schedules background tasks for operations it initiates, but the outbox is the
    durable path: a deployment needs a process that keeps draining it after a restart or a
    failed submission.
    """
    configure_logging()
    settings = Settings.from_env()
    if not settings.registry_address:
        raise RuntimeError("IMPACT_REGISTRY_ADDRESS is required for the EVM worker")
    # Its own server on its own port: the worker is a separate process, so counters it
    # increments are invisible to the API's /metrics and Prometheus scrapes the two
    # independently. Telemetry must never stop the thing it observes, so a port already
    # in use costs the exporter and not the outbox.
    try:
        start_http_server(settings.worker_metrics_port, registry=REGISTRY)
        log.info("worker.metrics_listening", port=settings.worker_metrics_port)
    except OSError as exc:
        log.error(
            "worker.metrics_unavailable",
            port=settings.worker_metrics_port,
            error_type=exc.__class__.__name__,
        )

    worker = BlockchainOutboxWorker(
        session_factory=create_session_factory(settings.database_url),
        blockchain=EvmBlockchainService.from_foundry_artifact(
            rpc_url=settings.rpc_url,
            contract_address=settings.registry_address,
            artifact_path=settings.contract_artifact_path,
            sender=settings.evm_sender_address or None,
        ),
        confirmations_required=settings.confirmations_required,
    )
    log.info("worker.started", interval_seconds=interval_seconds)
    while True:
        try:
            result = worker.run_once()
            if result.submitted or result.confirmed or result.failed:
                log.info(
                    "worker.batch",
                    submitted=result.submitted,
                    confirmed=result.confirmed,
                    failed=result.failed,
                )
        except Exception as exc:  # noqa: BLE001 -- a worker must outlive a transient RPC fault
            # The class, never the text: a web3 fault can carry the RPC URL, and that URL
            # may embed credentials.
            log.error("worker.batch_failed", error_type=exc.__class__.__name__)
        time.sleep(interval_seconds)


def retention_once() -> RetentionResult:
    """Erase everything past its retention schedule, once."""
    configure_logging()
    settings = Settings.from_env()
    worker = RetentionWorker(
        session_factory=create_session_factory(settings.database_url),
        storage=FileEvidenceStorage(
            settings.evidence_storage_path,
            settings.evidence_encryption_key,
            settings.evidence_key_path,
        ),
    )
    result = worker.run_once()
    log.info("retention.batch", erased=result.erased, failed=result.failed)
    return result


def retention_loop(interval_seconds: float) -> None:
    """Enforce retention continuously.

    Its own process rather than a step inside the chain worker: an erasure that is already
    overdue must not wait on a registration, and a failing RPC must not postpone it.
    """
    configure_logging()
    settings = Settings.from_env()
    worker = RetentionWorker(
        session_factory=create_session_factory(settings.database_url),
        storage=FileEvidenceStorage(
            settings.evidence_storage_path,
            settings.evidence_encryption_key,
            settings.evidence_key_path,
        ),
    )
    log.info("retention.started", interval_seconds=interval_seconds)
    while True:
        try:
            result = worker.run_once()
            if result.erased or result.failed:
                log.info("retention.batch", erased=result.erased, failed=result.failed)
        except Exception as exc:  # noqa: BLE001 -- must outlive a transient database fault
            log.error("retention.batch_failed", error_type=exc.__class__.__name__)
        time.sleep(interval_seconds)


def rotate_encryption_key() -> dict[str, int]:
    """Re-seal everything under a new key-encryption key.

    Re-seals rather than swaps. A swap makes every bank connection dead and every evidence
    object unrecoverable in one step, which is the failure docs/key-rotation.md exists to
    prevent.

    An object whose key has already been destroyed is skipped and stays erased: rotation
    must not resurrect what somebody exercised a right to remove.
    """
    configure_logging()
    settings = Settings.from_env()
    new_key = _decode_encryption_key(os.getenv("EVIDENCE_ENCRYPTION_KEY_NEXT", ""))
    if not new_key:
        raise RuntimeError(
            "EVIDENCE_ENCRYPTION_KEY_NEXT is required, and must differ from the current key"
        )
    if new_key == settings.evidence_encryption_key:
        raise RuntimeError("The next key is the current key; nothing would be rotated")

    storage = FileEvidenceStorage(
        settings.evidence_storage_path,
        settings.evidence_encryption_key,
        settings.evidence_key_path,
    )
    factory = create_session_factory(settings.database_url)
    resealed = skipped = credentials = 0

    with factory.begin() as session:
        for record in session.scalars(select(EvidenceRecord)):
            if storage.rewrap(record.storage_uri, new_key):
                resealed += 1
            else:
                skipped += 1
        for credential in session.scalars(select(ProviderCredentialRecord)):
            if credential.revoked_at is not None or not credential.sealed_refresh_token:
                continue
            token = unseal(
                settings.evidence_encryption_key,
                credential.sealed_refresh_token,
                associated=f"{credential.organization_ref}:{credential.provider}",
            )
            credential.sealed_refresh_token = seal(
                new_key,
                token,
                associated=f"{credential.organization_ref}:{credential.provider}",
            )
            credentials += 1

    log.info(
        "rotation.complete", resealed=resealed, skipped=skipped, credentials=credentials
    )
    print(
        f"Re-sealed {resealed} evidence objects and {credentials} credentials. "
        f"Skipped {skipped} already-erased objects.\n"
        "Verify an integrity check and a bank connection under the new key before "
        "promoting it, and destroy the old key only after that passes."
    )
    return {"resealed": resealed, "skipped": skipped, "credentials": credentials}


def notification_loop(interval_seconds: float) -> None:
    """Drain notification intent from the outbox.

    Separate from the chain worker: the two claim different topics from the same table,
    and a slow or failing transport must not hold up a registration.
    """
    configure_logging()
    settings = Settings.from_env()
    dispatcher = NotificationDispatcher(
        session_factory=create_session_factory(settings.database_url),
        transport=ConsoleNotificationTransport(),
    )
    log.info("notifications.started", interval_seconds=interval_seconds)
    while True:
        try:
            sent = dispatcher.run_once()
            if sent:
                log.info("notifications.batch", sent=sent)
        except Exception as exc:  # noqa: BLE001 -- a sender must outlive a transient fault
            log.error("notifications.batch_failed", error_type=exc.__class__.__name__)
        time.sleep(interval_seconds)


def new_demo_run(network: str) -> None:
    if network not in {"local", "sepolia"}:
        raise RuntimeError("Demo run network must be local or sepolia")
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    suffix = uuid4().hex[:8]
    print(
        json.dumps(
            {
                "network": network,
                "demoRunId": f"clean-water-kenya-2026-{network}-{timestamp}-{suffix}",
            },
            indent=2,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="impactgraph")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("seed")
    reset = sub.add_parser("demo-reset")
    reset.add_argument("--local-only", action="store_true")
    sub.add_parser("reconcile-chain")
    sub.add_parser("worker-once")
    loop = sub.add_parser("worker")
    loop.add_argument("--interval", type=float, default=2.0)
    notify = sub.add_parser("notifications")
    notify.add_argument("--interval", type=float, default=5.0)
    sub.add_parser("retention-once")
    sub.add_parser("rotate-encryption-key")
    retention = sub.add_parser("retention")
    # Hourly by default: a retention period is measured in years, and checking more often
    # would be load without meaning.
    retention.add_argument("--interval", type=float, default=3600.0)
    deploy = sub.add_parser("deploy-registry")
    deploy.add_argument("--expect-address", default=None)
    sub.add_parser("bootstrap-chain")
    sub.add_parser("chain-args")
    record = sub.add_parser("record-evidence-registration")
    record.add_argument("--transaction-hash", required=True)
    run = sub.add_parser("new-demo-run")
    run.add_argument("--network", choices=("local", "sepolia"), required=True)
    args = parser.parse_args()
    if args.command == "seed":
        seed()
    elif args.command == "demo-reset":
        demo_reset(args.local_only)
    elif args.command == "reconcile-chain":
        reconcile_chain()
    elif args.command == "worker-once":
        worker_once()
    elif args.command == "worker":
        worker_loop(args.interval)
    elif args.command == "notifications":
        notification_loop(args.interval)
    elif args.command == "rotate-encryption-key":
        rotate_encryption_key()
    elif args.command == "retention-once":
        retention_once()
    elif args.command == "retention":
        retention_loop(args.interval)
    elif args.command == "deploy-registry":
        deploy_registry(args.expect_address)
    elif args.command == "bootstrap-chain":
        bootstrap_chain()
    elif args.command == "chain-args":
        chain_args()
    elif args.command == "record-evidence-registration":
        record_evidence_registration(args.transaction_hash)
    elif args.command == "new-demo-run":
        new_demo_run(args.network)


if __name__ == "__main__":
    main()
