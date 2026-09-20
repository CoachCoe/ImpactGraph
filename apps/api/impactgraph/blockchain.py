from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Protocol

from web3 import Web3
from web3.contract import Contract
from web3.logs import DISCARD

from .domain import BlockchainStatus


@dataclass
class BlockchainOperation:
    id: str
    entity_id: str
    operation_type: str
    expected_event: str
    status: BlockchainStatus = BlockchainStatus.CREATED
    transaction_hash: str | None = None
    correlation_id: str = ""
    confirmations: int = 0
    error: str | None = None


@dataclass(frozen=True)
class ReceiptObservation:
    success: bool
    transaction_hash: str
    block_number: int
    confirmations: int
    events: tuple[dict[str, Any], ...]
    sender: str | None = None
    recipient: str | None = None


class BlockchainService(Protocol):
    def create_program(self, entity_id: str, commitment: str) -> str: ...
    def record_funding(self, entity_id: str, program_id: str, commitment: str) -> str: ...
    def record_allocation(self, entity_id: str, program_id: str, commitment: str) -> str: ...
    def record_financial_transaction(
        self, entity_id: str, program_id: str, commitment: str
    ) -> str: ...
    def record_delivery(self, entity_id: str, program_id: str, commitment: str) -> str: ...
    def register_evidence(self, entity_id: str, program_id: str, commitment: str) -> str: ...
    def create_attestation(
        self, entity_id: str, subject_id: str, statement_hash: str, bundle_hash: str
    ) -> str: ...
    def record_outcome(self, entity_id: str, program_id: str, commitment: str) -> str: ...
    def create_claim(self, entity_id: str, program_id: str, commitment: str) -> str: ...
    def link_provenance(self, source_id: str, relationship: str, target_id: str) -> str: ...
    def get_transaction(self, transaction_hash: str) -> ReceiptObservation | None: ...
    def entity_exists(self, entity_id: str) -> bool: ...
    def block_time(self, block_number: int) -> str | None: ...
    def find_evidence_registration(
        self, entity_id: str
    ) -> ReceiptObservation | None: ...


class MockBlockchainService:
    """Deterministic boundary for tests. It never claims to be Ethereum."""

    def __init__(self, sender: str | None = None) -> None:
        self._counter = 0
        self.receipts: dict[str, ReceiptObservation] = {}
        self.sender = sender

    def _submit(self, event: str, entity_id: str, **args: Any) -> str:
        self._counter += 1
        tx = "0x" + f"{self._counter:064x}"
        self.receipts[tx] = ReceiptObservation(
            True,
            tx,
            1000 + self._counter,
            1,
            ({"event": event, "entityId": entity_id, "logIndex": 0, "args": args},),
            self.sender,
            "0x" + "9" * 40,
        )
        return tx

    def create_program(self, entity_id: str, commitment: str) -> str:
        return self._submit("ProgramCreated", entity_id)

    def record_funding(self, entity_id: str, program_id: str, commitment: str) -> str:
        return self._submit("FundingRecorded", entity_id)

    def record_allocation(self, entity_id: str, program_id: str, commitment: str) -> str:
        return self._submit("AllocationRecorded", entity_id)

    def record_financial_transaction(self, entity_id: str, program_id: str, commitment: str) -> str:
        return self._submit("FinancialTransactionRecorded", entity_id)

    def record_delivery(self, entity_id: str, program_id: str, commitment: str) -> str:
        return self._submit("DeliveryRecorded", entity_id)

    def register_evidence(self, entity_id: str, program_id: str, commitment: str) -> str:
        return self._submit(
            "EvidenceRegistered",
            entity_id,
            programId=Web3.to_hex(entity_id_bytes(program_id)),
            contentHash=Web3.to_hex(digest_bytes(commitment)),
        )

    def create_attestation(
        self, entity_id: str, subject_id: str, statement_hash: str, bundle_hash: str
    ) -> str:
        return self._submit(
            "AttestationCreated",
            entity_id,
            subjectId=Web3.to_hex(entity_id_bytes(subject_id)),
            attestationType=INDEPENDENT_VERIFIER_ATTESTATION,
            statementHash=Web3.to_hex(digest_bytes(statement_hash)),
            verificationBundleHash=Web3.to_hex(digest_bytes(bundle_hash)),
            issuer=self.sender,
        )

    def record_outcome(self, entity_id: str, program_id: str, commitment: str) -> str:
        return self._submit("OutcomeRecorded", entity_id)

    def create_claim(self, entity_id: str, program_id: str, commitment: str) -> str:
        return self._submit("ClaimCreated", entity_id)

    def link_provenance(self, source_id: str, relationship: str, target_id: str) -> str:
        return self._submit("ProvenanceLinked", f"{source_id}:{relationship}:{target_id}")

    def get_transaction(self, transaction_hash: str) -> ReceiptObservation | None:
        return self.receipts.get(transaction_hash)

    def block_time(self, block_number: int) -> str | None:
        return datetime.fromtimestamp(1_756_000_000 + block_number, tz=UTC).isoformat()

    def entity_exists(self, entity_id: str) -> bool:
        return any(
            event["entityId"] == entity_id
            for receipt in self.receipts.values()
            for event in receipt.events
        )

    def find_evidence_registration(self, entity_id: str) -> ReceiptObservation | None:
        return next(
            (
                receipt
                for receipt in self.receipts.values()
                for event in receipt.events
                if event["event"] == "EvidenceRegistered"
                and event["entityId"] == entity_id
            ),
            None,
        )


