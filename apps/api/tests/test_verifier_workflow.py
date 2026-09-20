from uuid import UUID

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from web3 import Web3

from impactgraph.blockchain import (
    INDEPENDENT_VERIFIER_ATTESTATION,
    OPERATOR_ATTESTATION,
    MockBlockchainService,
    digest_bytes,
)
from impactgraph.domain import BlockchainStatus, Role
from impactgraph.persistence import (
    AttestationRecord,
    Base,
    BlockchainOperationRecord,
    ClaimRecord,
    EvidenceRecord,
)
from impactgraph.read_model import CLAIM_ID, seed_read_model
from impactgraph.services import (
    ApplicationActor,
    AuthorizationError,
    VerificationApplicationService,
)
from impactgraph.worker import BlockchainOutboxWorker


def factory(*, evidence_ready: bool = True) -> sessionmaker[Session]:
    """A seeded database.

    The seed deliberately no longer claims the showcase evidence is registered onchain or
    that its integrity has been checked -- it cannot know either. Tests that need a claim
    capable of reaching VERIFIED therefore state those preconditions themselves, which is
    what the worker and the integrity endpoint would have written in a running system.
    """
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    result = sessionmaker(engine, expire_on_commit=False)
    with result.begin() as session:
        seed_read_model(session)
        if evidence_ready:
            for record in session.scalars(select(EvidenceRecord)):
                record.blockchain_status = "CONFIRMED"
                record.integrity_status = "MATCH"
    return result


def test_wallet_submission_does_not_verify_until_receipt_event_confirmation():
    db_factory = factory()
    verifier = ApplicationActor(
        "org-impactverify", Role.VERIFIER, "0x2222222222222222222222222222222222222222"
    )
    service = VerificationApplicationService(31337, "0x" + "9" * 40)
    with db_factory.begin() as session:
        intent = service.create_verifier_intent(
            session,
            actor=verifier,
            claim_id=CLAIM_ID,
            correlation_id="corr-verifier",
            idempotency_key="verify-intent-1",
        )
    blockchain = MockBlockchainService(sender=verifier.wallet)
    transaction_hash = blockchain.create_attestation(
        intent["attestationId"],
        CLAIM_ID,
        intent["arguments"]["statementHash"].replace("0x", "sha256:"),
        intent["arguments"]["verificationBundleHash"].replace("0x", "sha256:"),
    )
    with db_factory.begin() as session:
        submitted = service.record_wallet_submission(
            session,
            actor=verifier,
            operation_id=UUID(intent["operationId"]),
            transaction_hash=transaction_hash,
            correlation_id="corr-verifier",
        )
    assert submitted["status"] == "SUBMITTED"
    with db_factory() as session:
        claim = session.scalar(select(ClaimRecord).where(ClaimRecord.external_id == CLAIM_ID))
        assert claim is not None and claim.status == "VERIFICATION_PENDING"

    result = BlockchainOutboxWorker(
        session_factory=db_factory,
        blockchain=blockchain,
        confirmations_required=1,
    ).run_once()
    assert result.confirmed == 1
    with db_factory() as session:
        claim = session.scalar(select(ClaimRecord).where(ClaimRecord.external_id == CLAIM_ID))
        assert claim is not None and claim.status == "VERIFIED"
        assert claim.verified_at is not None


def _intent_and_verifier(db_factory):
    verifier = ApplicationActor(
        "org-impactverify", Role.VERIFIER, "0x2222222222222222222222222222222222222222"
    )
    service = VerificationApplicationService(31337, "0x" + "9" * 40)
    with db_factory.begin() as session:
        intent = service.create_verifier_intent(
            session,
            actor=verifier,
            claim_id=CLAIM_ID,
            correlation_id="corr-verifier",
            idempotency_key="verify-intent-1",
        )
    return service, verifier, intent


