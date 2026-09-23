"""Finding the things that look wrong, and leaving the deciding to a person.

Deterministic detectors only, and deliberately so. Statistical scoring is worth having
once there is enough history to estimate a baseline from; run on the volume this platform
has today it would mostly report noise, and a queue of noise teaches reviewers to clear
it without reading -- which is worse than having no queue, because it also costs their
attention.

Nothing in this module changes anything. Every detector returns findings for a person to
look at. A false accusation of fraud against an operating organisation is a serious harm,
and one this system made on its own would be a harm it caused rather than surfaced.

Every finding carries the identifiers it rests on, because a finding a reviewer cannot
check is one they can only believe or ignore.
"""
from __future__ import annotations

import io
import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

#: Out of 64 bits. Six tolerates re-encoding, a resize and mild recompression; it does not
#: tolerate a different photograph. Raising it starts matching any two pictures of a
#: similar scene, which in this domain is every photograph of a food distribution.
NEAR_DUPLICATE_DISTANCE = 6

#: A programme with three payments does not have a concentration problem, it has three
#: payments. Below this the share is an artefact of the count.
CONCENTRATION_MINIMUM_PAYMENTS = 5

#: Share of a programme's spend, in one currency, going to a single payee.
CONCENTRATION_SHARE = 0.6


@dataclass(frozen=True)
class Finding:
    kind: str
    explanation: str
    subjects: dict = field(default_factory=dict)


@dataclass(frozen=True)
class InvoiceFacts:
    evidence_ref: str
    program_ref: str
    vendor: str
    invoice_number: str
    total_minor: int
    currency: str
    issued_on: str


@dataclass(frozen=True)
class ImageFacts:
    evidence_ref: str
    project_ref: str
    content_hash: str
    perceptual_hash: str


@dataclass(frozen=True)
class PaymentFacts:
    transaction_ref: str
    program_ref: str
    payee_ref: str
    payee_name: str
    amount_minor: int
    currency: str


class PerceptualHashUnsupported(RuntimeError):
    """The bytes are not an image this can hash, so there is nothing to compare."""


def perceptual_hash(data: bytes) -> str:
    """A 64-bit difference hash, as 16 hex characters.

    Distinct from the content hash and not a replacement for it. The content hash proves
    a file's bytes are unchanged; this survives a re-encode or a crop and so can find the
    same photograph submitted twice under two identities. Neither answers the other's
    question.
    """
    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
    except ImportError as exc:  # pragma: no cover - pillow is a hard dependency
        raise PerceptualHashUnsupported("pillow is not installed") from exc

    try:
        with Image.open(io.BytesIO(data)) as image:
            # Rotating a photograph is the cheapest way to defeat a hash that ignores
            # orientation, and a camera sets this tag without anybody choosing to.
            upright = ImageOps.exif_transpose(image)
            small = upright.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise PerceptualHashUnsupported("not a decodable image") from exc

    # `tobytes` on an 8-bit greyscale image is exactly the 72 pixel values, and
    # unlike `getdata` it has not moved between Pillow versions.
    pixels = small.tobytes()
    bits = 0
    for row in range(8):
        for column in range(8):
            left = pixels[row * 9 + column]
            right = pixels[row * 9 + column + 1]
            bits = (bits << 1) | int(left > right)
    return f"{bits:016x}"


def hamming(left: str, right: str) -> int:
    """How many of the 64 bits differ."""
    if len(left) != len(right):
        raise ValueError("hashes of different lengths are not comparable")
    return (int(left, 16) ^ int(right, 16)).bit_count()


def _normalise(value: str) -> str:
    """Case and punctuation only.

    Deliberately not fuzzy. "Acme Ltd" and "Acme Limited" will not match, and that is the
    trade accepted here: a reviewer can act on "these two invoices carry the same number
    from the same vendor" and cannot act on "these two names are 0.8 similar".
    """
    return re.sub(r"[^A-Za-z0-9]", "", value).upper()


def duplicate_invoices(invoices: Sequence[InvoiceFacts]) -> list[Finding]:
    """The same invoice supporting more than one delivery.

    Billing one invoice to two funders is the version of this that costs money, so a
    match that spans programmes is reported differently from one inside a single
    programme, which is more often a duplicate upload.
    """
    findings: list[Finding] = []

    by_number: dict[tuple[str, str], list[InvoiceFacts]] = defaultdict(list)
    for invoice in invoices:
        if not _normalise(invoice.invoice_number) or not _normalise(invoice.vendor):
            continue
        by_number[(_normalise(invoice.vendor), _normalise(invoice.invoice_number))].append(invoice)

    for (vendor, number), group in sorted(by_number.items()):
        if len({item.evidence_ref for item in group}) < 2:
            continue
        programs = sorted({item.program_ref for item in group})
        refs = sorted({item.evidence_ref for item in group})
        if len(programs) > 1:
            explanation = (
                f"Invoice {number} from {vendor} supports deliveries in "
                f"{len(programs)} programmes ({', '.join(programs)}). One invoice billed "
                f"to more than one funder would be paid for twice."
            )
        else:
            explanation = (
                f"Invoice {number} from {vendor} is registered {len(refs)} times in "
                f"{programs[0]}. Either it was uploaded twice or it was counted twice."
            )
        findings.append(
            Finding(
                kind="DUPLICATE_INVOICE",
                explanation=explanation,
                subjects={"evidence": refs, "programs": programs, "invoiceNumber": number},
            )
        )

    by_amount: dict[tuple[str, int, str, str], list[InvoiceFacts]] = defaultdict(list)
    for invoice in invoices:
        if not _normalise(invoice.vendor):
            continue
        key = (_normalise(invoice.vendor), invoice.total_minor, invoice.currency, invoice.issued_on)
        by_amount[key].append(invoice)

    for (vendor, amount, currency, issued_on), group in sorted(by_amount.items()):
        numbers = {_normalise(item.invoice_number) for item in group}
        if len(numbers) < 2 or len({item.evidence_ref for item in group}) < 2:
            continue
        refs = sorted({item.evidence_ref for item in group})
        findings.append(
            Finding(
                kind="DUPLICATE_INVOICE",
                explanation=(
                    f"{vendor} issued {len(numbers)} invoices for the same amount "
                    f"({amount} {currency} minor units) on the same day ({issued_on}), "
                    f"under different numbers. That is either a split order or the same "
                    f"charge submitted twice."
                ),
                subjects={
                    "evidence": refs,
                    "invoiceNumbers": sorted(numbers),
                    "amountMinor": amount,
                    "currency": currency,
                    "issuedOn": issued_on,
                },
            )
        )

    return findings


