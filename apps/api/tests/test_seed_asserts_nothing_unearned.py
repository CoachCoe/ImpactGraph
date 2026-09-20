"""The seed may not assert anything it cannot know.

Three fabrications lived here. The evidence claimed an onchain registration and a passed
integrity check; the operator attestation carried a placeholder wallet and transaction
hash and was marked CONFIRMED, satisfying a verification requirement on the strength of a
signature that never happened. That one sat twelve lines above the comment explaining why
exactly it had been removed from the evidence record.
"""

from __future__ import annotations

from sqlalchemy import select

from impactgraph.main import session_factory
from impactgraph.persistence import AttestationRecord, EvidenceRecord
from impactgraph.read_model import CLAIM_ID, EVIDENCE_ID, TransparencyReadRepository

PLACEHOLDER_WALLET = "0x" + "1" * 40
PLACEHOLDER_HASH = "0x" + "1" * 64


def operator_attestation() -> AttestationRecord:
    assert session_factory is not None
    with session_factory() as session:
        return session.scalar(
            select(AttestationRecord).where(
                AttestationRecord.subject_id == CLAIM_ID,
                AttestationRecord.attestation_type == "OPERATOR",
            )
        )


def test_the_operator_attestation_claims_no_chain_signature():
    record = operator_attestation()
    assert record is not None, "the operator attestation should still be seeded"
    assert record.transaction_hash is None
    assert record.issuer_wallet is None
    assert record.status == "RECORDED"


def test_no_placeholder_wallet_or_hash_survives_anywhere():
    assert session_factory is not None
    with session_factory() as session:
        for record in session.scalars(select(AttestationRecord)):
            assert record.issuer_wallet != PLACEHOLDER_WALLET
            assert record.transaction_hash != PLACEHOLDER_HASH


def test_the_seed_does_not_claim_registration_or_a_passed_integrity_check():
    assert session_factory is not None
    with session_factory() as session:
        record = session.scalar(
            select(EvidenceRecord).where(EvidenceRecord.external_id == EVIDENCE_ID)
        )
    assert record.blockchain_status == "NOT_STARTED"
    assert record.integrity_status == "NOT_CHECKED"
    assert "blockchainReference" not in (record.metadata_json or {})


def test_a_recorded_operator_attestation_still_satisfies_its_requirement():
    """It is a real assertion by the operating organisation, just not a chain signature.
    Requiring a transaction for it would demand something the system never produces."""
    assert session_factory is not None
    with session_factory() as session:
        verification = TransparencyReadRepository(session).verification(CLAIM_ID)
    operator = next(
        item for item in verification["requirements"] if item["requirement"] == "OPERATOR_ATTESTATION"
    )
    assert operator["status"] == "PASS"


def test_independent_verification_still_demands_a_confirmed_chain_attestation():
    """The bar that must not move: RECORDED is enough for the operator, never for the
    independent verifier. That is the one this product asks a reader to trust a chain for."""
    assert session_factory is not None
    with session_factory.begin() as session:
        session.add(
            AttestationRecord(
                external_id="att-verifier-recorded-only",
                attestation_type="INDEPENDENT_VERIFIER",
                subject_type="CLAIM",
                subject_id=CLAIM_ID,
                issuer_id="org-impactverify",
                issuer_wallet=None,
                statement_hash="sha256:" + "0" * 64,
                verification_bundle_hash="sha256:" + "0" * 64,
                transaction_hash=None,
                status="RECORDED",
            )
        )
    try:
        with session_factory() as session:
            verification = TransparencyReadRepository(session).verification(CLAIM_ID)
        independent = next(
            item
            for item in verification["requirements"]
            if item["requirement"] == "INDEPENDENT_VERIFICATION"
        )
        assert independent["status"] == "FAIL", (
            "a verifier attestation with no chain transaction must not count"
        )
    finally:
        with session_factory.begin() as session:
            session.execute(
                select(AttestationRecord).where(
                    AttestationRecord.external_id == "att-verifier-recorded-only"
                )
            )
            record = session.scalar(
                select(AttestationRecord).where(
                    AttestationRecord.external_id == "att-verifier-recorded-only"
                )
            )
            if record:
                session.delete(record)


def test_the_issuer_is_the_real_organisation_not_a_constant():
    assert session_factory is not None
    with session_factory() as session:
        claim = TransparencyReadRepository(session).claim(CLAIM_ID)
    operator = next(item for item in claim["attestations"] if item["type"] == "OPERATOR")
    assert operator["issuer"] == "Global Water Initiative"
    assert operator["onchain"] is False