def test_attestation_committing_a_different_bundle_onchain_does_not_verify():
    db_factory = factory()
    service, verifier, intent = _intent_and_verifier(db_factory)
    blockchain = MockBlockchainService(sender=verifier.wallet)
    # The wallet signs a bundle the backend never issued. The database copies still agree
    # with each other, so only reading the committed value back can catch this.
    transaction_hash = blockchain.create_attestation(
        intent["attestationId"],
        CLAIM_ID,
        intent["arguments"]["statementHash"].replace("0x", "sha256:"),
        "sha256:" + "c" * 64,
    )
    with db_factory.begin() as session:
        service.record_wallet_submission(
            session,
            actor=verifier,
            operation_id=UUID(intent["operationId"]),
            transaction_hash=transaction_hash,
            correlation_id="corr-verifier",
        )

    BlockchainOutboxWorker(
        session_factory=db_factory, blockchain=blockchain, confirmations_required=1
    ).run_once()

    with db_factory() as session:
        claim = session.scalar(select(ClaimRecord).where(ClaimRecord.external_id == CLAIM_ID))
        assert claim is not None and claim.status == "VERIFICATION_PENDING"
        operation = session.get(BlockchainOperationRecord, UUID(intent["operationId"]))
        assert operation is not None
        assert operation.status == BlockchainStatus.FAILED
        assert "different verification bundle" in (operation.error or "")
        attestation = session.scalar(
            select(AttestationRecord).where(
                AttestationRecord.external_id == intent["attestationId"]
            )
        )
        assert attestation is not None and attestation.status == "FAILED"


def test_attestation_from_a_different_wallet_is_not_attributed_to_bound_verifier():
    db_factory = factory()
    service, verifier, intent = _intent_and_verifier(db_factory)
    other_wallet = "0x3333333333333333333333333333333333333333"
    blockchain = MockBlockchainService(sender=other_wallet)
    transaction_hash = blockchain.create_attestation(
        intent["attestationId"],
        CLAIM_ID,
        intent["arguments"]["statementHash"].replace("0x", "sha256:"),
        intent["arguments"]["verificationBundleHash"].replace("0x", "sha256:"),
    )
    with db_factory.begin() as session:
        service.record_wallet_submission(
            session,
            actor=verifier,
            operation_id=UUID(intent["operationId"]),
            transaction_hash=transaction_hash,
            correlation_id="corr-verifier",
        )
    BlockchainOutboxWorker(
        session_factory=db_factory, blockchain=blockchain, confirmations_required=1
    ).run_once()
    with db_factory() as session:
        operation = session.get(BlockchainOperationRecord, UUID(intent["operationId"]))
        claim = session.scalar(select(ClaimRecord).where(ClaimRecord.external_id == CLAIM_ID))
        assert operation is not None and operation.status == BlockchainStatus.FAILED
        assert "issuer" in (operation.error or "")
        assert claim is not None and claim.status == "VERIFICATION_PENDING"


class _Claim:
    def __init__(self, bundle: str) -> None:
        self.verification_bundle_hash = bundle


BUNDLE = "sha256:" + "a" * 64


def _event(**args):
    return {
        "event": "AttestationCreated",
        "entityId": "att-1",
        "logIndex": 0,
        "args": {
            "verificationBundleHash": Web3.to_hex(digest_bytes(BUNDLE)),
            "attestationType": INDEPENDENT_VERIFIER_ATTESTATION,
            **args,
        },
    }


def test_onchain_attestation_accepted_when_bundle_and_type_match():
    assert (
        BlockchainOutboxWorker._onchain_attestation_mismatch(
            [_event()], "att-1", _Claim(BUNDLE)
        )
        is None
    )


def test_onchain_attestation_rejected_when_receipt_has_no_matching_event():
    reason = BlockchainOutboxWorker._onchain_attestation_mismatch([], "att-1", _Claim(BUNDLE))
    assert reason is not None and "No AttestationCreated event" in reason


