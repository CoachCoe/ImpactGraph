"""Asking the people a claim describes, without the organisation being able to steer it.

If the operator supplies the numbers and sees the answers, the signal is worth nothing.
These pin the properties that make it worth something, and the one that refuses to run
until a safeguarding review exists.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import select

from impactgraph.confirmation import (
    ConfirmationChannelDisabled,
    aggregate,
    contact_hash,
    dispatch,
    open_round,
    record_answer,
    selected_for,
)
from impactgraph.persistence import BeneficiaryContactRecord, ConfirmationRoundRecord
from impactgraph.sampling import MINIMUM_REPORTABLE_SAMPLE
from tests.support import memory_session


def write(session):
    """A write block that does not trip over a read.

    A read starts an implicit transaction and leaves it open, and `Session.begin` refuses
    while one is active. Rolling the idle one back first keeps the tests readable rather
    than threading every read through a begin block.
    """
    session.rollback()
    return session.begin()


SALT = b"a-programme-specific-salt"
PROGRAM = "program-clean-water-kenya-2026"


@pytest.fixture
def session():
    db = memory_session()
    with db.begin():
        for index in range(60):
            db.add(
                BeneficiaryContactRecord(
                    program_ref=PROGRAM,
                    contact_hash=contact_hash(f"+2547000000{index:02d}", salt=SALT),
                    sealed_contact=b"sealed",
                    household_ref=f"household-{index}",
                )
            )
    return db


def a_round(session, seed="delivery-water-12:2026-09-14"):
    with write(session):
        open_round(
            session,
            round_id="round-1",
            delivery_ref="delivery-water-12",
            program_ref=PROGRAM,
            seed=seed,
        )
    return session.scalar(
        select(ConfirmationRoundRecord).where(ConfirmationRoundRecord.external_id == "round-1")
    )


def test_the_platform_draws_the_sample_and_records_how(session):
    """"We sampled randomly" is an assertion by the organisation being checked unless the
    selection can be re-derived."""
    record = a_round(session)
    assert record.population == 60
    assert 0 < record.sample_size <= 60
    assert "sha256" in record.method
    assert record.seed


def test_the_selection_is_reproducible_rather_than_stored(session):
    """A stored list is one somebody could have edited afterwards."""
    record = a_round(session)
    assert selected_for(session, record) == selected_for(session, record)
    assert len(selected_for(session, record)) == record.sample_size


def test_somebody_who_was_not_asked_cannot_answer(session):
    """Otherwise an operator reaching this path manufactures agreement from numbers that
    were never sampled."""
    a_round(session)
    with pytest.raises(ValueError, match="not selected"), write(session):
        record_answer(
            session,
            round_ref="round-1",
            contact_hash_value=contact_hash("+254799999999", salt=SALT),
            answer="CONFIRMED",
        )


def test_answering_twice_does_not_count_twice(session):
    """A resend is ordinary on a feature phone."""
    record = a_round(session)
    who = selected_for(session, record)[0]
    with write(session):
        record_answer(session, round_ref="round-1", contact_hash_value=who, answer="CONFIRMED")
    with write(session):
        record_answer(session, round_ref="round-1", contact_hash_value=who, answer="DISPUTED")

    from impactgraph.persistence import ConfirmationResponseRecord

    rows = list(session.scalars(select(ConfirmationResponseRecord)))
    assert len(rows) == 1
    assert rows[0].answer == "DISPUTED"


def test_a_small_round_reports_nothing_at_all(session):
    """Four confirmations out of five identifies the fifth person to anyone who knows who
    was asked, which in a small village is everyone."""
    record = a_round(session)
    who = selected_for(session, record)
    with write(session):
        for person in who[: MINIMUM_REPORTABLE_SAMPLE - 1]:
            record_answer(
                session, round_ref="round-1", contact_hash_value=person, answer="CONFIRMED"
            )

    summary = aggregate(session, "round-1")
    assert summary.reportable is False
    assert summary.confirmed == 0, "a count leaked below the reportable threshold"
    assert "elimination" in summary.reason


def test_a_large_enough_round_reports_counts_and_an_interval(session):
    record = a_round(session)
    who = selected_for(session, record)
    with write(session):
        for index, person in enumerate(who[:20]):
            record_answer(
                session,
                round_ref="round-1",
                contact_hash_value=person,
                answer="DISPUTED" if index < 3 else "CONFIRMED",
            )

    summary = aggregate(session, "round-1")
    assert summary.reportable is True
    assert (summary.confirmed, summary.disputed, summary.responded) == (17, 3, 20)
    assert 0 < summary.rate < 1
    assert summary.margin > 0


def test_no_answer_is_not_counted_as_agreement(session):
    """Silence is not consent here either."""
    record = a_round(session)
    who = selected_for(session, record)
    with write(session):
        for person in who[:10]:
            record_answer(
                session, round_ref="round-1", contact_hash_value=person, answer="CONFIRMED"
            )
        for person in who[10:20]:
            record_answer(
                session, round_ref="round-1", contact_hash_value=person, answer="NO_ANSWER"
            )

    summary = aggregate(session, "round-1")
    assert summary.responded == 10
    assert summary.confirmed == 10


def test_somebody_who_asked_to_stop_is_not_selected(session):
    with write(session):
        for record in session.scalars(select(BeneficiaryContactRecord).limit(50)):
            record.opted_out_at = __import__("datetime").datetime.now(
                __import__("datetime").UTC
            )
    record = a_round(session)
    assert record.population == 10


def test_a_number_is_not_recoverable_from_its_hash():
    """A bare SHA-256 of a phone number is reversible by enumerating the number space,
    which for a national mobile range is minutes of compute."""
    number = "+254700000042"
    assert contact_hash(number, salt=SALT) != contact_hash(number, salt=b"another-salt")
    assert number.replace("+", "") not in contact_hash(number, salt=SALT)
    # Formatting is not identity: the same person texting from a differently written
    # number is still the same person.
    assert contact_hash("+254 700 000 042", salt=SALT) == contact_hash(number, salt=SALT)


def test_nobody_is_contacted_until_the_review_exists(session):
    """Not a convenience flag. This collects data from people in an unequal relationship
    with the organisation asking."""
    a_round(session)
    os.environ.pop("BENEFICIARY_CONFIRMATION_ENABLED", None)
    with pytest.raises(ConfirmationChannelDisabled, match="safeguarding review"):
        dispatch(session, "round-1")

    record = session.scalar(
        select(ConfirmationRoundRecord).where(ConfirmationRoundRecord.external_id == "round-1")
    )
    assert record.dispatched_at is None
