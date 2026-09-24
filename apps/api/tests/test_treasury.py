"""Money an organisation holds before it has decided what it funds.

The old model required every contribution to name a programme at the instant it was
received, which is right for a grant and wrong for a standing order: it left nowhere to
put unrestricted money, and made "funds held vs deployed" -- one of four things the
operating organisation commits to publishing -- answerable only per programme, which is
the one place it cannot be answered.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from impactgraph.financial import LEDGER_PAGE, financial_summary
from impactgraph.persistence import (
    AllocationRecord,
    FundingRecord,
    ProgramRecord,
    public_funder_name,
)
from impactgraph.treasury import AssignmentRefused, assign, held, position
from tests.support import memory_session

ORG = "org-global-water"
OTHER_ORG = "org-shelter"


@pytest.fixture
def session():
    return memory_session()


def _program(session, slug: str, org: str = ORG) -> None:
    session.add(
        ProgramRecord(
            slug=slug, name=slug, operator_name=org, operator_org_ref=org,
            region="Region", status="ACTIVE",
        )
    )


def _contribution(session, ref: str, *, minor: int = 100_000, program: str | None = None,
                  org: str = ORG, contributors: int = 1, name: str = "Jane Smith",
                  is_org: bool = False, currency: str = "USD") -> None:
    session.add(
        FundingRecord(
            external_id=ref,
            organization_ref=org,
            program_ref=program,
            contributor_count=contributors,
            funder_name=name,
            funder_is_organisation=is_org,
            amount_minor=minor,
            currency=currency,
            received_on="2026-03-01",
            source_ref=f"src-{ref}",
        )
    )


def test_a_contribution_can_arrive_without_naming_a_programme(session):
    with session.begin():
        _contribution(session, "c-1", program=None)

    with session.begin():
        row = session.scalar(select(FundingRecord).where(FundingRecord.external_id == "c-1"))
        assert row.program_ref is None
        assert row.assigned_at is None
        assert row.organization_ref == ORG


def test_held_money_is_the_money_with_no_programme(session):
    with session.begin():
        _program(session, "prog-a")
        _contribution(session, "c-1", minor=100_000, program=None)
        _contribution(session, "c-2", minor=250_000, program=None)
        _contribution(session, "c-3", minor=400_000, program="prog-a")

    with session.begin():
        [usd] = position(session, ORG)

    assert usd.received.amount_minor == 750_000
    assert usd.held.amount_minor == 350_000
    assert usd.assigned.amount_minor == 400_000


def test_a_position_is_reported_per_currency_and_never_blended(session):
    """Adding across currencies would misstate every part of the answer."""
    with session.begin():
        _contribution(session, "c-usd", minor=100_000, currency="USD", program=None)
        _contribution(session, "c-kes", minor=900_000, currency="KES", program=None)

    with session.begin():
        by_currency = {item.currency: item for item in position(session, ORG)}

    assert set(by_currency) == {"KES", "USD"}
    assert by_currency["USD"].held.amount_minor == 100_000
    assert by_currency["KES"].held.amount_minor == 900_000


def test_a_position_does_not_pool_organisations(session):
    with session.begin():
        _contribution(session, "c-1", minor=100_000, org=ORG, program=None)
        _contribution(session, "c-2", minor=999_000, org=OTHER_ORG, program=None)

    with session.begin():
        [ours] = position(session, ORG)
    assert ours.received.amount_minor == 100_000


def test_a_position_must_name_the_organisation(session):
    with session.begin(), pytest.raises(ValueError):
        position(session, "")


def test_held_money_stays_out_of_a_programmes_figures(session):
    """A contribution the programme has not been assigned has no place in its totals."""
    with session.begin():
        _program(session, "prog-a")
        _contribution(session, "assigned", minor=400_000, program="prog-a")
        _contribution(session, "held", minor=999_000, program=None)

    with session.begin():
        summary = financial_summary(session, "prog-a")

    assert summary["received"]["amountMinor"] == 400_000
    assert [item["id"] for item in summary["funding"]] == ["assigned"]


def test_assigning_a_contribution_gives_it_a_programme_and_a_time(session):
    with session.begin():
        _program(session, "prog-a")
        _contribution(session, "c-1", program=None)

    with session.begin():
        funding = assign(session, funding_ref="c-1", program_ref="prog-a", organization_ref=ORG)
        assert funding.program_ref == "prog-a"
        assert funding.assigned_at is not None

    with session.begin():
        assert position(session, ORG)[0].held.amount_minor == 0


def test_money_that_has_been_assigned_cannot_be_moved(session):
    """Reassigning allocated money would change what a published attribution says a
    contribution paid for, after somebody has read it."""
    with session.begin():
        _program(session, "prog-a")
        _program(session, "prog-b")
        _contribution(session, "c-1", program=None)
    with session.begin():
        assign(session, funding_ref="c-1", program_ref="prog-a", organization_ref=ORG)

    with session.begin(), pytest.raises(AssignmentRefused):
        assign(session, funding_ref="c-1", program_ref="prog-b", organization_ref=ORG)


def test_a_contribution_cannot_be_assigned_by_another_organisation(session):
    with session.begin():
        _program(session, "prog-a")
        _contribution(session, "c-1", org=ORG, program=None)

    with session.begin(), pytest.raises(LookupError):
        assign(session, funding_ref="c-1", program_ref="prog-a", organization_ref=OTHER_ORG)


def test_a_contribution_cannot_be_assigned_to_another_organisations_programme(session):
    with session.begin():
        _program(session, "prog-theirs", org=OTHER_ORG)
        _contribution(session, "c-1", org=ORG, program=None)

    with session.begin(), pytest.raises(AssignmentRefused):
        assign(session, funding_ref="c-1", program_ref="prog-theirs", organization_ref=ORG)


def test_held_lists_only_this_organisations_unassigned_money(session):
    with session.begin():
        _program(session, "prog-a")
        _contribution(session, "held-1", program=None)
        _contribution(session, "assigned-1", program="prog-a")
        _contribution(session, "theirs", org=OTHER_ORG, program=None)

    with session.begin():
        rows = held(session, ORG)

    assert [row.external_id for row in rows] == ["held-1"]


def test_held_is_paginated(session):
    with session.begin():
        for index in range(12):
            _contribution(session, f"c-{index:02d}", program=None)

    with session.begin():
        first = held(session, ORG, limit=5)
        second = held(session, ORG, limit=5, offset=5)

    assert len(first) == 5
    assert len(second) == 5
    assert {r.external_id for r in first}.isdisjoint(r.external_id for r in second)


def test_a_rollup_stands_for_many_people_and_names_none(session):
    """365 million payments a year cannot be one row each, and a roll-up has no single
    funder to name. ADR-018."""
    with session.begin():
        _contribution(session, "day-2026-03-01", minor=124_000, contributors=1240,
                      name="Daily contributions", program=None)

    with session.begin():
        row = session.scalar(
            select(FundingRecord).where(FundingRecord.external_id == "day-2026-03-01")
        )
        assert public_funder_name(row) == "1,240 individual donors"
        # Privilege does not unlock a name, because the row never held one.
        assert public_funder_name(row, privileged=True) == "1,240 individual donors"
        assert position(session, ORG)[0].contributors == 1240


def test_a_donor_who_asked_to_be_named_still_is(session):
    """#12's consent path has to survive the roll-up: a person who chose to be named
    against what they funded is a single row, and stays one."""
    with session.begin():
        _program(session, "prog-a")
        _contribution(session, "c-named", program="prog-a", name="Jane Smith")
    with session.begin():
        row = session.scalar(select(FundingRecord).where(FundingRecord.external_id == "c-named"))
        assert public_funder_name(row) == "An individual donor"
        row.publish_funder_name = True
    with session.begin():
        row = session.scalar(select(FundingRecord).where(FundingRecord.external_id == "c-named"))
        assert public_funder_name(row) == "Jane Smith"


def test_a_summary_totals_every_row_but_returns_a_page(session):
    """The totals are the answer; the rows are a courtesy. A programme funded by a
    standing order has more contributions than anybody reads at once, and this route is
    public and unauthenticated."""
    with session.begin():
        _program(session, "prog-a")
        for index in range(LEDGER_PAGE + 25):
            _contribution(session, f"c-{index:04d}", minor=1_000, program="prog-a")

    with session.begin():
        summary = financial_summary(session, "prog-a")

    assert summary["received"]["amountMinor"] == (LEDGER_PAGE + 25) * 1_000
    assert summary["fundingCount"] == LEDGER_PAGE + 25
    assert len(summary["funding"]) == LEDGER_PAGE


def test_a_restricted_grants_figures_are_what_they_always_were(session):
    with session.begin():
        _program(session, "prog-a")
        _contribution(session, "grant", minor=1_000_000, program="prog-a",
                      name="Institutional funding pool", is_org=True)
        session.add(
            AllocationRecord(
                external_id="alloc-1", program_ref="prog-a", project_ref="proj-a",
                funding_ref="grant", purpose="Filters", amount_minor=600_000, currency="USD",
            )
        )

    with session.begin():
        summary = financial_summary(session, "prog-a")

    assert summary["received"]["amountMinor"] == 1_000_000
    assert summary["committed"]["amountMinor"] == 600_000
    assert summary["uncommitted"]["amountMinor"] == 400_000
    assert summary["funding"][0]["funder"] == "Institutional funding pool"
