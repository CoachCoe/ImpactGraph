"""Every verification requirement must be individually load-bearing.

Five of the eight could be deleted outright from `VerificationPolicyService.evaluate` with
the whole suite still green -- including EVIDENCE_INTEGRITY, the single requirement this
product exists to enforce. Proven by deleting it and watching 67 tests pass.

The table below flips exactly one context field at a time, so removing any requirement
fails here by name rather than silently.
"""

from __future__ import annotations

import pytest

from impactgraph.domain import Claim, ClaimStatus, Result
from impactgraph.verification import VerificationContext, VerificationPolicyService

BUNDLE = "sha256:" + "a" * 64
OTHER_BUNDLE = "sha256:" + "b" * 64


def context(**overrides) -> VerificationContext:
    satisfied = {
        "provenance_complete": True,
        "evidence_registered": True,
        "evidence_integrity": True,
        "reconciliation_passes": True,
        "operator_attestation_confirmed": True,
        "verifier_attestation_confirmed": True,
        "attestation_bundle_hash": BUNDLE,
        "current_bundle_hash": BUNDLE,
        "operator_id": "org-global-water",
        "verifier_id": "org-impactverify",
    }
    return VerificationContext(**{**satisfied, **overrides})


def claim() -> Claim:
    return Claim(
        id="claim-water-12-200",
        program_id="program-clean-water-kenya-2026",
        project_id="project-water-12",
        statement="200 households gained access to clean drinking water.",
        payload_hash="sha256:" + "0" * 64,
        status=ClaimStatus.VERIFICATION_PENDING,
        evidence_ids=["ev-inv-8291"],
        verification_bundle_hash=BUNDLE,
        verified_at=None,
        verification_policy_version="1.0",
    )


def decide(**overrides):
    return VerificationPolicyService().evaluate(claim(), context(**overrides))


# One requirement, the single context change that must break it, in the policy's own order.
BREAKS = [
    ("PROVENANCE_COMPLETE", {"provenance_complete": False}),
    ("EVIDENCE_REGISTERED", {"evidence_registered": False}),
    ("EVIDENCE_INTEGRITY", {"evidence_integrity": False}),
    ("FINANCIAL_RECONCILIATION", {"reconciliation_passes": False}),
    ("OPERATOR_ATTESTATION", {"operator_attestation_confirmed": False}),
    ("INDEPENDENT_VERIFICATION", {"verifier_attestation_confirmed": False}),
    ("BUNDLE_CURRENT", {"attestation_bundle_hash": OTHER_BUNDLE}),
    ("ACTOR_SEPARATION", {"verifier_id": "org-global-water"}),
]


def test_everything_satisfied_verifies():
    decision = decide()
    assert decision.status == ClaimStatus.VERIFIED
    assert [item.requirement for item in decision.requirements] == [name for name, _ in BREAKS], (
        "the policy's requirement list changed; this table must change with it"
    )


@pytest.mark.parametrize(("requirement", "break_it"), BREAKS, ids=[name for name, _ in BREAKS])
def test_each_requirement_is_load_bearing(requirement: str, break_it: dict):
    decision = decide(**break_it)
    failed = {item.requirement for item in decision.requirements if item.status == Result.FAIL}
    assert requirement in failed, f"{requirement} did not fail when its condition was false"
    assert decision.status != ClaimStatus.VERIFIED, (
        f"a claim verified despite {requirement} failing"
    )


@pytest.mark.parametrize(("requirement", "break_it"), BREAKS, ids=[name for name, _ in BREAKS])
def test_breaking_one_requirement_breaks_only_that_one(requirement: str, break_it: dict):
    """Otherwise a requirement could be deleted and its neighbour would cover for it."""
    decision = decide(**break_it)
    failed = {item.requirement for item in decision.requirements if item.status == Result.FAIL}
    assert failed == {requirement}, f"expected only {requirement} to fail, got {sorted(failed)}"


def test_an_absent_attestation_is_not_reported_as_a_stale_one():
    """With no attestation at all, attestation_bundle_hash is None and BUNDLE_CURRENT
    failed with 'Attestation does not cover the current evidence bundle' -- a sentence
    asserting an attestation exists and is out of date. None exists."""
    decision = decide(verifier_attestation_confirmed=False, attestation_bundle_hash=None)
    bundle = next(
        item for item in decision.requirements if item.requirement == "BUNDLE_CURRENT"
    )
    assert "does not cover" not in bundle.reason.lower(), bundle.reason
    assert "no independent attestation" in bundle.reason.lower(), bundle.reason
