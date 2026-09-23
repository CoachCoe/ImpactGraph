"""Reading a document with a model instead of a lookup table.

The mock this replaces returned a fixed dictionary for anything containing the string
`INV-8291` and raised on everything else, so a second organisation's first upload failed.

Two things about this provider are deliberately modest.

The confidence figures are **self-reported by the model**, not measured. A model asked how
sure it is produces a plausible number, not a calibrated one, and the field names say so
rather than presenting a guess as a measurement.

The fields reconciliation matches a payment against are always sent for operator
confirmation regardless of what confidence comes back, and `/evidence/{id}/review` refuses
a submission that does not cover them -- so the confirmation is a rule the API holds rather
than a checkbox in a browser. A single misread digit turns a matched payment into an
unmatched one. Observed: a low-quality image of INV-8291 was read as INV-6291, with every
other field correct.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .evidence import AnalysisResult
from .observability import logger

log = logger("impactgraph.extraction")

#: Fields the reconciliation engine resolves a payment and a delivery against. These are
#: the ones a mistake is expensive in, so they are confirmed by a person whatever the
#: model says about its own certainty.
RECONCILIATION_KEYS = ("invoiceNumber", "amountMinor", "currency")

#: Below this, a field is put in front of the operator. Self-reported, so the threshold is
#: a filter on the model's own optimism rather than a statistical bound.
REVIEW_THRESHOLD = 0.9

#: Enough for the schema and the reasoning ahead of it. Reaching it means the reply was cut
#: off rather than finished, which `analyze` reports as such rather than as bad JSON.
_MAX_TOKENS = 1500

SCHEMA_FIELDS = (
    "documentType",
    "invoiceNumber",
    "vendor",
    "amountMinor",
    "currency",
    "date",
    "equipment",
    "quantity",
    "projectReference",
)

_INSTRUCTION = (
    "Read the attached document and return ONLY a JSON object, with no prose before or "
    "after it, containing exactly these keys:\n"
    '  documentType (string, e.g. "invoice"), invoiceNumber (string), vendor (string), '
    "amountMinor (integer, the total in minor units -- 4,200.00 is 420000), "
    "currency (ISO 4217, e.g. USD), date (ISO 8601 yyyy-mm-dd), equipment (string), "
    "quantity (integer), projectReference (string), and a nested object "
    '"selfReportedConfidence" giving a number between 0 and 1 for each of the above keys.\n'
    "Report a low confidence where the document is unclear rather than guessing. "
    "Use null for a field the document does not contain."
)


def _first_json_object(text: str) -> dict[str, Any]:
    """Pull the object out of a reply that may carry a reasoning channel before it.

    The model emits its thinking before its answer, so the response is not JSON even when
    the answer within it is. Decoding from each opening brace in turn, rather than matching
    the span between the first and the last, because a brace anywhere else in the reply --
    in the reasoning, or in a remark after the answer -- makes that span unparseable.
    """
    body = text.split("<|content_final|>")[-1]
    decoder = json.JSONDecoder()
    for start in (index for index, char in enumerate(body) if char == "{"):
        try:
            value, _ = decoder.raw_decode(body, start)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("The model returned no JSON object")


_INTEGER_FIELDS = ("amountMinor", "quantity")


def _as_integer(value: Any) -> int | None:
    """An integer, or None for anything that would have to be guessed at.

    The prompt asks for minor units, and a model that answers "4,200.00" has not obeyed it.
    Reading that as 420000 is a guess about where the decimal point belongs, on the field a
    payment is reconciled against; None puts it in front of the operator instead, which is
    the same answer this module gives for every other value it cannot stand behind.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def _conform(raw: dict[str, Any]) -> dict[str, Any]:
    """Reduce the reply to the shape the review boundary accepts.

    A model asked for nine keys sometimes returns ten, and an unexpected key is refused at
    review -- in front of an operator who can do nothing about it. A key it omitted becomes
    an explicit None, which `review_required_fields` puts in front of that operator instead.
    """
    stated = raw.get("selfReportedConfidence")
    stated = stated if isinstance(stated, dict) else {}
    values = {field: raw.get(field) for field in SCHEMA_FIELDS}
    for field in _INTEGER_FIELDS:
        values[field] = _as_integer(values[field])
    return {
        **values,
        "selfReportedConfidence": {
            field: float(value)
            for field, value in stated.items()
            if field in SCHEMA_FIELDS and isinstance(value, int | float) and 0 <= value <= 1
        },
    }


def review_required_fields(extraction: dict[str, Any]) -> list[str]:
    """Which fields a person has to confirm before this is registered."""
    confidence = extraction.get("selfReportedConfidence") or {}
    flagged = set(RECONCILIATION_KEYS)
    for field in SCHEMA_FIELDS:
        value = confidence.get(field)
        if extraction.get(field) is None or not isinstance(value, int | float) or value < REVIEW_THRESHOLD:
            flagged.add(field)
    return sorted(flagged)


