"""Reading a document with a model instead of a lookup table.

The mock this replaces returned a fixed dictionary for anything containing the string
`INV-8291` and raised on everything else, so a second organisation's first upload failed.

Two things about this provider are deliberately modest.

The confidence figures are **self-reported by the model**, not measured. A model asked how
sure it is produces a plausible number, not a calibrated one, and the field names say so
rather than presenting a guess as a measurement.

The invoice number is always sent for operator confirmation regardless of what confidence
comes back. It is the field reconciliation matches a payment against, and a single
misread digit turns a matched payment into an unmatched one. Observed: a low-quality
image of INV-8291 was read as INV-6291, with every other field correct.
"""

from __future__ import annotations

import json
import re
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

_SCHEMA_FIELDS = (
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
    the answer within it is.
    """
    body = text.split("<|content_final|>")[-1]
    match = re.search(r"\{.*\}", body, re.DOTALL)
    if match is None:
        raise ValueError("The model returned no JSON object")
    return json.loads(match.group(0))


def _conform(raw: dict[str, Any]) -> dict[str, Any]:
    """Reduce the reply to the shape the review boundary accepts.

    A model asked for nine keys sometimes returns ten, and an unexpected key is refused at
    review -- in front of an operator who can do nothing about it. A key it omitted becomes
    an explicit None, which `review_required_fields` puts in front of that operator instead.
    """
    stated = raw.get("selfReportedConfidence")
    stated = stated if isinstance(stated, dict) else {}
    return {
        **{field: raw.get(field) for field in _SCHEMA_FIELDS},
        "selfReportedConfidence": {
            field: float(value)
            for field, value in stated.items()
            if field in _SCHEMA_FIELDS and isinstance(value, int | float) and 0 <= value <= 1
        },
    }


def review_required_fields(extraction: dict[str, Any]) -> list[str]:
    """Which fields a person has to confirm before this is registered."""
    confidence = extraction.get("selfReportedConfidence") or {}
    flagged = set(RECONCILIATION_KEYS)
    for field in _SCHEMA_FIELDS:
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
                    max_tokens=1500,
                    # The same bytes must read the same way every time, or re-analysing a
                    # file produces a different document from the one already reviewed.
                    temperature=0.0,
                    stop_tokens=self._renderer.stop(),
                ),
            ).result()

        text = self._tokenizer.decode(list(response.sequences[0].tokens))
        extraction = _conform(_first_json_object(text))
        # Never the extracted content: the document is the operator's, and an extraction
        # carries whatever the document carries.
        log.info(
            "extraction.completed",
            model=self.model,
            fields=len([k for k in _SCHEMA_FIELDS if extraction.get(k) is not None]),
            review_required=len(review_required_fields(extraction)),
            seconds=round((datetime.now(UTC) - started).total_seconds(), 2),
        )
        return AnalysisResult(
            extraction=extraction,
            provider=self.name,
            model=self.model,
            processed_at=started.isoformat(),
            raw_response={
                "reply": text,
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
        from tml_renderers.chat import ImageFormat, ImagePointer, Text

        formats = {"image/png": ImageFormat.Png, "image/jpeg": ImageFormat.Jpeg}
        if mime_type == "text/plain":
            yield Text(text=content.decode("utf-8", errors="replace")[:20000])
            return
        if mime_type not in formats:
            # A PDF decoded as text is mojibake, and a model given mojibake returns
            # confident nonsense. Refusing is the honest answer until pages are rendered.
            raise ValueError(
                f"{mime_type} cannot be read yet; upload a PNG or JPEG of the document"
            )

        from PIL import Image

        directory = tempfile.mkdtemp(prefix="impactgraph-evidence-")
        path = Path(directory) / "document.img"
        try:
            path.write_bytes(content)
            with Image.open(path) as opened:
                width, height = opened.size
            yield ImagePointer(
                location=str(path), format=formats[mime_type], width=width, height=height
            )
        finally:
            shutil.rmtree(directory, ignore_errors=True)
