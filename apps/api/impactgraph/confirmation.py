"""Asking a sample of people whether a delivery reached them.

The crux of issue #8 is fraud resistance: if the organisation being checked supplies the
numbers and sees the answers, the signal is worth nothing. So three things are structural
rather than promised.

The platform draws the sample, from a recorded seed, so the selection is reproducible and
an operator cannot curate it. The operator never reads an individual response -- only
counts, and only above a sample size at which a dissenter cannot be identified by
elimination. And the numbers are sealed, so holding the database is not holding a
directory of aid recipients.

None of that addresses an operator who controls enrolment; see
docs/beneficiary-safeguarding.md, which states that residual risk rather than implying it
away.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .persistence import (
    BeneficiaryContactRecord,
    ConfirmationResponseRecord,
    ConfirmationRoundRecord,
)
from .sampling import MINIMUM_REPORTABLE_SAMPLE, confirmation_rate, draw, required_size

ANSWERS = ("CONFIRMED", "DISPUTED", "NO_ANSWER")


class ConfirmationChannelDisabled(RuntimeError):
    """Dispatch is refused until the safeguarding review is signed off.

    Not a feature flag for convenience. This collects data from people in an unequal
    relationship with the organisation asking, and issue #8 requires the review before any
    of it happens.
    """


def channel_enabled() -> bool:
    return os.getenv("BENEFICIARY_CONFIRMATION_ENABLED", "").lower() in {"1", "true", "yes"}


def contact_hash(number: str, *, salt: bytes) -> str:
    """A stable identifier for a phone number that is not the number.

    Keyed rather than plain: a bare SHA-256 of a phone number is trivially reversible by
    enumerating the number space, which for a national mobile range is minutes of compute.
    """
    normalised = "".join(character for character in number if character.isdigit())
    return hmac.new(salt, normalised.encode("utf-8"), hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class Aggregate:
    """What anyone outside the round is allowed to know."""

    reportable: bool
    sampled: int
    responded: int
    confirmed: int
    disputed: int
    rate: float
    margin: float
    reason: str = ""


def open_round(
    session: Session,
    *,
    round_id: str,
    delivery_ref: str,
    program_ref: str,
    seed: str,
    margin: float = 0.1,
) -> ConfirmationRoundRecord:
    """Draw a sample for a delivery and record how it was drawn.

    Opening a round does not contact anybody. Dispatch is separate and refuses to run
    while the channel is disabled, so a round can be prepared and reviewed before anyone
    is messaged.
    """
    population = [
        record.contact_hash
        for record in session.scalars(
            select(BeneficiaryContactRecord).where(
                BeneficiaryContactRecord.program_ref == program_ref,
                BeneficiaryContactRecord.opted_out_at.is_(None),
            )
        )
    ]
    sample = draw(population, seed=seed, size=required_size(len(population), margin=margin))
    record = ConfirmationRoundRecord(
        external_id=round_id,
        delivery_ref=delivery_ref,
        program_ref=program_ref,
        seed=seed,
        method=sample.method,
        population=sample.population,
        sample_size=sample.size,
    )
    session.add(record)
    return record


def selected_for(session: Session, round_record: ConfirmationRoundRecord) -> tuple[str, ...]:
    """Re-derive exactly who this round selected.

    Stored nowhere: the seed and the population reproduce it, which is what makes the
    selection auditable rather than a list somebody could have edited afterwards.
    """
    population = [
        record.contact_hash
        for record in session.scalars(
            select(BeneficiaryContactRecord).where(
                BeneficiaryContactRecord.program_ref == round_record.program_ref,
                BeneficiaryContactRecord.opted_out_at.is_(None),
            )
        )
    ]
    return draw(
        population, seed=round_record.seed, size=round_record.sample_size
    ).selected


def record_answer(
    session: Session,
    *,
    round_ref: str,
    contact_hash_value: str,
    answer: str,
) -> ConfirmationResponseRecord:
    """One answer per person per round, and only from someone who was asked."""
    if answer not in ANSWERS:
        raise ValueError(f"answer must be one of {', '.join(ANSWERS)}")
    round_record = session.scalar(
        select(ConfirmationRoundRecord).where(ConfirmationRoundRecord.external_id == round_ref)
    )
    if round_record is None:
        raise LookupError("No such confirmation round")
    if round_record.closed_at is not None:
        raise ValueError("That round is closed")
    if contact_hash_value not in selected_for(session, round_record):
        # Otherwise an operator who can reach this path can manufacture agreement from
        # numbers that were never sampled.
        raise ValueError("That person was not selected for this round")

    existing = session.scalar(
        select(ConfirmationResponseRecord).where(
            ConfirmationResponseRecord.round_ref == round_ref,
            ConfirmationResponseRecord.contact_hash == contact_hash_value,
        )
    )
    if existing is not None:
        # Answering twice does not count twice. A resend is ordinary on a feature phone.
        existing.answer = answer
        existing.responded_at = datetime.now(UTC)
        return existing

    record = ConfirmationResponseRecord(
        round_ref=round_ref, contact_hash=contact_hash_value, answer=answer
    )
    session.add(record)
    return record


def aggregate(session: Session, round_ref: str) -> Aggregate:
    """Counts only, and only when a count cannot be unpicked.

    Below the minimum, four confirmations out of five identifies the dissenter to anyone
    who knows who was asked -- which in a small village is everyone.
    """
    round_record = session.scalar(
        select(ConfirmationRoundRecord).where(ConfirmationRoundRecord.external_id == round_ref)
    )
    if round_record is None:
        raise LookupError("No such confirmation round")
    responses = list(
        session.scalars(
            select(ConfirmationResponseRecord).where(
                ConfirmationResponseRecord.round_ref == round_ref
            )
        )
    )
    answered = [item for item in responses if item.answer != "NO_ANSWER"]
    confirmed = sum(1 for item in answered if item.answer == "CONFIRMED")
    disputed = sum(1 for item in answered if item.answer == "DISPUTED")
    rate, margin = confirmation_rate(confirmed, len(answered))

    if len(answered) < MINIMUM_REPORTABLE_SAMPLE:
        return Aggregate(
            reportable=False,
            sampled=round_record.sample_size,
            responded=len(answered),
            confirmed=0,
            disputed=0,
            rate=0.0,
            margin=0.0,
            reason=(
                f"Fewer than {MINIMUM_REPORTABLE_SAMPLE} people have answered. Reporting "
                "now would let an individual's answer be worked out by elimination."
            ),
        )
    return Aggregate(
        reportable=True,
        sampled=round_record.sample_size,
        responded=len(answered),
        confirmed=confirmed,
        disputed=disputed,
        rate=rate,
        margin=margin,
    )


def dispatch(session: Session, round_ref: str) -> int:
    """Send the round. Refuses while the safeguarding review is outstanding."""
    if not channel_enabled():
        raise ConfirmationChannelDisabled(
            "BENEFICIARY_CONFIRMATION_ENABLED is not set. This contacts people in an "
            "unequal relationship with the organisation asking, and issue #8 requires a "
            "documented safeguarding review first -- see docs/beneficiary-safeguarding.md."
        )
    round_record = session.scalar(
        select(ConfirmationRoundRecord).where(ConfirmationRoundRecord.external_id == round_ref)
    )
    if round_record is None:
        raise LookupError("No such confirmation round")
    round_record.dispatched_at = datetime.now(UTC)
    return round_record.sample_size
