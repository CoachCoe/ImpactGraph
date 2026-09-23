from pathlib import Path

import pytest

from impactgraph.blockchain import (
    BlockchainOperation,
    ProcessedEventSet,
    ReceiptObservation,
    confirm_operation,
    digest_bytes,
    entity_id_bytes,
)
from impactgraph.domain import (
    BlockchainStatus,
    Claim,
    ClaimStatus,
    Evidence,
    EvidenceWorkflowStatus,
    transition_claim,
    transition_evidence,
)
from impactgraph.evidence import (
    FileEvidenceStorage,
    MockEvidenceAnalysisProvider,
    ReconciliationService,
    verify_integrity,
)
from impactgraph.hashing import (
    attestation_hash,
    claim_hash,
    financial_transaction_hash,
    sha256_bytes,
    verification_bundle_hash,
)
from impactgraph.verification import (
    ConfirmedVerification,
    VerificationContext,
    VerificationPolicyService,
)
from tests.conftest import TEST_ENCRYPTION_KEY


def make_claim() -> Claim:
    return Claim(
        "claim-1",
        "program-1",
        "project-1",
        "200 households gained access",
        claim_hash("claim-1", "200 households gained access", "outcome-1"),
        ClaimStatus.VERIFICATION_PENDING,
        ["ev-1"],
        "outcome-1",
    )


def test_evidence_requires_confirmed_receipt_and_expected_event():
    operation = BlockchainOperation(
        "op-1", "ev-1", "REGISTER_EVIDENCE", "EvidenceRegistered", BlockchainStatus.SUBMITTED
    )
    shallow = ReceiptObservation(
        True, "0x1", 10, 0, ({"event": "EvidenceRegistered", "entityId": "ev-1"},)
    )
    assert confirm_operation(operation, shallow, 1) == BlockchainStatus.SUBMITTED
    wrong = ReceiptObservation(
        True, "0x1", 10, 1, ({"event": "EvidenceRegistered", "entityId": "other"},)
    )
    assert confirm_operation(operation, wrong, 1) == BlockchainStatus.FAILED
    operation.status = BlockchainStatus.SUBMITTED
    correct = ReceiptObservation(
        True, "0x1", 10, 1, ({"event": "EvidenceRegistered", "entityId": "ev-1"},)
    )
    assert confirm_operation(operation, correct, 1) == BlockchainStatus.CONFIRMED


def test_evidence_state_machine_rejects_skips():
    evidence = Evidence(
        "ev-1",
        "project-1",
        "INVOICE",
        "file:///tmp/a",
        "sha256:" + "0" * 64,
        "text/plain",
        "operator",
        "upload",
    )
    with pytest.raises(ValueError):
        transition_evidence(evidence, EvidenceWorkflowStatus.REGISTERED_ONCHAIN)
    transition_evidence(evidence, EvidenceWorkflowStatus.ANALYZING)
    transition_evidence(evidence, EvidenceWorkflowStatus.ANALYZED)
    transition_evidence(evidence, EvidenceWorkflowStatus.REVIEWED)
    transition_evidence(evidence, EvidenceWorkflowStatus.REGISTRATION_PENDING)
    assert evidence.workflow_status == EvidenceWorkflowStatus.REGISTRATION_PENDING


def test_verified_claim_can_be_challenged_or_revoked_without_rewriting_history():
    claim = make_claim()
    claim.status = ClaimStatus.VERIFIED
    transition_claim(claim, ClaimStatus.CHALLENGED)
    transition_claim(claim, ClaimStatus.REVOKED)
    assert claim.status == ClaimStatus.REVOKED
    with pytest.raises(ValueError):
        transition_claim(claim, ClaimStatus.VERIFIED)


def test_policy_never_uses_ai_confidence_and_requires_confirmation_and_bundle():
    claim = make_claim()
    context = VerificationContext(
        True, True, True, True, True, (), 1, "sha256:bundle", "operator"
    )
    decision = VerificationPolicyService().evaluate(claim, context)
    assert decision.status == ClaimStatus.VERIFICATION_PENDING
    context = VerificationContext(
        True,
        True,
        True,
        True,
        True,
        (ConfirmedVerification("verifier", "sha256:bundle"),),
        1,
        "sha256:bundle",
        "operator",
    )
    assert VerificationPolicyService().evaluate(claim, context).status == ClaimStatus.VERIFIED