# ImpactRegistry.AttestationType
OPERATOR_ATTESTATION = 0
INDEPENDENT_VERIFIER_ATTESTATION = 1

ENTITY_ID_ARGUMENTS = (
    "programId",
    "fundingId",
    "allocationId",
    "financialTransactionId",
    "deliveryId",
    "evidenceId",
    "attestationId",
    "outcomeId",
    "claimId",
    "edgeId",
)


def _hexify(value: Any) -> Any:
    return Web3.to_hex(value) if isinstance(value, bytes) else value


def entity_id_bytes(entity_id: str) -> bytes:
    """Canonical opaque onchain identifier for an application entity ID."""
    return hashlib.sha256(entity_id.encode("utf-8")).digest()


def digest_bytes(commitment: str) -> bytes:
    prefix, separator, value = commitment.partition(":")
    if separator != ":" or prefix != "sha256" or len(value) != 64:
        raise ValueError("Commitment must use sha256:<64 lowercase hex> representation")
    if value.lower() != value:
        raise ValueError("Commitment hex must be lowercase")
    try:
        return bytes.fromhex(value)
    except ValueError as exc:
        raise ValueError("Commitment contains non-hexadecimal characters") from exc


class EvmBlockchainService:
    """ImpactRegistry adapter for submission and independent receipt observation.

    `sender` is intended for unlocked Anvil accounts. Public-network verifier writes are
    prepared/signed by the browser; the backend then observes the resulting transaction.
    """

    event_names = (
        "ProgramCreated",
        "FundingRecorded",
        "AllocationRecorded",
        "FinancialTransactionRecorded",
        "DeliveryRecorded",
        "EvidenceRegistered",
        "AttestationCreated",
        "OutcomeRecorded",
        "ClaimCreated",
        "ProvenanceLinked",
        "AttestationRevoked",
    )
    relationships: ClassVar[dict[str, int]] = {
        "FUNDS": 0,
        "ALLOCATES_TO": 1,
        "PAYS": 2,
        "SUPPORTS": 3,
        "EVIDENCES": 4,
        "DELIVERS": 5,
        "ATTESTS": 6,
        "VERIFIES": 7,
        "PRODUCES": 8,
        "SUPERSEDES": 9,
    }

    def __init__(
        self,
        *,
        rpc_url: str,
        contract_address: str,
        abi: list[dict[str, Any]],
        sender: str | None = None,
    ) -> None:
        self.web3 = Web3(Web3.HTTPProvider(rpc_url))
        if not self.web3.is_connected():
            raise ConnectionError(f"Cannot connect to configured EVM RPC: {rpc_url}")
        self.contract: Contract = self.web3.eth.contract(
            address=Web3.to_checksum_address(contract_address), abi=abi
        )
        self.sender = Web3.to_checksum_address(sender) if sender else None

    @classmethod
    def from_foundry_artifact(
        cls,
        *,
        rpc_url: str,
        contract_address: str,
        artifact_path: Path,
        sender: str | None = None,
    ) -> EvmBlockchainService:
        artifact = json.loads(artifact_path.read_text())
        return cls(
            rpc_url=rpc_url,
            contract_address=contract_address,
            abi=artifact["abi"],
            sender=sender,
        )

    def _transact(self, function: Any) -> str:
        if self.sender is None:
            raise PermissionError(
                "No backend sender configured; use the wallet-signed intent workflow"
            )
        return self.web3.to_hex(function.transact({"from": self.sender}))

    def create_program(self, entity_id: str, commitment: str) -> str:
        return self._transact(
            self.contract.functions.createProgram(
                entity_id_bytes(entity_id), digest_bytes(commitment)
            )
        )

    def record_funding(self, entity_id: str, program_id: str, commitment: str) -> str:
        return self._transact(
            self.contract.functions.recordFunding(
                entity_id_bytes(entity_id), entity_id_bytes(program_id), digest_bytes(commitment)
            )
        )

    def record_allocation(self, entity_id: str, program_id: str, commitment: str) -> str:
        return self._transact(
            self.contract.functions.recordAllocation(
                entity_id_bytes(entity_id), entity_id_bytes(program_id), digest_bytes(commitment)
            )
        )

    def record_financial_transaction(self, entity_id: str, program_id: str, commitment: str) -> str:
        return self._transact(
            self.contract.functions.recordFinancialTransaction(
                entity_id_bytes(entity_id), entity_id_bytes(program_id), digest_bytes(commitment)
            )
        )

    def record_delivery(self, entity_id: str, program_id: str, commitment: str) -> str:
        return self._transact(
            self.contract.functions.recordDelivery(
                entity_id_bytes(entity_id), entity_id_bytes(program_id), digest_bytes(commitment)
            )
        )

    def register_evidence(self, entity_id: str, program_id: str, commitment: str) -> str:
        return self._transact(
            self.contract.functions.registerEvidence(
                entity_id_bytes(entity_id), entity_id_bytes(program_id), digest_bytes(commitment)
            )
        )

    def create_attestation(
        self, entity_id: str, subject_id: str, statement_hash: str, bundle_hash: str
    ) -> str:
        return self._transact(
            self.contract.functions.createAttestation(
                entity_id_bytes(entity_id),
                entity_id_bytes(subject_id),
                1,
                digest_bytes(statement_hash),
                digest_bytes(bundle_hash),
            )
        )

    def record_outcome(self, entity_id: str, program_id: str, commitment: str) -> str:
        return self._transact(
            self.contract.functions.recordOutcome(
                entity_id_bytes(entity_id), entity_id_bytes(program_id), digest_bytes(commitment)
            )
        )

    def create_claim(self, entity_id: str, program_id: str, commitment: str) -> str:
        return self._transact(
            self.contract.functions.createClaim(
                entity_id_bytes(entity_id), entity_id_bytes(program_id), digest_bytes(commitment)
            )
        )

    def link_provenance(self, source_id: str, relationship: str, target_id: str) -> str:
        try:
            relationship_value = self.relationships[relationship]
        except KeyError as exc:
            raise ValueError(f"Unknown provenance relationship: {relationship}") from exc
        return self._transact(
            self.contract.functions.linkProvenance(
                entity_id_bytes(source_id), relationship_value, entity_id_bytes(target_id)
            )
        )

    def block_time(self, block_number: int) -> str | None:
        """When the chain says the block was mined, in UTC.

        The chain's clock, not ours: this is the moment the commitment became
        unalterable, and the interface tells a donor nothing has changed since it.
        """
        try:
            block = self.web3.eth.get_block(block_number)
        except Exception as exc:
            # A pruned or not-yet-visible block costs the donor a date, not the check.
            # Anything else is a real fault and must not be swallowed.
            if exc.__class__.__name__ == "BlockNotFound":
                return None
            raise
        timestamp = block.get("timestamp")
        return (
            datetime.fromtimestamp(int(timestamp), tz=UTC).isoformat()
            if timestamp is not None
            else None
        )

    def entity_exists(self, entity_id: str) -> bool:
        return bool(self.contract.functions.entityExists(entity_id_bytes(entity_id)).call())

    def has_operator_role(self, address: str) -> bool:
        role = self.contract.functions.OPERATOR_ROLE().call()
        return bool(
            self.contract.functions.hasRole(role, Web3.to_checksum_address(address)).call()
        )

    def has_verifier_role(self, address: str) -> bool:
        role = self.contract.functions.VERIFIER_ROLE().call()
        return bool(
            self.contract.functions.hasRole(role, Web3.to_checksum_address(address)).call()
        )

    def find_evidence_registration(self, entity_id: str) -> ReceiptObservation | None:
        """Recover the registration receipt for evidence already on the registry.

        The database row carrying the transaction hash can be lost while the commitment
        stays on chain -- a reseeded database against a surviving chain. Re-registering
        reverts, because the contract enforces evidence uniqueness, so the reference has
        to be read back from the logs instead.

        Scanning from the first block is acceptable here: this runs only when the backend
        holds a signing key, which it must not on a public network.
        """
        events = self.contract.events.EvidenceRegistered().get_logs(
            from_block=0,
            argument_filters={"evidenceId": entity_id_bytes(entity_id)},
        )
        first = next(iter(events), None)
        if first is None:
            return None
        return self.get_transaction(Web3.to_hex(first["transactionHash"]))

    def grant_verifier_role(self, address: str) -> str:
        role = self.contract.functions.VERIFIER_ROLE().call()
        return self._transact(
            self.contract.functions.grantRole(role, Web3.to_checksum_address(address))
        )

    def get_transaction(self, transaction_hash: str) -> ReceiptObservation | None:
        try:
            receipt = self.web3.eth.get_transaction_receipt(transaction_hash)
        except Exception as exc:
            if exc.__class__.__name__ in {"TransactionNotFound", "TransactionIndexingInProgress"}:
                return None
            raise
        latest = self.web3.eth.block_number
        transaction = self.web3.eth.get_transaction(transaction_hash)
        confirmations = max(0, latest - receipt.blockNumber + 1)
        events: list[dict[str, Any]] = []
        for event_name in self.event_names:
            event_factory = getattr(self.contract.events, event_name)
            for decoded in event_factory().process_receipt(receipt, errors=DISCARD):
                # web3 matches logs on ABI topic alone and never compares the emitting
                # address, so without this guard any contract emitting a same-signature
                # event would satisfy confirmation and bypass the registry's role checks.
                if Web3.to_checksum_address(decoded["address"]) != self.contract.address:
                    continue
                args = {key: _hexify(value) for key, value in decoded["args"].items()}
                entity_value = next(
                    (value for key, value in args.items() if key in ENTITY_ID_ARGUMENTS),
                    None,
                )
                events.append(
                    {
                        "event": event_name,
                        "entityId": entity_value,
                        "logIndex": decoded["logIndex"],
                        "args": args,
                    }
                )
        return ReceiptObservation(
            success=receipt.status == 1,
            transaction_hash=self.web3.to_hex(receipt.transactionHash),
            block_number=receipt.blockNumber,
            confirmations=confirmations,
            events=tuple(events),
            sender=transaction.get("from"),
            recipient=transaction.get("to"),
        )


