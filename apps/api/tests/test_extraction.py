"""What a person still has to check after a model has read a document.

These need no key and no network. The provider is exercised through its own pure parts
and a stubbed sampling client, because CI has neither, and because the decisions worth
testing here are about what is done with an extraction rather than how it was obtained.
"""

from __future__ import annotations

import json

import pytest

from impactgraph.extraction import (
    _INSTRUCTION,
    _MAX_TOKENS,
    RECONCILIATION_KEYS,
    REVIEW_THRESHOLD,
    SCHEMA_FIELDS,
    _conform,
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


def test_a_providers_extraction_is_accepted_by_the_boundary_that_registers_it():
    """The shape produced and the shape accepted were allowed to drift apart once.

    The review model forbids unknown keys, so a provider emitting per-field confidence
    while the model expected a single `confidence` float made every real extraction fail
    validation at the one point an operator submits it -- after the wait, after the review.
    """
    from impactgraph.evidence import MOCK_INVOICE_EXTRACTION
    from impactgraph.main import InvoiceExtraction

    InvoiceExtraction(**MOCK_INVOICE_EXTRACTION)
    InvoiceExtraction(**_conform(dict(COMPLETE, selfReportedConfidence={"vendor": 0.98})))


def test_the_prompt_and_the_boundary_describe_the_same_document():
    """A field asked for but not accepted is rejected at review; a field accepted but
    never asked for is always absent and so always flagged. Either way an operator pays."""
    from impactgraph.main import InvoiceExtraction

    accepted = set(InvoiceExtraction.model_fields) - {"selfReportedConfidence"}
    assert accepted == set(SCHEMA_FIELDS)
    assert all(field in _INSTRUCTION for field in SCHEMA_FIELDS)


def test_a_key_the_model_invented_is_dropped_rather_than_refused_at_review():
    conformed = _conform({**COMPLETE, "bankAccount": "GB29 NWBK 6016 1331 9268 19"})
    assert "bankAccount" not in conformed


def test_a_key_the_model_omitted_becomes_a_field_to_confirm():
    conformed = _conform({"invoiceNumber": "MBS-4417"})
    assert conformed["vendor"] is None
    assert set(review_required_fields(conformed)) == set(SCHEMA_FIELDS)


def test_a_confidence_outside_zero_to_one_is_not_believed():
    """It is a number the model chose, not a measurement, and 1.4 means nothing."""
    conformed = _conform({**COMPLETE, "selfReportedConfidence": {"vendor": 1.4, "date": 0.95}})
    assert conformed["selfReportedConfidence"] == {"date": 0.95}
    assert "vendor" in review_required_fields(conformed)


def test_a_brace_anywhere_else_in_the_reply_does_not_lose_the_answer():
    """The answer used to be located as the span from the first brace to the last, so a
    brace in the reasoning, or a remark after the object, made a good reply unparseable."""
    assert _first_json_object('I see {a total}. {"invoiceNumber": "X"}') == {"invoiceNumber": "X"}
    assert _first_json_object('<|content_final|>{"invoiceNumber": "X"} ({sic})') == {
        "invoiceNumber": "X"
    }


def test_the_nested_confidence_object_survives_being_found():
    reply = '<|content_final|>{"invoiceNumber":"X","selfReportedConfidence":{"vendor":0.9}}'
    assert _first_json_object(reply)["selfReportedConfidence"] == {"vendor": 0.9}


def test_a_total_the_model_formatted_is_not_guessed_at():
    """The prompt asks for minor units. "4,200.00" is the model declining to obey it, and
    reading that as 420000 is a guess about the decimal point on the field a payment is
    reconciled against. None sends it to the operator, which is the honest answer."""
    for formatted in ("4,200.00", "USD 4200.00", 4200.5):
        conformed = _conform({"amountMinor": formatted})
        assert conformed["amountMinor"] is None
        assert "amountMinor" in review_required_fields(conformed)


def test_an_integer_the_model_quoted_is_still_an_integer():
    assert _conform({"amountMinor": "420000", "quantity": 3.0}) | {} == _conform(
        {"amountMinor": 420000, "quantity": 3}
    )


def test_refusing_a_format_needs_nothing_from_the_optional_extra(monkeypatch):
    """tinker is an optional extra, so CI runs without it. Importing the renderer before
    deciding whether the format is readable turned this refusal into an ImportError there,
    and the test that pins it passed only on a machine that happened to have the extra."""
    import sys

    from impactgraph.extraction import TinkerEvidenceAnalysisProvider

    for module in ("tml_renderers", "tml_renderers.chat", "PIL", "PIL.Image"):
        monkeypatch.setitem(sys.modules, module, None)
    provider = TinkerEvidenceAnalysisProvider(model="thinkingmachines/Inkling-Small")
    with (
        pytest.raises(ValueError, match="cannot be read yet"),
        provider._document(b"%PDF-1.7 ...", "application/pdf"),
    ):
        pass


@pytest.fixture
def offline_provider(monkeypatch):
    """A provider whose sampling client returns a scripted reply.

    The renderer and the service are stubbed rather than reached: what is under test is
    what `analyze` does with a reply, which is the part CI can hold to account.
    """
    import sys
    from types import SimpleNamespace

    from impactgraph.extraction import TinkerEvidenceAnalysisProvider

    passthrough = SimpleNamespace(
        Author=lambda **kw: kw,
        AuthorKind=SimpleNamespace(User="user"),
        Message=lambda **kw: kw,
        Text=lambda text: text,
        ImageFormat=SimpleNamespace(Png="png", Jpeg="jpeg"),
        ImagePointer=lambda **kw: kw,
    )
    # The parent packages are stubbed too, or importing a submodule executes the real
    # __init__ -- which is the torch load this suite is meant to run without. `import a.b`
    # then reads b off the parent, so each child is hung on it as an attribute as well.
    types = SimpleNamespace(SamplingParams=lambda **kw: kw)
    renderers = SimpleNamespace(token_spans_to_tinker_model_input=list)
    for name, module in {
        "tinker": SimpleNamespace(types=types),
        "tinker.types": types,
        "tml_renderers": SimpleNamespace(chat=passthrough, tinker=renderers),
        "tml_renderers.chat": passthrough,
        "tml_renderers.tinker": renderers,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    def build(tokens: list[int], reply: str) -> TinkerEvidenceAnalysisProvider:
        provider = TinkerEvidenceAnalysisProvider(model="thinkingmachines/Inkling-Small")
        response = SimpleNamespace(sequences=[SimpleNamespace(tokens=tokens)])
        provider._client = SimpleNamespace(
            sample=lambda **kw: SimpleNamespace(result=lambda: response)
        )
        provider._renderer = SimpleNamespace(
            render_for_completion=lambda messages: ([], None), stop=list
        )
        provider._tokenizer = SimpleNamespace(decode=lambda tokens: reply)
        return provider

    return build


REPLY = (
    "<|content_thinking|>The total reads 4,200.00, so amountMinor is 420000."
    '<|content_final|>{"documentType":"invoice","invoiceNumber":"INV-8291","vendor":'
    '"Aqua Systems Ltd.","amountMinor":420000,"currency":"USD","date":"2026-08-17",'
    '"equipment":"AquaPure X200","quantity":2,"projectReference":"Water Project #12",'
    '"selfReportedConfidence":{"vendor":0.98}}'
)


def test_nothing_the_model_read_is_kept_where_it_would_outlive_the_request(offline_provider):
    """`raw_response` is written into evidence metadata and served over the API. The reply
    restates the document, so keeping it there was a second copy of a restricted document
    governed by none of the rules that govern the first."""
    result = offline_provider([1, 2, 3], REPLY).analyze(b"Invoice INV-8291", "text/plain")

    assert result.extraction["invoiceNumber"] == "INV-8291"
    assert "INV-8291" not in json.dumps(result.raw_response)
    assert "Aqua Systems" not in json.dumps(result.raw_response)
    assert result.raw_response["replyTokens"] == 3


def test_a_reply_cut_off_at_the_cap_says_so_rather_than_blaming_the_json(offline_provider):
    """Both arrive as an unparseable reply, and an operator retrying a document that will
    never fit needs to be told which one happened."""
    truncated = offline_provider([0] * _MAX_TOKENS, REPLY[:120])
    with pytest.raises(ValueError, match="cut off"):
        truncated.analyze(b"Invoice INV-8291", "text/plain")


def test_the_document_is_confirmed_field_by_field_after_a_real_read(offline_provider):
    result = offline_provider([1], REPLY).analyze(b"Invoice INV-8291", "text/plain")
    flagged = review_required_fields(result.extraction)
    assert set(RECONCILIATION_KEYS) <= set(flagged)
    assert "vendor" not in flagged


def test_two_uploads_arriving_together_build_one_renderer(monkeypatch):
    """A sync FastAPI endpoint runs in a threadpool, so a cold process really does get two
    analyses at once. Both used to enter the builder, and each rebound the tokenizer and
    renderer the other was already decoding with -- returning a document carrying fields
    from someone else's reply, with a confidence figure in the invoice number.
    """
    import sys
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace

    from impactgraph.extraction import TinkerEvidenceAnalysisProvider

    builds = []

    def slow_tokenizer():
        # Wide enough that every thread is inside the builder before the first one leaves.
        time.sleep(0.05)
        builds.append(threading.current_thread().name)
        return SimpleNamespace(decode=lambda tokens: "")

    for name, module in {
        "tinker": SimpleNamespace(
            ServiceClient=lambda: SimpleNamespace(
                create_sampling_client=lambda base_model: SimpleNamespace()
            )
        ),
        "tml_renderers": SimpleNamespace(),
        "tml_renderers.tokenizers": SimpleNamespace(o200k_base_chat=slow_tokenizer),
        "tml_renderers.v0": SimpleNamespace(Renderer=lambda tokenizer: SimpleNamespace()),
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    provider = TinkerEvidenceAnalysisProvider(model="thinkingmachines/Inkling-Small")
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: provider._ensure(), range(8)))

    assert len(builds) == 1, f"built {len(builds)} times; the second rebinds the first's state"


def test_the_readiness_flag_is_never_set_before_what_it_stands_for():
    """`_ensure` returns early on `_client` without taking the lock, so a thread that sees
    it set must find the tokenizer and renderer already there."""
    import inspect

    from impactgraph.extraction import TinkerEvidenceAnalysisProvider

    body = inspect.getsource(TinkerEvidenceAnalysisProvider._build)
    assert body.index("self._tokenizer") < body.index("self._client = client")
