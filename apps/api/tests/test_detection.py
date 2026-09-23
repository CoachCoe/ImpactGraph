"""What the detectors find, and -- more importantly -- what they refuse to find.

A detector that reports everything is the same as one that reports nothing, because a
reviewer learns in a week to clear the queue without reading it.
"""
from __future__ import annotations

import io
import math

import pytest
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from impactgraph.detection import (
    CONCENTRATION_MINIMUM_PAYMENTS,
    NEAR_DUPLICATE_DISTANCE,
    ImageFacts,
    InvoiceFacts,
    PaymentFacts,
    PerceptualHashUnsupported,
    duplicate_invoices,
    hamming,
    perceptual_hash,
    perceptual_hash_for,
    reused_images,
    vendor_concentration,
)


def _photo(seed: int, size: tuple[int, int] = (240, 180)) -> Image.Image:
    """A smooth gradient with a few blobs, blurred -- shaped like a photograph.

    Deliberately low frequency. A fixture built from per-pixel arithmetic carries detail
    at the sampling limit, which aliases when it is downscaled and makes the hash look
    unstable under resizing; a camera image does not contain that, so testing against one
    would be testing the fixture.
    """
    width, height = size
    image = Image.new("RGB", size)
    canvas = ImageDraw.Draw(image)
    for y in range(height):
        position = y / height
        canvas.line(
            [(0, y), (width, y)],
            fill=(
                int(40 + 150 * position),
                int(90 + 90 * math.sin(position * 2 + seed)),
                int(180 - 120 * position),
            ),
        )
    for index in range(4):
        centre_x = (seed * 37 + index * 61) % width
        centre_y = (seed * 53 + index * 43) % height
        radius = 18 + ((seed * 13 + index * 7) % 30)
        canvas.ellipse(
            [centre_x - radius, centre_y - radius, centre_x + radius, centre_y + radius],
            fill=(
                (seed * 91 + index * 60) % 256,
                (seed * 45 + index * 90) % 256,
                (seed * 160 + index * 30) % 256,
            ),
        )
    return image.filter(ImageFilter.GaussianBlur(1.2))


def _encoded(image: Image.Image, fmt: str = "JPEG", **options) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **options)
    return buffer.getvalue()


def test_re_encoding_an_image_does_not_change_what_it_looks_like():
    original = _photo(1)
    high = perceptual_hash(_encoded(original, quality=95))
    low = perceptual_hash(_encoded(original, quality=40))
    resized = perceptual_hash(_encoded(original.resize((120, 90)), quality=80))
    as_png = perceptual_hash(_encoded(original, fmt="PNG"))

    assert hamming(high, low) <= NEAR_DUPLICATE_DISTANCE
    assert hamming(high, resized) <= NEAR_DUPLICATE_DISTANCE
    assert hamming(high, as_png) <= NEAR_DUPLICATE_DISTANCE


def test_a_different_photograph_is_not_a_near_duplicate():
    assert hamming(
        perceptual_hash(_encoded(_photo(1))), perceptual_hash(_encoded(_photo(9)))
    ) > NEAR_DUPLICATE_DISTANCE


def test_the_content_hash_and_the_perceptual_hash_answer_different_questions():
    """Re-encoding changes every byte. If a duplicate check rested on the content hash
    alone, saving the same photograph at another quality would defeat it."""
    original = _photo(3)
    first = _encoded(original, quality=95)
    second = _encoded(original, quality=40)

    assert first != second
    assert hamming(perceptual_hash(first), perceptual_hash(second)) <= NEAR_DUPLICATE_DISTANCE


def test_rotating_a_photograph_does_not_hide_it():
    """A camera writes the orientation tag without anybody choosing to, and re-saving a
    picture rotated is the cheapest way to defeat a hash that ignores it."""
    original = _photo(4)
    upright = _encoded(original)
    buffer = io.BytesIO()
    exif = Image.Exif()
    exif[274] = 3  # Orientation: rotated 180 degrees.
    original.rotate(180).save(buffer, format="JPEG", exif=exif)

    assert hamming(perceptual_hash(upright), perceptual_hash(buffer.getvalue())) <= (
        NEAR_DUPLICATE_DISTANCE
    )


