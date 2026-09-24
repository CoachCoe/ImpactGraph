"""Money an organisation holds, and what it has decided to do with it.

The programme is the wrong unit for this question. "How much has been given and how much
is still sitting here" is asked of the organisation, and a figure computed per programme
cannot answer it -- unrestricted money has no programme yet, which is the whole point of
holding it.

Totals are summed in the database rather than by reading every row into memory. A
contribution model built for a few dozen grants can get away with the latter; one built
for a standing order cannot.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .domain import Money
from .persistence import (
    AllocationRecord,
    FinancialTransactionRecord,
    FundingRecord,
    ProgramRecord,
)


class AssignmentRefused(ValueError):
    """The contribution cannot be assigned as asked."""


@dataclass(frozen=True)
class CurrencyPosition:
    """One currency's position. Never blended: see ADR-012 and #35."""

    currency: str
    received: Money
    held: Money
    assigned: Money
    contributors: int


def _totals_by_currency(session: Session, organization_ref: str, *, held_only: bool):
    condition = FundingRecord.organization_ref == organization_ref
    query = (
        select(
            FundingRecord.currency,
            func.sum(FundingRecord.amount_minor),
            func.sum(FundingRecord.contributor_count),
        )
        .where(condition)
        .group_by(FundingRecord.currency)
    )
    if held_only:
        query = query.where(FundingRecord.program_ref.is_(None))
    return {
        currency: (int(total or 0), int(count or 0))
        for currency, total, count in session.execute(query)
    }


def position(session: Session, organization_ref: str) -> list[CurrencyPosition]:
    """What this organisation has been given, and how much of it is still unassigned.

    One of the four things Dynamic Aid Delivery commits to publishing. Reported per
    currency because money in different currencies does not add up, and a single blended
    figure would misstate every part of it.
    """
    if not organization_ref:
        raise ValueError("a position is only ever for a named organisation")

    everything = _totals_by_currency(session, organization_ref, held_only=False)
    held = _totals_by_currency(session, organization_ref, held_only=True)

    positions = []
    for currency in sorted(everything):
        received_minor, contributors = everything[currency]
        held_minor = held.get(currency, (0, 0))[0]
        positions.append(
            CurrencyPosition(
                currency=currency,
                received=Money(received_minor, currency),
                held=Money(held_minor, currency),
                assigned=Money(received_minor - held_minor, currency),
                contributors=contributors,
            )
        )
    return positions


def position_response(session: Session, organization_ref: str) -> dict:
    return {
        "organizationRef": organization_ref,
        "byCurrency": [
            {
                "currency": item.currency,
                "received": item.received.as_dict(),
                "held": item.held.as_dict(),
                "assigned": item.assigned.as_dict(),
                "contributors": item.contributors,
            }
            for item in position(session, organization_ref)
        ],
        "generatedAt": datetime.now(UTC).isoformat(),
    }


def held(session: Session, organization_ref: str, *, limit: int = 50, offset: int = 0):
    """Contributions this organisation has received and not yet assigned."""
    if not organization_ref:
        raise ValueError("held money is only ever listed for a named organisation")
    return list(
        session.scalars(
            select(FundingRecord)
            .where(
                FundingRecord.organization_ref == organization_ref,
                FundingRecord.program_ref.is_(None),
            )
            .order_by(FundingRecord.received_on, FundingRecord.external_id)
            .limit(limit)
            .offset(offset)
        )
    )


def assign(
    session: Session,
    *,
    funding_ref: str,
    program_ref: str,
    organization_ref: str,
) -> FundingRecord:
    """Decide what a held contribution funds.

    One direction only. Reassigning money that has already been allocated would change
    what a published attribution says a contribution paid for, after somebody has read
    it -- the same objection as unpublishing a claim.
    """
    funding = session.scalar(
        select(FundingRecord).where(
            FundingRecord.external_id == funding_ref,
            # Scoped in the query: another organisation's contribution is indistinguishable
            # from one that does not exist.
            FundingRecord.organization_ref == organization_ref,
        )
    )
    if funding is None:
        raise LookupError(funding_ref)
    if funding.program_ref is not None:
        raise AssignmentRefused(
            f"{funding_ref} is already assigned to {funding.program_ref}. Money that has "
            "been allocated cannot be moved; what it paid for has been published."
        )

    program = session.scalar(select(ProgramRecord).where(ProgramRecord.slug == program_ref))
    if program is None:
        raise LookupError(program_ref)
    if program.operator_org_ref != organization_ref:
        raise AssignmentRefused("A contribution cannot be assigned to another organisation's programme")

    funding.program_ref = program_ref
    funding.assigned_at = datetime.now(UTC)
    return funding


def deployed_against(session: Session, organization_ref: str) -> dict[str, Money]:
    """Observed spend across this organisation's programmes, per currency.

    Reversed payments are excluded: money the bank took back was not deployed.
    """
    programs = select(ProgramRecord.slug).where(
        ProgramRecord.operator_org_ref == organization_ref
    )
    rows = session.execute(
        select(
            FinancialTransactionRecord.currency,
            func.sum(FinancialTransactionRecord.amount_minor),
        )
        .where(
            FinancialTransactionRecord.program_ref.in_(programs),
            FinancialTransactionRecord.settlement != "REVERSED",
        )
        .group_by(FinancialTransactionRecord.currency)
    )
    return {currency: Money(int(total or 0), currency) for currency, total in rows}


def committed_against(session: Session, organization_ref: str) -> dict[str, Money]:
    """What this organisation's programmes have committed, per currency."""
    programs = select(ProgramRecord.slug).where(
        ProgramRecord.operator_org_ref == organization_ref
    )
    rows = session.execute(
        select(AllocationRecord.currency, func.sum(AllocationRecord.amount_minor))
        .where(AllocationRecord.program_ref.in_(programs))
        .group_by(AllocationRecord.currency)
    )
    return {currency: Money(int(total or 0), currency) for currency, total in rows}