def confirm_operation(
    operation: BlockchainOperation,
    observation: ReceiptObservation | None,
    confirmations_required: int,
) -> BlockchainStatus:
    if observation is None:
        return operation.status
    if not observation.success:
        operation.status = BlockchainStatus.FAILED
        operation.error = "Transaction receipt indicates failure"
        return operation.status
    expected_entity_id = (
        operation.entity_id
        if operation.entity_id.startswith("0x") and len(operation.entity_id) == 66
        else Web3.to_hex(entity_id_bytes(operation.entity_id))
    )
    expected = any(
        event.get("event") == operation.expected_event
        and event.get("entityId") in {operation.entity_id, expected_entity_id}
        for event in observation.events
    )
    if not expected:
        operation.status = BlockchainStatus.FAILED
        operation.error = f"Missing expected {operation.expected_event} event"
        return operation.status
    operation.confirmations = observation.confirmations
    if observation.confirmations >= confirmations_required:
        operation.status = BlockchainStatus.CONFIRMED
    else:
        operation.status = BlockchainStatus.SUBMITTED
    return operation.status


class ProcessedEventSet:
    def __init__(self) -> None:
        self._keys: set[tuple[int, str, int]] = set()

    def process_once(self, chain_id: int, transaction_hash: str, log_index: int) -> bool:
        key = (chain_id, transaction_hash.lower(), log_index)
        if key in self._keys:
            return False
        self._keys.add(key)
        return True


def commitment_from_receipt(
    observation: ReceiptObservation | None, evidence_id: str
) -> str | None:
    """The content hash an EvidenceRegistered event committed for this evidence.

    Pure so it can be tested without a chain: the RPC call stays in the caller. Returns
    None when the receipt carries no matching registration, which the caller must treat
    as a failure rather than falling back to a locally held value -- the point of reading
    the registry is that the local copy is not trusted.
    """
    if observation is None:
        return None
    expected_id = Web3.to_hex(entity_id_bytes(evidence_id))
    event = next(
        (
            item
            for item in observation.events
            if item.get("event") == "EvidenceRegistered"
            and item.get("entityId") in {evidence_id, expected_id}
        ),
        None,
    )
    commitment = (event or {}).get("args", {}).get("contentHash")
    if not commitment:
        return None
    return "sha256:" + str(commitment).removeprefix("0x")