def test_bytes_that_are_not_an_image_are_refused_rather_than_hashed():
    with pytest.raises(PerceptualHashUnsupported):
        perceptual_hash(b"this is a PDF, honestly")


def test_an_unhashable_upload_still_uploads():
    assert perceptual_hash_for("application/pdf", b"%PDF-1.7") is None
    assert perceptual_hash_for("image/jpeg", b"truncated") is None
    assert perceptual_hash_for("image/jpeg", _encoded(_photo(2))) is not None


def test_hashes_of_different_lengths_are_not_silently_compared():
    with pytest.raises(ValueError):
        hamming("ffff", "ffffffffffffffff")


def _invoice(ref: str, program: str, *, number: str = "INV-4411", vendor: str = "Acme Supplies",
             total: int = 120000, issued: str = "2026-03-01") -> InvoiceFacts:
    return InvoiceFacts(
        evidence_ref=ref,
        program_ref=program,
        vendor=vendor,
        invoice_number=number,
        total_minor=total,
        currency="GBP",
        issued_on=issued,
    )


def test_one_invoice_billed_to_two_programmes_is_reported_as_such():
    findings = duplicate_invoices([_invoice("ev-1", "prog-a"), _invoice("ev-2", "prog-b")])

    assert [f.kind for f in findings] == ["DUPLICATE_INVOICE"]
    assert "2 programmes" in findings[0].explanation
    assert findings[0].subjects["evidence"] == ["ev-1", "ev-2"]
    assert findings[0].subjects["programs"] == ["prog-a", "prog-b"]


def test_the_same_invoice_twice_in_one_programme_reads_differently():
    findings = duplicate_invoices([_invoice("ev-1", "prog-a"), _invoice("ev-2", "prog-a")])

    assert len(findings) == 1
    assert "registered 2 times" in findings[0].explanation
    assert "programmes" not in findings[0].explanation


def test_formatting_differences_do_not_hide_the_same_invoice():
    findings = duplicate_invoices(
        [
            _invoice("ev-1", "prog-a", number="INV-4411", vendor="Acme Supplies"),
            _invoice("ev-2", "prog-b", number="inv4411", vendor="ACME SUPPLIES."),
        ]
    )
    assert len(findings) == 1


def test_different_invoices_from_the_same_vendor_are_left_alone():
    assert (
        duplicate_invoices(
            [
                _invoice("ev-1", "prog-a", number="INV-1", total=100, issued="2026-03-01"),
                _invoice("ev-2", "prog-b", number="INV-2", total=200, issued="2026-03-02"),
            ]
        )
        == []
    )


def test_one_evidence_object_does_not_duplicate_itself():
    """The same record read twice is a query artefact, not a duplicate invoice."""
    assert duplicate_invoices([_invoice("ev-1", "prog-a"), _invoice("ev-1", "prog-a")]) == []


def test_the_same_vendor_billing_one_amount_twice_in_a_day_is_not_a_finding():
    """A supplier delivering identical goods to two projects on one day issues two
    invoices for the same amount on the same date. In this domain that is routine, and a
    detector firing on it would fill the queue ADR-016 depends on keeping clean."""
    assert (
        duplicate_invoices(
            [
                _invoice("ev-1", "prog-a", number="INV-1"),
                _invoice("ev-2", "prog-a", number="INV-2"),
            ]
        )
        == []
    )


def test_an_invoice_with_nothing_readable_on_it_is_not_matched_to_every_other_one():
    """An unreadable scan extracts as empty. Grouping those together would report every
    failed extraction as a duplicate of every other, which is the noise that kills a
    queue."""
    blank = [
        InvoiceFacts("ev-1", "prog-a", "", "", 0, "", ""),
        InvoiceFacts("ev-2", "prog-b", "", "", 0, "", ""),
    ]
    assert duplicate_invoices(blank) == []


