"""Matching a payment to an invoice when the bank mangled the reference.

A substring test was the whole of this, which finds nothing on a real memo: they arrive
truncated and stripped of punctuation. Matching loosely instead would invent a
reconciliation nobody checked, and reconciliation is what makes a CONFLICT verdict mean
anything.
"""

from __future__ import annotations

import pytest

from impactgraph.domain import Result
from impactgraph.evidence import ReconciliationService
from impactgraph.financial import MEMO_MATCH_THRESHOLD, match_memo


def check(memo: str, invoice: str = "INV-8291") -> dict:
    return ReconciliationService._memo_check(
        {"invoiceNumber": invoice}, {"id": "ftx-9182", "memo": memo}
    )


def test_a_memo_that_quotes_the_invoice_matches_outright():
    assert match_memo("Payment for INV-8291", "INV-8291").confidence == 1.0
    assert check("Payment for INV-8291")["result"] == Result.PASS


def test_a_bank_that_stripped_the_prefix_is_still_recognised():
    """"INV-8291" arriving as "8291" is ordinary; it is not a different invoice."""
    match = match_memo("CARD PAYMENT TO AQUA SYSTEM 8291", "INV-8291")
    assert match.confidence >= MEMO_MATCH_THRESHOLD
    assert match.confidence < 1.0
    assert check("CARD PAYMENT TO AQUA SYSTEM 8291")["result"] == Result.WARNING


def test_a_probable_match_is_a_warning_and_not_a_pass():
    """A match nobody checked is worse here than no match at all, because a passing
    reconciliation is what a verified claim rests on."""
    result = check("CARD PAYMENT TO AQUA SYSTEM 8291")
    assert result["result"] == Result.WARNING
    assert "Confirm it" in result["message"]
    assert result["confidence"] < 1.0


def test_a_different_invoice_is_not_matched():
    assert match_memo("REF 88213 AQUA", "INV-8291").confidence == 0.0
    assert match_memo("BACS 4821 SUPPLIER", "INV-8291").confidence == 0.0
    assert check("REF 88213 AQUA")["result"] == Result.FAIL


def test_an_empty_memo_matches_nothing_rather_than_everything():
    """An empty needle in an empty haystack is trivially contained, which would make every
    payment with no memo reference every document."""
    assert match_memo("", "INV-8291").confidence == 0.0
    assert match_memo("Payment for INV-8291", "").confidence == 0.0
    assert check("")["result"] == Result.FAIL


def test_punctuation_and_case_do_not_decide_the_answer():
    for memo in ("payment inv8291", "PAYMENT INV/8291", "Payment  inv-8291  thanks"):
        assert match_memo(memo, "INV-8291").confidence == 1.0


def test_the_score_is_reported_so_a_person_can_decide():
    """Self-reported in the same sense as extraction confidence: a number this code
    computed, not a probability anyone measured."""
    assert "confidence" in check("CARD PAYMENT TO AQUA SYSTEM 8291")
    assert "confidence" in check("REF 88213 AQUA")


@pytest.mark.parametrize("memo", ["8291", "ref:8291", "INV 8291 part payment"])
def test_the_common_real_world_shapes_are_recognised(memo: str):
    assert match_memo(memo, "INV-8291").confidence >= MEMO_MATCH_THRESHOLD


def test_a_guessed_match_cannot_carry_a_claim_to_verified():
    """The score existed so a weak match would not be treated as a fact, and it was then
    routed into the one set verification policy accepts -- so a claim could verify on four
    digits appearing somewhere in a memo, with nobody ever seeing it."""
    from impactgraph.domain import ReconciliationStatus
    from impactgraph.verification import VerificationContext

    service = ReconciliationService()
    guessed = service.reconcile_invoice(
        {
            "invoiceNumber": "INV-8291",
            "vendor": "Aqua Systems Ltd.",
            "amountMinor": 420000,
            "currency": "USD",
            "equipment": "AquaPure X200",
            "quantity": 2,
        },
        {
            "id": "ftx-9182",
            "payee": "Aqua Systems Ltd.",
            "amountMinor": 420000,
            "currency": "USD",
            "memo": "CARD PAYMENT TO AQUA SYSTEM 8291",
        },
        {
            "id": "delivery-1",
            "financialTransactionId": "ftx-9182",
            "item": "AquaPure X200",
            "quantity": 2,
            "projectId": "project-water-12",
        },
        completion_report_present=True,
        photos_have_gps=[True],
    )
    assert guessed["status"] == ReconciliationStatus.NEEDS_CONFIRMATION
    # And that status is not one the policy treats as a passing reconciliation.
    assert ReconciliationStatus.NEEDS_CONFIRMATION not in {"MATCHED", "PARTIAL_MATCH"}
    assert "VerificationContext" in str(VerificationContext)


def test_an_exact_reference_still_reconciles_without_anybody_confirming_it():
    """The point is to stop a guess counting, not to make every payment need a person."""
    from impactgraph.domain import ReconciliationStatus

    exact = ReconciliationService().reconcile_invoice(
        {
            "invoiceNumber": "INV-8291",
            "vendor": "Aqua Systems Ltd.",
            "amountMinor": 420000,
            "currency": "USD",
            "equipment": "AquaPure X200",
            "quantity": 2,
        },
        {
            "id": "ftx-9182",
            "payee": "Aqua Systems Ltd.",
            "amountMinor": 420000,
            "currency": "USD",
            "memo": "Payment for INV-8291",
        },
        {
            "id": "delivery-1",
            "financialTransactionId": "ftx-9182",
            "item": "AquaPure X200",
            "quantity": 2,
            "projectId": "project-water-12",
        },
        completion_report_present=True,
        photos_have_gps=[True],
    )
    assert exact["status"] == ReconciliationStatus.MATCHED
