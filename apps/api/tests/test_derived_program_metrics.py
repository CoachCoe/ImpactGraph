"""The dashboard's headline figures must agree with the records they link to.

The program response carried a JSON blob saying 12 filtration systems and 2,840 people
while the only DeliveryRecord was quantity 2 and the only OutcomeRecord was value 200 --
and the dashboard hyperlinked each figure to the claim whose records contradicted it, under
the words "every figure above leads to its own records".
"""

from __future__ import annotations

from sqlalchemy import func, select

from impactgraph.main import session_factory
from impactgraph.persistence import DeliveryRecord, OutcomeRecord
from impactgraph.read_model import PROGRAM_ID, TransparencyReadRepository


def program() -> dict:
    assert session_factory is not None
    with session_factory() as session:
        return TransparencyReadRepository(session).program(PROGRAM_ID)


def totals() -> tuple[int, int]:
    assert session_factory is not None
    with session_factory() as session:
        delivered = session.scalar(
            select(func.coalesce(func.sum(DeliveryRecord.quantity), 0)).where(
                DeliveryRecord.program_ref == PROGRAM_ID
            )
        )
        served = session.scalar(
            select(func.coalesce(func.sum(OutcomeRecord.value), 0)).where(
                OutcomeRecord.program_ref == PROGRAM_ID
            )
        )
        return int(delivered), int(served)


def test_the_headline_figures_equal_the_records_behind_them():
    delivered, served = totals()
    payload = program()
    assert payload["filtrationSystems"] == delivered
    assert payload["peopleServed"] == served


def test_the_figures_are_not_the_old_fixture_values():
    """A guard against the blob coming back: 12 and 2,840 were never in the records."""
    payload = program()
    assert (payload["filtrationSystems"], payload["peopleServed"]) != (12, 2840)


def test_no_verification_percentage_is_served():
    """Nothing computes one. Serving a number invites a page to render it."""
    assert "verificationPercent" not in program()


def test_a_figure_moves_when_its_record_moves():
    """The point of deriving: change the record and the headline follows."""
    assert session_factory is not None
    before = program()["peopleServed"]
    with session_factory.begin() as session:
        outcome = session.scalars(
            select(OutcomeRecord).where(OutcomeRecord.program_ref == PROGRAM_ID)
        ).first()
        original = outcome.value
        outcome.value = original + 17
    try:
        assert program()["peopleServed"] == before + 17
    finally:
        with session_factory.begin() as session:
            outcome = session.scalars(
                select(OutcomeRecord).where(OutcomeRecord.program_ref == PROGRAM_ID)
            ).first()
            outcome.value = original