def test_the_same_photograph_under_two_projects_is_reported():
    hashes = perceptual_hash(_encoded(_photo(5)))
    findings = reused_images(
        [
            ImageFacts("ev-1", "proj-a", "sha256:aaa", hashes),
            ImageFacts("ev-2", "proj-b", "sha256:bbb", hashes),
        ]
    )
    assert [f.kind for f in findings] == ["REUSED_IMAGE"]
    assert findings[0].subjects["projects"] == ["proj-a", "proj-b"]


def test_an_identical_file_is_reported_as_a_fact_not_a_resemblance():
    hashes = perceptual_hash(_encoded(_photo(6)))
    findings = reused_images(
        [
            ImageFacts("ev-1", "proj-a", "sha256:same", hashes),
            ImageFacts("ev-2", "proj-b", "sha256:same", hashes),
        ]
    )
    assert "byte for byte" in findings[0].explanation
    assert findings[0].subjects["distance"] == 0


def test_two_photographs_of_one_delivery_are_not_a_finding():
    """Pictures from the same distribution are expected to look alike. Reporting that
    would report every honest delivery."""
    hashes = perceptual_hash(_encoded(_photo(7)))
    assert (
        reused_images(
            [
                ImageFacts("ev-1", "proj-a", "sha256:aaa", hashes),
                ImageFacts("ev-2", "proj-a", "sha256:bbb", hashes),
            ]
        )
        == []
    )


def test_different_photographs_across_projects_are_not_a_finding():
    assert (
        reused_images(
            [
                ImageFacts("ev-1", "proj-a", "sha256:aaa", perceptual_hash(_encoded(_photo(1)))),
                ImageFacts("ev-2", "proj-b", "sha256:bbb", perceptual_hash(_encoded(_photo(9)))),
            ]
        )
        == []
    )


def _payment(ref: str, payee: str, amount: int, *, program: str = "prog-a",
             currency: str = "GBP") -> PaymentFacts:
    return PaymentFacts(ref, program, payee, payee.title(), amount, currency)


def test_one_supplier_taking_most_of_a_programme_is_surfaced_with_its_share():
    payments = [_payment(f"tx-{i}", "acme", 1000) for i in range(5)]
    payments.append(_payment("tx-9", "other", 500))

    findings = vendor_concentration(payments)

    assert [f.kind for f in findings] == ["VENDOR_CONCENTRATION"]
    assert findings[0].subjects["payee"] == "acme"
    assert findings[0].subjects["share"] == pytest.approx(5000 / 5500, abs=1e-4)
    assert "91%" in findings[0].explanation


def test_a_programme_with_too_few_payments_has_no_concentration_to_report():
    payments = [_payment(f"tx-{i}", "acme", 1000) for i in range(CONCENTRATION_MINIMUM_PAYMENTS - 2)]
    payments.append(_payment("tx-9", "other", 1))
    assert len(payments) == CONCENTRATION_MINIMUM_PAYMENTS - 1
    assert vendor_concentration(payments) == []

    payments.append(_payment("tx-10", "other", 1))
    assert len(payments) == CONCENTRATION_MINIMUM_PAYMENTS
    assert [f.kind for f in vendor_concentration(payments)] == ["VENDOR_CONCENTRATION"]


def test_a_programme_with_one_supplier_is_not_a_concentration_finding():
    """A sole supplier is a fact about the programme's design, not a pattern within it."""
    assert vendor_concentration([_payment(f"tx-{i}", "acme", 1000) for i in range(8)]) == []