def test_bundle_hash_is_order_independent_but_changes_with_evidence():
    args = (
        "claim",
        "sha256:claim",
        ["sha256:b", "sha256:a"],
        "outcome",
        ["edge-b", "edge-a"],
        "1.0",
    )
    first = verification_bundle_hash(*args)
    assert first == verification_bundle_hash(
        "claim", "sha256:claim", ["sha256:a", "sha256:b"], "outcome", ["edge-a", "edge-b"], "1.0"
    )
    assert first != verification_bundle_hash(
        "claim", "sha256:claim", ["sha256:a", "sha256:c"], "outcome", ["edge-a", "edge-b"], "1.0"
    )


def test_original_bytes_are_immutable_and_integrity_detects_changes(tmp_path: Path):
    storage = FileEvidenceStorage(tmp_path, TEST_ENCRYPTION_KEY)
    content = b"Invoice INV-8291"
    uri = storage.store("ev-1", content)
    expected = sha256_bytes(content)
    assert verify_integrity(storage.retrieve(uri), expected)[0]
    with pytest.raises(FileExistsError):
        storage.store("ev-1", b"altered")
    assert not verify_integrity(b"altered", expected)[0]


def test_mock_ai_and_deterministic_reconciliation():
    result = MockEvidenceAnalysisProvider().analyze(b"Invoice INV-8291", "text/plain")
    transaction = {
        "id": "ftx-9182",
        "payee": "Aqua Systems Ltd.",
        "amountMinor": 420000,
        "currency": "USD",
        "memo": "INV-8291 AquaPure X200 x2",
    }
    delivery = {
        "id": "delivery-water-12",
        "projectId": "project-water-12",
        "financialTransactionId": "ftx-9182",
        "item": "AquaPure X200",
        "quantity": 2,
    }
    reconciliation = ReconciliationService().reconcile_invoice(
        result.extraction, transaction, delivery, True, [True, False]
    )
    assert reconciliation["status"] == "PARTIAL_MATCH"
    assert any(item["result"] == "WARNING" for item in reconciliation["checks"])

    # A document the payment does not reference is a conflict, not a pass.
    unreferenced = ReconciliationService().reconcile_invoice(
        result.extraction, {**transaction, "memo": "unrelated"}, delivery, True, [True]
    )
    assert unreferenced["status"] == "CONFLICT"

    # No counterpart at all is UNMATCHED, which is a different answer from CONFLICT.
    orphan = ReconciliationService().reconcile_invoice(result.extraction, None, None, True, [True])
    assert orphan["status"] == "UNMATCHED"


def test_chain_event_processing_is_idempotent():
    events = ProcessedEventSet()
    assert events.process_once(31337, "0xABC", 2)
    assert not events.process_once(31337, "0xabc", 2)


def test_evm_identifier_and_digest_encodings_are_strict_and_deterministic():
    assert entity_id_bytes("ev-1") == entity_id_bytes("ev-1")
    assert entity_id_bytes("ev-1") != entity_id_bytes("ev-2")
    assert digest_bytes("sha256:" + "ab" * 32) == bytes.fromhex("ab" * 32)
    with pytest.raises(ValueError):
        digest_bytes("sha256:" + "AB" * 32)
    with pytest.raises(ValueError):
        digest_bytes("0x" + "ab" * 32)


def test_documented_hash_vectors_in_docs_hashing_md():
    """Pin every test vector published in docs/hashing.md.

    These are the cross-language commitment contract. Without assertions the document and
    the implementation can drift silently, and a second implementer has nothing to check
    their codec against.
    """
    assert (
        sha256_bytes(b"Invoice INV-8291\n")
        == "sha256:76874e018df604591aab602a926e0f70f1c93e699afa32eb277e13e8ffbb358c"
    )
    documented_claim_hash = claim_hash(
        "claim-water-12-200",
        "200 households gained access to clean drinking water.",
        "outcome-water-12-200",
        "1.0",
    )
    assert (
        documented_claim_hash
        == "sha256:22a97a7d2f721d60c7572d652858e8f6d734da22041da0630c06594c8c0f9bff"
    )
    assert (
        financial_transaction_hash(
            "TX-9182", 420000, "USD", "org-gwi", "vendor-aqua", "2026-08-17", "mock-bank-9182", "1.0"
        )
        == "sha256:821156a29c50ddbd64fb917773eb5862145bd76e8d5e44e4c696e080689bc5e6"
    )
    assert (
        verification_bundle_hash(
            "claim-water-12-200",
            documented_claim_hash,
            ["sha256:" + "a" * 64, "sha256:" + "b" * 64],
            "outcome-water-12-200",
            ["edge-2", "edge-1"],
            "1.0",
        )
        == "sha256:aab4f7360a3fb2c50bf646d9ada99b1b06365f51fdcc83fc7c63f5ad5e67ad0b"
    )


