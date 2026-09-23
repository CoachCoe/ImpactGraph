"""What a person still has to check after a model has read a document.

These need no key and no network. The provider is exercised through its own pure parts
and a stubbed sampling client, because CI has neither, and because the decisions worth
testing here are about what is done with an extraction rather than how it was obtained.
"""

from __future__ import annotations

import pytest

from impactgraph.extraction import (
    RECONCILIATION_KEYS,
    REVIEW_THRESHOLD,
    _first_json_object,
    review_required_fields,
)

COMPLETE = {
    "documentType": "invoice",
    "invoiceNumber": "MBS-4417",
    "vendor": "Meridian Borehole Services",
    "amountMinor": 18650000,
    "currency": "KES",
    "date": "2026-09-03",
    "equipment": "HandPump Mk3",
    "quantity": 3,
    "projectReference": "Water Project #14",
}


def confident(**overrides) -> dict:
    extraction = dict(COMPLETE)
    confidence = {field: 0.99 for field in COMPLETE}
    confidence.update(overrides)
    extraction["selfReportedConfidence"] = confidence
    return extraction


def test_the_reconciliation_keys_are_always_confirmed_by_a_person():
    """Whatever the model says about its own certainty.

    A misread digit in an invoice number turns a matched payment into an unmatched one,
    and the model has been observed doing exactly that: a low-quality image of INV-8291
    read as INV-6291, with every other field correct.
    """
    flagged = review_required_fields(confident())
    assert set(RECONCILIATION_KEYS) <= set(flagged)


def test_a_field_the_model_is_unsure_of_is_confirmed_too():
    flagged = review_required_fields(confident(vendor=REVIEW_THRESHOLD - 0.2))
    assert "vendor" in flagged


def test_a_field_the_model_is_sure_of_is_not_laboured():
    """Everything needing confirmation is the same as nothing needing it: an operator
    asked to check nine fields every time will stop checking any of them."""
    flagged = review_required_fields(confident())
    assert "vendor" not in flagged
    assert "equipment" not in flagged


def test_an_extraction_with_no_stated_confidence_is_confirmed_in_full():
    """The mock reports none, and neither would a provider that stopped reporting it.
    Absent certainty is not high certainty."""
    assert set(review_required_fields(COMPLETE)) == set(COMPLETE)


def test_a_field_the_document_did_not_contain_is_confirmed():
    missing = confident()
    missing["projectReference"] = None
    assert "projectReference" in review_required_fields(missing)


def test_the_answer_is_read_out_of_a_reply_that_reasons_first():
    """The model thinks aloud before answering, so the reply is not JSON even when the
    answer inside it is."""
    reply = (
        "<|content_thinking|>The total reads 4,200.00 so amountMinor is 420000."
        '<|content_final|>{"invoiceNumber": "INV-8291", "amountMinor": 420000}'
    )
    assert _first_json_object(reply) == {"invoiceNumber": "INV-8291", "amountMinor": 420000}


def test_a_reply_with_no_answer_in_it_is_an_error_not_an_empty_extraction():
    """An empty extraction would register a document nobody read."""
    with pytest.raises(ValueError, match="no JSON object"):
        _first_json_object("<|content_thinking|>I am unable to read this document.")


def test_a_pdf_is_refused_rather_than_read_as_text():
    """A PDF decoded as text is mojibake, and a model handed mojibake returns confident
    nonsense. Refusing is the honest answer until pages are rendered to images."""
    from impactgraph.extraction import TinkerEvidenceAnalysisProvider

    provider = TinkerEvidenceAnalysisProvider(model="thinkingmachines/Inkling-Small")
    with (
        pytest.raises(ValueError, match="cannot be read yet"),
        provider._document(b"%PDF-1.7 ...", "application/pdf"),
    ):
        pass


def test_the_mock_stays_the_default_so_ci_needs_no_key():
    from impactgraph.config import Settings

    assert Settings().ai_provider == "mock"


def test_selecting_tinker_without_a_key_is_refused_at_startup(monkeypatch):
    """Rather than in front of an operator holding a document they already waited to hash."""
    from impactgraph.config import Settings

    monkeypatch.setenv("AI_PROVIDER", "tinker")
    monkeypatch.delenv("TINKER_API_KEY", raising=False)
    with pytest.raises(ValueError, match="TINKER_API_KEY"):
        Settings.from_env()


def test_an_unknown_provider_is_refused_rather_than_quietly_mocked():
    import os

    from impactgraph.config import Settings

    original = os.environ.get("AI_PROVIDER")
    os.environ["AI_PROVIDER"] = "something-else"
    try:
        with pytest.raises(ValueError, match="mock or tinker"):
            Settings.from_env()
    finally:
        if original is None:
            del os.environ["AI_PROVIDER"]
        else:
            os.environ["AI_PROVIDER"] = original