def test_currencies_are_not_added_together_to_compute_a_share():
    """Summing across currencies would state a share of a total that does not exist."""
    payments = [_payment(f"gbp-{i}", "acme", 1000) for i in range(5)]
    payments += [_payment(f"gbp-o{i}", "other", 1000) for i in range(5)]
    payments += [_payment(f"kes-{i}", "acme", 1000, currency="KES") for i in range(5)]
    payments += [_payment("kes-o", "other", 1, currency="KES")]

    findings = vendor_concentration(payments)

    assert [f.subjects["currency"] for f in findings] == ["KES"]
    assert findings[0].subjects["totalMinor"] == 5001


def test_concentration_is_computed_per_programme():
    payments = [_payment(f"a-{i}", "acme", 1000, program="prog-a") for i in range(5)]
    payments.append(_payment("a-o", "other", 1, program="prog-a"))
    payments += [_payment(f"b-{i}", "acme", 1000, program="prog-b") for i in range(3)]
    payments += [_payment(f"b-o{i}", "other", 1000, program="prog-b") for i in range(3)]

    findings = vendor_concentration(payments)

    assert [f.subjects["program"] for f in findings] == ["prog-a"]


def test_a_decompression_bomb_does_not_reach_the_caller():
    """This runs inline on the upload path, so an escaping error takes evidence upload
    down. A few hundred bytes can declare a 60000x60000 canvas."""
    import struct
    import zlib

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    bomb = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 60000, 60000, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00" * 100))
        + chunk(b"IEND", b"")
    )

    assert perceptual_hash_for("image/png", bomb) is None
    with pytest.raises(PerceptualHashUnsupported):
        perceptual_hash(bomb)


def test_banding_finds_every_pair_an_exhaustive_comparison_would():
    """Comparing only images that share an 8-bit band is an optimisation, and an
    optimisation that silently drops findings is worse than the quadratic loop it
    replaced. Two hashes within six bits share at least two of the eight bands."""
    import random

    def exhaustive(images):
        pairs = set()
        for index, left in enumerate(images):
            for right in images[index + 1 :]:
                if left.project_ref == right.project_ref:
                    continue
                distance = hamming(left.perceptual_hash, right.perceptual_hash)
                if left.content_hash == right.content_hash or distance <= NEAR_DUPLICATE_DISTANCE:
                    pairs.add(tuple(sorted([left.evidence_ref, right.evidence_ref])))
        return pairs

    random.seed(11)
    for _ in range(40):
        images = []
        for index in range(50):
            digest = random.getrandbits(64)
            images.append(
                ImageFacts(f"e{index}", f"p{index % 5}", f"sha{index}", f"{digest:016x}")
            )
            if random.random() < 0.4:
                mutated = digest
                for bit in random.sample(range(64), random.randint(0, 8)):
                    mutated ^= 1 << bit
                images.append(
                    ImageFacts(
                        f"e{index}n", f"p{(index + 2) % 5}", f"sha{index}n", f"{mutated:016x}"
                    )
                )
        banded = {tuple(f.subjects["evidence"]) for f in reused_images(images)}
        assert banded == exhaustive(images)


def test_a_pair_is_reported_once_even_though_it_shares_several_bands():
    digest = f"{0xABCD1234ABCD1234:016x}"
    findings = reused_images(
        [
            ImageFacts("ev-1", "proj-a", "sha256:aaa", digest),
            ImageFacts("ev-2", "proj-b", "sha256:bbb", digest),
        ]
    )
    assert len(findings) == 1


def test_what_the_hash_does_not_catch_is_stated_rather_than_implied():
    """A difference hash is not invariant under mirroring, rotation or a hard crop. The
    detector is for careless reuse, not for an adversary, and the docstring says so."""
    original = _photo(12)
    upright = perceptual_hash(_encoded(original))
    mirrored = perceptual_hash(_encoded(ImageOps.mirror(original)))

    assert hamming(upright, mirrored) > NEAR_DUPLICATE_DISTANCE
    assert "mirrored, rotated or cropped hard" in reused_images.__doc__