class TinkerEvidenceAnalysisProvider:
    """Structured extraction through a Thinking Machines sampling client.

    The renderer, the tokenizer and the client are built once and reused: each carries a
    tokenizer load, and rebuilding them per upload would put that on the operator's wait.
    """

    name = "tinker"

    def __init__(self, *, model: str, api_key: str | None = None) -> None:
        self.model = model
        self._api_key = api_key
        self._client = None
        self._renderer = None
        self._tokenizer = None

    def _ensure(self) -> None:
        if self._client is not None:
            return
        try:
            import tinker
            from tml_renderers.tokenizers import o200k_base_chat
            from tml_renderers.v0 import Renderer
        except ImportError as exc:  # pragma: no cover - a deployment fault, not a path
            raise RuntimeError(
                "AI_PROVIDER=tinker needs the tinker extra: pip install '.[tinker]'"
            ) from exc

        self._tokenizer = o200k_base_chat()
        self._renderer = Renderer(self._tokenizer)
        self._client = tinker.ServiceClient().create_sampling_client(base_model=self.model)

    def analyze(self, content: bytes, mime_type: str) -> AnalysisResult:
        self._ensure()
        import tinker.types as tinker_types
        from tml_renderers.chat import Author, AuthorKind, Message, Text
        from tml_renderers.tinker import token_spans_to_tinker_model_input

        started = datetime.now(UTC)
        user = Author(kind=AuthorKind.User)
        with self._document(content, mime_type) as document:
            messages = [
                Message(content=document, author=user),
                Message(content=Text(text=_INSTRUCTION), author=user),
            ]
            spans, _ = self._renderer.render_for_completion(messages)
            response = self._client.sample(
                prompt=token_spans_to_tinker_model_input(spans),
                num_samples=1,
                sampling_params=tinker_types.SamplingParams(
                    max_tokens=_MAX_TOKENS,
                    # The same bytes must read the same way every time, or re-analysing a
                    # file produces a different document from the one already reviewed.
                    temperature=0.0,
                    stop_tokens=self._renderer.stop(),
                ),
            ).result()

        tokens = list(response.sequences[0].tokens)
        # A reply cut off at the cap arrives as unparseable JSON, which is indistinguishable
        # from a model that answered badly unless the cap is named as the cause.
        if len(tokens) >= _MAX_TOKENS:
            raise ValueError(
                f"The model's reply was cut off at {_MAX_TOKENS} tokens before it finished"
            )
        text = self._tokenizer.decode(tokens)
        extraction = _conform(_first_json_object(text))
        # Counts only. The reply carries the model's reading of the document, and this line
        # is the one place it would otherwise be written down.
        log.info(
            "extraction.completed",
            model=self.model,
            fields=len([k for k in SCHEMA_FIELDS if extraction.get(k) is not None]),
            review_required=len(review_required_fields(extraction)),
            seconds=round((datetime.now(UTC) - started).total_seconds(), 2),
        )
        return AnalysisResult(
            extraction=extraction,
            provider=self.name,
            model=self.model,
            processed_at=started.isoformat(),
            # Diagnostics, not content. The reply itself is the document restated, and
            # this dictionary is persisted in evidence metadata and served over the API,
            # where it would be a second copy of the document outside the visibility rules
            # that govern the first.
            raw_response={
                "replyTokens": len(tokens),
                "confidenceBasis": "self-reported by the model, not measured",
            },
        )

    @staticmethod
    @contextmanager
    def _document(content: bytes, mime_type: str):
        """Hand the model the document, as an image where it is one.

        The renderer takes a location and reads the bytes itself, client side, so an image
        goes to a temporary file that is removed as soon as the request has been built.
        Nothing is fetched by the service: a restricted document never leaves this process
        except as the bytes deliberately sent.
        """
        # Decided before the renderer is imported, so refusing a format needs nothing
        # installed -- and so the test that pins the refusal proves it without the extra.
        if mime_type not in {"text/plain", "image/png", "image/jpeg"}:
            # A PDF decoded as text is mojibake, and a model given mojibake returns
            # confident nonsense. Refusing is the honest answer until pages are rendered.
            raise ValueError(
                f"{mime_type} cannot be read yet; upload a PNG or JPEG of the document"
            )

        from tml_renderers.chat import ImageFormat, ImagePointer, Text

        if mime_type == "text/plain":
            yield Text(text=content.decode("utf-8", errors="replace")[:20000])
            return

        from PIL import Image

        # 0700 from mkdtemp, and the file lives for the whole round trip: the renderer
        # hands the service a location and reads the bytes from it as the request is sent.
        directory = tempfile.mkdtemp(prefix="impactgraph-evidence-")
        path = Path(directory) / "document.img"
        try:
            path.write_bytes(content)
            with Image.open(path) as opened:
                width, height = opened.size
            yield ImagePointer(
                location=str(path),
                format=ImageFormat.Png if mime_type == "image/png" else ImageFormat.Jpeg,
                width=width,
                height=height,
            )
        finally:
            try:
                shutil.rmtree(directory)
            except OSError as exc:
                # Not ignored: this directory holds the operator's document, and a copy
                # left behind is the kind of thing nobody discovers by looking.
                log.error(
                    "extraction.tempdir_not_removed",
                    directory=directory,
                    error=exc.__class__.__name__,
                )