def reused_images(images: Sequence[ImageFacts]) -> list[Finding]:
    """The same photograph standing in for two different deliveries.

    Byte-identical is reported separately from visually similar: the first is a fact, the
    second is a resemblance a person still has to look at. Two pictures from one delivery
    are expected to resemble each other, so only a match across projects is reported.
    """
    findings: list[Finding] = []
    hashable = [image for image in images if image.perceptual_hash]

    for index, left in enumerate(hashable):
        for right in hashable[index + 1 :]:
            if left.project_ref == right.project_ref:
                continue
            refs = sorted([left.evidence_ref, right.evidence_ref])
            projects = sorted([left.project_ref, right.project_ref])
            if left.content_hash == right.content_hash:
                findings.append(
                    Finding(
                        kind="REUSED_IMAGE",
                        explanation=(
                            f"The same file, byte for byte, is filed against "
                            f"{projects[0]} and {projects[1]}."
                        ),
                        subjects={"evidence": refs, "projects": projects, "distance": 0},
                    )
                )
                continue
            distance = hamming(left.perceptual_hash, right.perceptual_hash)
            if distance <= NEAR_DUPLICATE_DISTANCE:
                findings.append(
                    Finding(
                        kind="REUSED_IMAGE",
                        explanation=(
                            f"Two different files filed against {projects[0]} and "
                            f"{projects[1]} look like the same photograph "
                            f"({distance} of 64 bits differ). Re-encoding or cropping an "
                            f"image changes its bytes and not its appearance."
                        ),
                        subjects={"evidence": refs, "projects": projects, "distance": distance},
                    )
                )
    return findings


def vendor_concentration(payments: Sequence[PaymentFacts]) -> list[Finding]:
    """Most of a programme's money going to one payee.

    Not wrong by itself -- a water programme buys from the one drilling contractor in the
    district -- which is why this reports the share and leaves the judgement alone.

    Grouped by currency because money in different currencies does not add up, and a
    total that pretends it does would misstate the share it is reporting.
    """
    findings: list[Finding] = []
    per_program: dict[tuple[str, str], list[PaymentFacts]] = defaultdict(list)
    for payment in payments:
        per_program[(payment.program_ref, payment.currency)].append(payment)

    for (program_ref, currency), group in sorted(per_program.items()):
        if len(group) < CONCENTRATION_MINIMUM_PAYMENTS:
            continue
        total = sum(payment.amount_minor for payment in group)
        if total <= 0:
            continue
        per_payee: dict[str, list[PaymentFacts]] = defaultdict(list)
        for payment in group:
            per_payee[payment.payee_ref].append(payment)
        if len(per_payee) < 2:
            continue
        for payee_ref, payee_payments in sorted(per_payee.items()):
            paid = sum(payment.amount_minor for payment in payee_payments)
            share = paid / total
            if share < CONCENTRATION_SHARE:
                continue
            name = payee_payments[0].payee_name
            findings.append(
                Finding(
                    kind="VENDOR_CONCENTRATION",
                    explanation=(
                        f"{name} received {share:.0%} of {program_ref}'s spend in "
                        f"{currency} ({paid} of {total} minor units across "
                        f"{len(payee_payments)} of {len(group)} payments). That can be "
                        f"ordinary for a specialised supplier; it is worth knowing which."
                    ),
                    subjects={
                        "program": program_ref,
                        "payee": payee_ref,
                        "currency": currency,
                        "share": round(share, 4),
                        "paidMinor": paid,
                        "totalMinor": total,
                        "transactions": sorted(p.transaction_ref for p in payee_payments),
                    },
                )
            )
    return findings


def perceptual_hash_for(mime_type: str, data: bytes) -> str | None:
    """The hash if these bytes are an image, and nothing if they are not.

    An upload must not fail because a photograph could not be hashed -- the evidence is
    the point and the hash is a check on it -- so an undecodable image yields no hash
    rather than an error. It then takes no part in near-duplicate detection, which is
    visible as an absent hash rather than as a silently missing comparison.
    """
    if not mime_type.lower().startswith("image/"):
        return None
    try:
        return perceptual_hash(data)
    except PerceptualHashUnsupported:
        return None