def test_attestation_hash_is_bound_to_its_bundle():
    common = ("att-1", "INDEPENDENT_VERIFIER", "CLAIM", "claim-water-12-200", "org-impactverify")
    statement = "sha256:" + "1" * 64
    for_bundle_a = attestation_hash(*common, statement, "sha256:" + "a" * 64)
    for_bundle_b = attestation_hash(*common, statement, "sha256:" + "b" * 64)
    assert for_bundle_a != for_bundle_b


def test_registry_commitment_is_read_from_the_matching_event():
    """The registry receipt, not the local copy, is the authority on what was committed.

    These paths were unreachable in tests because the suite runs with no registry
    configured, so the resolution logic is now a pure function over an observation.
    """
    from web3 import Web3

    from impactgraph.blockchain import (
        ReceiptObservation,
        commitment_from_receipt,
        entity_id_bytes,
    )

    evidence_id = "ev-inv-8291"
    committed = "0x" + "ab" * 32

    def observation(*events):
        return ReceiptObservation(True, "0x" + "1" * 64, 100, 3, tuple(events))

    registered = {
        "event": "EvidenceRegistered",
        "entityId": Web3.to_hex(entity_id_bytes(evidence_id)),
        "logIndex": 0,
        "args": {"contentHash": committed},
    }
    assert commitment_from_receipt(observation(registered), evidence_id) == (
        "sha256:" + "ab" * 32
    )

    # A registration for different evidence in the same receipt must not be borrowed.
    other = {**registered, "entityId": Web3.to_hex(entity_id_bytes("ev-something-else"))}
    assert commitment_from_receipt(observation(other), evidence_id) is None

    # Nor an unrelated event that happens to carry a contentHash.
    wrong_event = {**registered, "event": "AttestationCreated"}
    assert commitment_from_receipt(observation(wrong_event), evidence_id) is None

    # No receipt at all is a failure, never a silent fallback to the local value.
    assert commitment_from_receipt(None, evidence_id) is None
    assert commitment_from_receipt(observation(), evidence_id) is None


def test_the_seed_does_not_fabricate_a_registry_reference():
    """A made-up transaction hash would be believed by anything reading the registry."""
    import tempfile
    from pathlib import Path

    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker

    from impactgraph.evidence import FileEvidenceStorage
    from impactgraph.persistence import Base, EvidenceRecord
    from impactgraph.read_model import EVIDENCE_ID, seed_read_model

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    storage = FileEvidenceStorage(Path(tempfile.mkdtemp()), TEST_ENCRYPTION_KEY)
    with factory.begin() as session:
        seed_read_model(session, storage)
    with factory() as session:
        record = session.scalar(
            select(EvidenceRecord).where(EvidenceRecord.external_id == EVIDENCE_ID)
        )
        metadata = record.metadata_json or {}
        assert "blockchainReference" not in metadata
        # It still records the program it belongs to, which registration requires.
        assert metadata["programId"] == "program-clean-water-kenya-2026"


def test_seeding_registers_the_evidence_when_a_signer_is_configured(tmp_path, monkeypatch):
    """The seed must actually register, not merely report that it would.

    A patch once spliced another function into the middle of this one, leaving only its
    guard clause. It returned None on every call and the registration body became
    unreachable, so the local demo silently stopped committing anything on chain and
    integrity verification began returning 409. Ruff's default rules do not flag
    unreachable code, and nothing exercised the path.
    """
    from pathlib import Path

    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker

    from impactgraph import cli
    from impactgraph.blockchain import MockBlockchainService
    from impactgraph.config import Settings
    from impactgraph.evidence import FileEvidenceStorage
    from impactgraph.persistence import Base, EvidenceRecord
    from impactgraph.read_model import EVIDENCE_ID, seed_read_model

    database_url = f"sqlite+pysqlite:///{tmp_path / 'seed.db'}"
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory.begin() as session:
        seed_read_model(session, FileEvidenceStorage(Path(tmp_path / "evidence"), TEST_ENCRYPTION_KEY))

    monkeypatch.setattr(cli, "create_session_factory", lambda _url: factory)
    settings = Settings.from_env()
    object.__setattr__(settings, "database_url", database_url)
    object.__setattr__(settings, "registry_address", "0x" + "9" * 40)
    object.__setattr__(settings, "evm_sender_address", "0x" + "1" * 40)

    reference = cli.register_seeded_evidence(settings, chain=MockBlockchainService())
    assert reference is not None, "a configured signer must produce a registration"
    assert reference["transactionHash"].startswith("0x")

    with factory() as session:
        record = session.scalar(
            select(EvidenceRecord).where(EvidenceRecord.external_id == EVIDENCE_ID)
        )
        assert record.metadata_json["blockchainReference"] == reference
        assert record.blockchain_status == "CONFIRMED"

    # Re-running is a no-op rather than a second registration.
    again = cli.register_seeded_evidence(settings, chain=MockBlockchainService())
    assert again == reference


