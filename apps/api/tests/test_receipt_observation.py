"""A receipt only counts when the registry emitted it.

`web3` matches logs on their ABI topic and never compares the emitting address, so a
contract anyone can deploy can emit an event with the same signature as
`EvidenceRegistered`. Without the address check in `get_transaction`, the worker would
accept that as confirmation and the registry's role checks would be bypassed entirely by
a transaction that never touched it.

That guard sits in the real EVM adapter, which CI cannot reach, so it had no test at all.
These construct the adapter around stub RPC and contract objects instead, which needs no
chain.
"""

from __future__ import annotations

from web3 import Web3

from impactgraph.blockchain import EvmBlockchainService, entity_id_bytes

REGISTRY = Web3.to_checksum_address("0x5FbDB2315678afecb367f032d93F642f64180aa3")
IMPOSTOR = Web3.to_checksum_address("0x000000000000000000000000000000000000dEaD")
TX = "0x" + "11" * 32


class StubReceipt:
    status = 1
    blockNumber = 100
    transactionHash = bytes.fromhex("11" * 32)


class StubEvent:
    """One decoded log, as `process_receipt` would return it."""

    def __init__(self, name: str, address: str) -> None:
        self.name = name
        self.address = address

    def process_receipt(self, receipt, errors=None):
        if self.name != "EvidenceRegistered":
            return []
        return [
            {
                "address": self.address,
                "logIndex": 0,
                "args": {
                    "evidenceId": entity_id_bytes("ev-inv-8291"),
                    "programId": entity_id_bytes("program-clean-water-kenya-2026"),
                    "contentHash": bytes.fromhex("ab" * 32),
                },
            }
        ]


class StubEvents:
    def __init__(self, address: str) -> None:
        self._address = address

    def __getattr__(self, name: str):
        event = StubEvent(name, self._address)
        return lambda: event


class StubContract:
    def __init__(self, emitting_address: str) -> None:
        self.address = REGISTRY
        self.events = StubEvents(emitting_address)


class StubEth:
    block_number = 100

    def get_transaction_receipt(self, transaction_hash):
        return StubReceipt()

    def get_transaction(self, transaction_hash):
        return {"from": IMPOSTOR, "to": REGISTRY}


class StubWeb3:
    eth = StubEth()

    @staticmethod
    def to_hex(value):
        return Web3.to_hex(value)


def adapter(emitting_address: str) -> EvmBlockchainService:
    """Build the adapter without its constructor, which opens an RPC connection."""
    service = EvmBlockchainService.__new__(EvmBlockchainService)
    service.web3 = StubWeb3()
    service.contract = StubContract(emitting_address)
    service.sender = None
    return service


def test_an_event_from_the_registry_is_observed():
    observation = adapter(REGISTRY).get_transaction(TX)
    assert observation is not None
    assert [event["event"] for event in observation.events] == ["EvidenceRegistered"]


def test_an_event_from_another_contract_is_not_observed():
    """The whole guard. Anyone can deploy a contract that emits this signature; only the
    configured registry's word is evidence of anything."""
    observation = adapter(IMPOSTOR).get_transaction(TX)
    assert observation is not None
    assert observation.events == (), (
        "a same-signature event from an address that is not the registry was accepted"
    )


def test_a_spoofed_event_cannot_confirm_an_operation():
    """End of the chain: with no observed event there is nothing for the worker to
    confirm, so a counterfeit receipt fails the operation rather than advancing it."""
    from impactgraph.blockchain import BlockchainOperation, confirm_operation
    from impactgraph.domain import BlockchainStatus

    operation = BlockchainOperation(
        id="op-1",
        entity_id="ev-inv-8291",
        operation_type="REGISTER_EVIDENCE",
        expected_event="EvidenceRegistered",
        status=BlockchainStatus.SUBMITTED,
        transaction_hash=TX,
    )
    result = confirm_operation(operation, adapter(IMPOSTOR).get_transaction(TX), 1)
    assert result == BlockchainStatus.FAILED
    assert "Missing expected" in (operation.error or "")


def test_a_spoofed_registration_yields_no_commitment():
    """`commitment_from_receipt` must not read a content hash out of someone else's log."""
    from impactgraph.blockchain import commitment_from_receipt

    genuine = commitment_from_receipt(adapter(REGISTRY).get_transaction(TX), "ev-inv-8291")
    assert genuine == "sha256:" + "ab" * 32
    assert commitment_from_receipt(adapter(IMPOSTOR).get_transaction(TX), "ev-inv-8291") is None
