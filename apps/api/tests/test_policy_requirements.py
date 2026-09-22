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
from impactgraph.verification import (
    ConfirmedVerification,
    VerificationContext,
    VerificationPolicyService,
)

BUNDLE = "sha256:" + "a" * 64
OTHER_BUNDLE = "sha256:" + "b" * 64
VERIFIER = "org-impactverify"
OPERATOR = "org-global-water"


def context(**overrides) -> VerificationContext:
    satisfied = {
        "provenance_complete": True,
        "evidence_registered": True,
        "evidence_integrity": True,
        "reconciliation_passes": True,
        "operator_attestation_confirmed": True,
        "verifications": (ConfirmedVerification(VERIFIER, BUNDLE),),
        "required_verifications": 1,
        "current_bundle_hash": BUNDLE,
        "operator_id": OPERATOR,
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
    # Raising the bar rather than removing the attestation: an empty set would fail
    # BUNDLE_CURRENT too, and the table's contract is one requirement at a time.
    ("INDEPENDENT_VERIFICATION", {"required_verifications": 2}),
    ("BUNDLE_CURRENT", {"verifications": (ConfirmedVerification(VERIFIER, OTHER_BUNDLE),)}),
    ("ACTOR_SEPARATION", {"verifications": (ConfirmedVerification(OPERATOR, BUNDLE),)}),
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
    """With no attestation at all, BUNDLE_CURRENT failed with 'Attestation does not cover
    the current evidence bundle' -- a sentence asserting an attestation exists and is out
    of date. None exists."""
    decision = decide(verifications=())
    bundle = next(
        item for item in decision.requirements if item.requirement == "BUNDLE_CURRENT"
    )
    assert "does not cover" not in bundle.reason.lower(), bundle.reason
    assert "no independent attestation" in bundle.reason.lower(), bundle.reason


SECOND_VERIFIER = "org-secondopinion"


def test_two_distinct_verifiers_on_the_current_bundle_meet_a_threshold_of_two():
    decision = decide(
        required_verifications=2,
        verifications=(
            ConfirmedVerification(VERIFIER, BUNDLE),
            ConfirmedVerification(SECOND_VERIFIER, BUNDLE),
        ),
    )
    assert decision.status == ClaimStatus.VERIFIED


def test_a_stale_attestation_cannot_help_meet_the_threshold():
    """Two attestations, one of them against evidence that has since changed.

    Counting them equally would report two independent verifications of the current
    bundle when only one verifier ever saw it. BUNDLE_CURRENT is `all`, not `any`,
    precisely so the count cannot be padded with an attestation that is out of date.
    """
    decision = decide(
        required_verifications=2,
        verifications=(
            ConfirmedVerification(VERIFIER, BUNDLE),
            ConfirmedVerification(SECOND_VERIFIER, OTHER_BUNDLE),
        ),
    )
    failed = {item.requirement for item in decision.requirements if item.status == Result.FAIL}
    assert "BUNDLE_CURRENT" in failed
    assert decision.status != ClaimStatus.VERIFIED


def test_the_same_verifier_twice_does_not_meet_a_threshold_of_two():
    """Otherwise one organisation signing twice would look like independent corroboration."""
    decision = decide(
        required_verifications=2,
        verifications=(
            ConfirmedVerification(VERIFIER, BUNDLE),
            ConfirmedVerification(VERIFIER, BUNDLE),
        ),
    )
    failed = {item.requirement for item in decision.requirements if item.status == Result.FAIL}
    assert "ACTOR_SEPARATION" in failed
    assert decision.status != ClaimStatus.VERIFIED


def test_the_operator_cannot_make_up_the_numbers():
    decision = decide(
        required_verifications=2,
        verifications=(
            ConfirmedVerification(VERIFIER, BUNDLE),
            ConfirmedVerification(OPERATOR, BUNDLE),
        ),
    )
    failed = {item.requirement for item in decision.requirements if item.status == Result.FAIL}
    assert "ACTOR_SEPARATION" in failed
    assert decision.status != ClaimStatus.VERIFIED


def test_one_verification_still_verifies_at_the_default_threshold():
    """The default is 1, so every program that predates the threshold behaves as before."""
    assert decide().status == ClaimStatus.VERIFIED


def test_the_requirement_reason_names_the_shortfall():
    decision = decide(required_verifications=3)
    independent = next(
        item for item in decision.requirements if item.requirement == "INDEPENDENT_VERIFICATION"
    )
    assert "1 of 3" in independent.reason