def test_seeding_a_fresh_database_against_a_surviving_chain_does_not_re_register(
    tmp_path, monkeypatch
):
    """The reseed case, which the database guard alone cannot cover.

    `demo.sh up` keeps its chain volume. Wipe the database and seed again and the row
    carrying the transaction hash is gone while the commitment is still on the registry.
    Registering again reverts on evidence uniqueness, which took the whole demo stack
    down with a contract error during gas estimation.

    The reference has to be recovered rather than merely skipped: integrity verification
    resolves the commitment through that transaction hash, so a record marked CONFIRMED
    without one answers 409.
    """
    from pathlib import Path as _Path

    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker

    from impactgraph import cli
    from impactgraph.blockchain import MockBlockchainService
    from impactgraph.config import Settings
    from impactgraph.evidence import FileEvidenceStorage
    from impactgraph.persistence import Base, EvidenceRecord
    from impactgraph.read_model import EVIDENCE_ID, PROGRAM_ID, seed_read_model

    def fresh_database(name: str):
        url = f"sqlite+pysqlite:///{tmp_path / name}"
        engine = create_engine(url)
        Base.metadata.create_all(engine)
        made = sessionmaker(engine, expire_on_commit=False)
        with made.begin() as session:
            seed_read_model(session, FileEvidenceStorage(_Path(tmp_path / "evidence"), TEST_ENCRYPTION_KEY))
        return url, made

    settings = Settings.from_env()
    object.__setattr__(settings, "registry_address", "0x" + "9" * 40)
    object.__setattr__(settings, "evm_sender_address", "0x" + "1" * 40)

    # One chain outlives both databases, as the demo's chain volume outlives its postgres.
    chain = MockBlockchainService()

    first_url, first = fresh_database("one.db")
    object.__setattr__(settings, "database_url", first_url)
    monkeypatch.setattr(cli, "create_session_factory", lambda _url: first)
    original = cli.register_seeded_evidence(settings, chain=chain)
    assert original is not None

    second_url, second = fresh_database("two.db")
    object.__setattr__(settings, "database_url", second_url)
    monkeypatch.setattr(cli, "create_session_factory", lambda _url: second)

    submitted: list[str] = []
    real_register = chain.register_evidence

    def counting_register(entity_id, program_id, commitment):
        submitted.append(entity_id)
        return real_register(entity_id, program_id, commitment)

    monkeypatch.setattr(chain, "register_evidence", counting_register)

    recovered = cli.register_seeded_evidence(settings, chain=chain)
    assert submitted == [], "a second registration would revert on evidence uniqueness"
    assert recovered == original, "the reference must be recovered, not discarded"

    with second() as session:
        record = session.scalar(
            select(EvidenceRecord).where(EvidenceRecord.external_id == EVIDENCE_ID)
        )
        assert record.metadata_json["blockchainReference"] == original
        assert record.blockchain_status == "CONFIRMED"

    # A chain that has never seen the evidence still registers it.
    empty = MockBlockchainService()
    third_url, third = fresh_database("three.db")
    object.__setattr__(settings, "database_url", third_url)
    monkeypatch.setattr(cli, "create_session_factory", lambda _url: third)
    assert empty.find_evidence_registration(EVIDENCE_ID) is None
    assert cli.register_seeded_evidence(settings, chain=empty) is not None
    assert empty.entity_exists(EVIDENCE_ID)
    assert PROGRAM_ID


def test_seeding_reports_why_it_did_not_register():
    from impactgraph.cli import register_seeded_evidence, seeded_registration_note
    from impactgraph.config import Settings

    settings = Settings.from_env()
    object.__setattr__(settings, "registry_address", "")
    object.__setattr__(settings, "evm_sender_address", "")
    assert register_seeded_evidence(settings) is None
    assert "no registry configured" in seeded_registration_note(settings)

    # A registry but no signer is correct on a public network, and says so.
    object.__setattr__(settings, "registry_address", "0x" + "9" * 40)
    assert register_seeded_evidence(settings) is None
    assert "holds no signing key" in seeded_registration_note(settings)