def test_onchain_attestation_rejected_when_operator_type():
    reason = BlockchainOutboxWorker._onchain_attestation_mismatch(
        [_event(attestationType=OPERATOR_ATTESTATION)], "att-1", _Claim(BUNDLE)
    )
    assert reason is not None and "independent verifier" in reason


def test_onchain_attestation_rejected_when_bundle_hash_absent():
    event = _event()
    del event["args"]["verificationBundleHash"]
    reason = BlockchainOutboxWorker._onchain_attestation_mismatch([event], "att-1", _Claim(BUNDLE))
    assert reason is not None and "no verification bundle hash" in reason


def test_the_operating_organisation_cannot_verify_its_own_claim():
    """Separation of duties now resolves the program's operator from the database.

    Previously this compared the actor id against the literal "org-global-water", and the
    actor id came from a request header the caller chose.
    """
    db_factory = factory()
    service = VerificationApplicationService(31337, "0x" + "9" * 40)
    operator_wearing_a_verifier_hat = ApplicationActor(
        "org-global-water", Role.VERIFIER, "0x3333333333333333333333333333333333333333"
    )
    with db_factory.begin() as session:
        try:
            service.create_verifier_intent(
                session,
                actor=operator_wearing_a_verifier_hat,
                claim_id=CLAIM_ID,
                correlation_id="corr-self-verify",
                idempotency_key="self-verify-1",
            )
        except AuthorizationError as exc:
            assert "cannot independently verify" in str(exc)
        else:
            raise AssertionError("the operating organisation must not be able to self-verify")

    with db_factory() as session:
        claim = session.scalar(select(ClaimRecord).where(ClaimRecord.external_id == CLAIM_ID))
        assert claim is not None and claim.status == "VERIFICATION_PENDING"


def test_a_confirmed_attestation_does_not_verify_a_policy_ineligible_claim():
    """A valid onchain attestation is necessary, not sufficient.

    The chain confirms that an identified verifier signed a bundle. Whether the claim is
    verified is a policy question, and the policy can still say no -- here because the
    supporting evidence no longer reconciles. The attestation must still be recorded as
    CONFIRMED: it genuinely happened, and discarding it would lose the fact.
    """
    db_factory = factory()
    service, verifier, intent = _intent_and_verifier(db_factory)

    with db_factory.begin() as session:
        evidence = session.scalar(
            select(EvidenceRecord).where(EvidenceRecord.external_id == "ev-inv-8291")
        )
        assert evidence is not None
        reconciliation = dict(evidence.reconciliation or {})
        reconciliation["status"] = "CONFLICT"
        evidence.reconciliation = reconciliation

    blockchain = MockBlockchainService(sender=verifier.wallet)
    transaction_hash = blockchain.create_attestation(
        intent["attestationId"],
        CLAIM_ID,
        intent["arguments"]["statementHash"].replace("0x", "sha256:"),
        intent["arguments"]["verificationBundleHash"].replace("0x", "sha256:"),
    )
    with db_factory.begin() as session:
        service.record_wallet_submission(
            session,
            actor=verifier,
            operation_id=UUID(intent["operationId"]),
            transaction_hash=transaction_hash,
            correlation_id="corr-verifier",
        )

    result = BlockchainOutboxWorker(
        session_factory=db_factory, blockchain=blockchain, confirmations_required=1
    ).run_once()
    assert result.confirmed == 1

    with db_factory() as session:
        claim = session.scalar(select(ClaimRecord).where(ClaimRecord.external_id == CLAIM_ID))
        assert claim is not None
        assert claim.status == "VERIFICATION_PENDING"
        assert claim.verified_at is None

        attestation = session.scalar(
            select(AttestationRecord).where(
                AttestationRecord.external_id == intent["attestationId"]
            )
        )
        assert attestation is not None and attestation.status == "CONFIRMED"

        operation = session.get(BlockchainOperationRecord, UUID(intent["operationId"]))
        assert operation is not None and operation.status == BlockchainStatus.CONFIRMED
