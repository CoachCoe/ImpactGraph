"""A tampered claim must stop reading as verified.

This was the worst outcome the product could produce. After the documented demo order --
verify the claim, then tamper with the evidence -- the requirement list correctly showed
EVIDENCE_INTEGRITY failing while the badge above it still said "Independently verified",
and the public dashboard still counted the claim as verified.

Two causes, both fixed here: the policy fell back to `claim.status` on a failure, so
re-evaluation could raise a status but never lower one; and nothing ever wrote the
CHALLENGED transition the domain has always modelled.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from impactgraph.auth import DEMO_PASSWORD
from impactgraph.domain import Claim, ClaimStatus, Result
from impactgraph.main import app, session_factory
from impactgraph.persistence import ClaimRecord
from impactgraph.verification import (
    ConfirmedVerification,
    VerificationContext,
    VerificationPolicyService,
    claims_supported_by,
)

CLAIM_ID = "claim-water-12-200"
EVIDENCE_ID = "ev-inv-8291"
ADMIN = "admin@impactgraph.example"


def context(**overrides) -> VerificationContext:
    base = {
        "provenance_complete": True,
        "evidence_registered": True,
        "evidence_integrity": True,
        "reconciliation_passes": True,
        "operator_attestation_confirmed": True,
        "verifications": (
            ConfirmedVerification("org-impactverify", "sha256:" + "a" * 64),
        ),
        "required_verifications": 1,
        "current_bundle_hash": "sha256:" + "a" * 64,
        "operator_id": "org-global-water",
    }
    return VerificationContext(**{**base, **overrides})


def claim(status: ClaimStatus) -> Claim:
    return Claim(
        id=CLAIM_ID,
        program_id="program-clean-water-kenya-2026",
        project_id="project-water-12",
        statement="200 households gained access to clean drinking water.",
        payload_hash="sha256:" + "0" * 64,
        status=status,
        evidence_ids=[EVIDENCE_ID],
        verification_bundle_hash="sha256:" + "a" * 64,
        verified_at=None,
        verification_policy_version="1.0",
    )


def test_a_verified_claim_that_fails_a_requirement_is_challenged_not_verified():
    decision = VerificationPolicyService().evaluate(
        claim(ClaimStatus.VERIFIED), context(evidence_integrity=False)
    )
    assert decision.status == ClaimStatus.CHALLENGED
    assert decision.status != ClaimStatus.VERIFIED


def test_re_evaluation_still_cannot_invent_a_verification():
    """The fix must not work in the other direction: a pending claim stays pending."""
    decision = VerificationPolicyService().evaluate(
        claim(ClaimStatus.VERIFICATION_PENDING), context(verifications=())
    )
    assert decision.status == ClaimStatus.VERIFICATION_PENDING


def test_a_satisfied_claim_is_still_verified():
    decision = VerificationPolicyService().evaluate(claim(ClaimStatus.VERIFICATION_PENDING), context())
    assert decision.status == ClaimStatus.VERIFIED
    assert all(item.status != Result.FAIL for item in decision.requirements)


def test_the_integrity_check_lowers_a_verified_claim_it_contradicts():
    """End to end: verify the claim, tamper, re-check, and read the claim back."""
    assert session_factory is not None
    with session_factory.begin() as session:
        record = session.scalar(select(ClaimRecord).where(ClaimRecord.external_id == CLAIM_ID))
        record.status = "VERIFIED"

    session = TestClient(app)
    assert session.get(f"/claims/{CLAIM_ID}").json()["status"] == "VERIFIED"

    session.post("/auth/login", json={"email": ADMIN, "password": DEMO_PASSWORD})
    assert session.post(f"/demo/evidence/{EVIDENCE_ID}/tamper").json()["status"] == "MISMATCH"

    after = TestClient(app).get(f"/claims/{CLAIM_ID}").json()
    assert after["status"] == "CHALLENGED", (
        "a claim whose evidence no longer matches its commitment must not read as verified"
    )
    assert after["verifiedAt"] is None

    verification = TestClient(app).get(f"/claims/{CLAIM_ID}/verification").json()
    integrity = next(
        item for item in verification["requirements"] if item["requirement"] == "EVIDENCE_INTEGRITY"
    )
    assert integrity["status"] == "FAIL"


def test_the_evidence_knows_which_claims_it_supports():
    assert session_factory is not None
    with session_factory() as session:
        supported = [claim.external_id for claim in claims_supported_by(session, EVIDENCE_ID)]
    assert CLAIM_ID in supported
