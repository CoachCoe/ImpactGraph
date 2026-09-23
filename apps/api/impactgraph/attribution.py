"""What one funder's money reached, and what it did not.

A donor gave money to a program and the program reports on itself. This answers the
narrower question they actually asked, by walking the typed ledger forward from a single
funding record rather than summing a program.

Attribution is exact rather than apportioned, because `AllocationRecord.funding_ref`
names one funding record: an allocation is money from that funder, not a share of a pool.
The honest limit sits one level up. A funding record that represents many contributors --
an institutional pool, a fundraising total -- cannot separate them, so this traces the
record, and the caller is told that is what it traced.

Queries are batched one per level. This is public and unauthenticated, and a traversal
that issued a query per node would let a single request fan out across a program.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .domain import Money
from .persistence import (
    AllocationRecord,
    ClaimRecord,
    DeliveryRecord,
    FinancialTransactionRecord,
    FundingRecord,
    OutcomeRecord,
    ProvenanceEdgeRecord,
    public_funder_name,
)


class MixedCurrencyError(ValueError):
    """Records under one funding record do not share its currency.

    `Money` refuses to combine currencies without a recorded rate, and this refuses to
    answer rather than present a total that silently added two of them. Conversion with a
    traceable rate is the financial adapter's work, not this traversal's.
    """


def _remainder(larger: Money, smaller: Money) -> tuple[Money, Money]:
    """Split a difference into what is left and what was exceeded.

    `Money` refuses to hold a negative, which is the right decision for a ledger and the
    wrong shape for this answer: a program can commit more than one funding record
    supplied, and the seeded portfolio does. Reporting that as a remainder of zero and an
    overspend of the difference keeps both numbers true.
    """
    if larger.currency != smaller.currency:
        # Before the comparison, not after. Minor units of different currencies are not
        # comparable, and an ordering taken on them is meaningless even when the
        # subtraction that follows would have raised anyway.
        raise MixedCurrencyError(
            f"Refusing to compare {larger.currency} with {smaller.currency}"
        )
    if larger.amount_minor >= smaller.amount_minor:
        return larger - smaller, Money.zero(larger.currency)
    return Money.zero(larger.currency), smaller - larger


def _money(record, currency: str) -> Money:
    if record.currency != currency:
        raise MixedCurrencyError(
            f"{record.external_id} is in {record.currency}, not {currency}"
        )
    return Money(record.amount_minor, record.currency)


def _group(rows, key) -> dict[str, list]:
    grouped: dict[str, list] = defaultdict(list)
    for row in rows:
        grouped[key(row)].append(row)
    return grouped


def funding_attribution(session: Session, funding_ref: str) -> dict[str, Any]:
    """Trace one funding record forward to the claims it ultimately supported."""
    funding = session.scalar(
        select(FundingRecord).where(FundingRecord.external_id == funding_ref)
    )
    if funding is None:
        raise LookupError("Funding not found")

    received = Money(funding.amount_minor, funding.currency)
    currency = received.currency

    allocations = list(
        session.scalars(
            select(AllocationRecord).where(AllocationRecord.funding_ref == funding_ref)
        )
    )
    allocation_ids = [item.external_id for item in allocations]

    transactions = (
        list(
            session.scalars(
                select(FinancialTransactionRecord)
                .where(FinancialTransactionRecord.allocation_ref.in_(allocation_ids))
                .order_by(FinancialTransactionRecord.occurred_on)
            )
        )
        if allocation_ids
        else []
    )
    transaction_ids = [item.external_id for item in transactions]

    deliveries = (
        list(
            session.scalars(
                select(DeliveryRecord).where(
                    DeliveryRecord.financial_transaction_ref.in_(transaction_ids)
                )
            )
        )
        if transaction_ids
        else []
    )
    delivery_ids = [item.external_id for item in deliveries]

    outcomes = (
        list(
            session.scalars(
                select(OutcomeRecord).where(OutcomeRecord.delivery_ref.in_(delivery_ids))
            )
        )
        if delivery_ids
        else []
    )
    outcome_ids = [item.external_id for item in outcomes]

    edges = (
        list(
            session.scalars(
                select(ProvenanceEdgeRecord).where(
                    ProvenanceEdgeRecord.source_id.in_(outcome_ids),
                    ProvenanceEdgeRecord.source_type == "OUTCOME",
                    ProvenanceEdgeRecord.target_type == "CLAIM",
                    ProvenanceEdgeRecord.superseded_by.is_(None),
                )
            )
        )
        if outcome_ids
        else []
    )
    claim_ids = {edge.target_id for edge in edges}
    claims = (
        {
            claim.external_id: claim
            for claim in session.scalars(
                select(ClaimRecord).where(ClaimRecord.external_id.in_(claim_ids))
            )
        }
        if claim_ids
        else {}
    )

    # Spend the program made without naming an allocation cannot be attributed to anyone,
    # and is reported so a reader can see that the traced total is not the whole of it.
    unallocated_rows = list(
        session.scalars(
            select(FinancialTransactionRecord).where(
                FinancialTransactionRecord.program_ref == funding.program_ref,
                FinancialTransactionRecord.allocation_ref.is_(None),
            )
        )
    )
    unallocated = Money.zero(currency)
    for row in unallocated_rows:
        unallocated = unallocated + _money(row, currency)

    by_allocation = _group(transactions, lambda row: row.allocation_ref)
    by_transaction = _group(deliveries, lambda row: row.financial_transaction_ref)
    by_delivery = _group(outcomes, lambda row: row.delivery_ref)
    by_outcome = _group(edges, lambda row: row.source_id)

    committed = Money.zero(currency)
    spent = Money.zero(currency)
    allocation_views: list[dict[str, Any]] = []

    for allocation in allocations:
        allocated = _money(allocation, currency)
        committed = committed + allocated
        allocation_spent = Money.zero(currency)
        transaction_views: list[dict[str, Any]] = []

        for transaction in by_allocation.get(allocation.external_id, []):
            paid = _money(transaction, currency)
            allocation_spent = allocation_spent + paid
            delivery_views = [
                {
                    "id": delivery.external_id,
                    "item": delivery.item,
                    "quantity": delivery.quantity,
                    "deliveredOn": delivery.delivered_on,
                    "outcomes": [
                        {
                            "id": outcome.external_id,
                            "metric": outcome.metric,
                            "value": outcome.value,
                            "unit": outcome.unit,
                            "region": outcome.region,
                            # Carried through, so a figure reached by following one
                            # contribution is qualified exactly as it is on the claim.
                            "method": outcome.method,
                            "source": outcome.source,
                            "confidencePercent": outcome.confidence_percent,
                            "claims": [
                                {
                                    "id": claim.external_id,
                                    "statement": claim.statement,
                                    # Shown whatever it says. A transparency product that
                                    # reports only a funder's successes is not one.
                                    "status": claim.status,
                                }
                                for edge in by_outcome.get(outcome.external_id, [])
                                if (claim := claims.get(edge.target_id)) is not None
                            ],
                        }
                        for outcome in by_delivery.get(delivery.external_id, [])
                    ],
                }
                for delivery in by_transaction.get(transaction.external_id, [])
            ]
            transaction_views.append(
                {
                    "id": transaction.external_id,
                    "payee": transaction.payee_name,
                    "amount": paid.as_dict(),
                    "occurredOn": transaction.occurred_on,
                    "matchStatus": transaction.match_status,
                    "deliveries": delivery_views,
                }
            )

        spent = spent + allocation_spent
        unspent, overspent = _remainder(allocated, allocation_spent)
        allocation_views.append(
            {
                "id": allocation.external_id,
                "purpose": allocation.purpose,
                "projectId": allocation.project_ref,
                "amount": allocated.as_dict(),
                "spent": allocation_spent.as_dict(),
                "unspent": unspent.as_dict(),
                "overspent": overspent.as_dict(),
                "transactions": transaction_views,
            }
        )

    uncommitted, overcommitted = _remainder(received, committed)
    return {
        "fundingId": funding.external_id,
        "funder": public_funder_name(funding),
        "programId": funding.program_ref,
        "receivedOn": funding.received_on,
        "received": received.as_dict(),
        "committed": committed.as_dict(),
        "spent": spent.as_dict(),
        # Reported alongside what was deployed rather than beneath it. A funder who is
        # only shown the part of their money that moved has been told half the answer.
        "uncommitted": uncommitted.as_dict(),
        "overcommitted": overcommitted.as_dict(),
        "unallocatedProgramSpend": unallocated.as_dict(),
        "allocations": allocation_views,
        "method": {
            "basis": "EXACT_BY_FUNDING_RECORD",
            "explanation": (
                "Each allocation names the single funding record it draws on, so the "
                "money below is this record's rather than a share of a pool. Where a "
                "funding record represents more than one contributor, their individual "
                "contributions cannot be separated and this traces the record as a whole."
            ),
            "limits": (
                "Only spend that names an allocation is traced. Payments the program made "
                "without one cannot be attributed to any funder and are reported "
                "separately as unallocated program spend."
            ),
        },
    }
