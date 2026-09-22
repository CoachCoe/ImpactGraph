"""What one funder's money reached, and what it did not.

A donor gave money to a program and the program reports on itself. This answers the
narrower question they actually asked, by walking the typed ledger forward from a single
funding record rather than summing a program.

Attribution is exact rather than apportioned, because `AllocationRecord.funding_ref`
names one funding record: an allocation is money from that funder, not a share of a pool.
The honest limit sits one level up. A funding record that represents many contributors --
an institutional pool, a fundraising total -- cannot separate them, so this traces the
record, and the caller is told that is what it traced.
"""

from __future__ import annotations

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
)


def _remainder(larger: Money, smaller: Money) -> tuple[Money, Money]:
    """Split a difference into what is left and what was exceeded.

    `Money` refuses to hold a negative, which is the right decision for a ledger and the
    wrong shape for this answer: a program can commit more than one funding record
    supplied, and the seeded portfolio does. Reporting that as a remainder of zero and an
    overspend of the difference keeps both numbers true.
    """
    if larger.amount_minor >= smaller.amount_minor:
        return larger - smaller, Money.zero(larger.currency)
    return Money.zero(larger.currency), smaller - larger


def _claims_supported_by_outcome(session: Session, outcome_ref: str) -> list[ClaimRecord]:
    claim_ids = list(
        session.scalars(
            select(ProvenanceEdgeRecord.target_id).where(
                ProvenanceEdgeRecord.source_id == outcome_ref,
                ProvenanceEdgeRecord.source_type == "OUTCOME",
                ProvenanceEdgeRecord.target_type == "CLAIM",
                ProvenanceEdgeRecord.superseded_by.is_(None),
            )
        )
    )
    if not claim_ids:
        return []
    return list(
        session.scalars(select(ClaimRecord).where(ClaimRecord.external_id.in_(claim_ids)))
    )


def funding_attribution(session: Session, funding_ref: str) -> dict[str, Any]:
    """Trace one funding record forward to the claims it ultimately supported."""
    funding = session.scalar(
        select(FundingRecord).where(FundingRecord.external_id == funding_ref)
    )
    if funding is None:
        raise LookupError("Funding not found")

    received = Money(funding.amount_minor, funding.currency)
    allocations = list(
        session.scalars(
            select(AllocationRecord).where(AllocationRecord.funding_ref == funding_ref)
        )
    )

    committed = Money.zero(received.currency)
    spent = Money.zero(received.currency)
    allocation_views: list[dict[str, Any]] = []

    for allocation in allocations:
        allocated = Money(allocation.amount_minor, allocation.currency)
        committed = committed + allocated
        transactions = list(
            session.scalars(
                select(FinancialTransactionRecord)
                .where(FinancialTransactionRecord.allocation_ref == allocation.external_id)
                .order_by(FinancialTransactionRecord.occurred_on)
            )
        )

        allocation_spent = Money.zero(allocated.currency)
        transaction_views: list[dict[str, Any]] = []
        for transaction in transactions:
            paid = Money(transaction.amount_minor, transaction.currency)
            allocation_spent = allocation_spent + paid
            deliveries = list(
                session.scalars(
                    select(DeliveryRecord).where(
                        DeliveryRecord.financial_transaction_ref == transaction.external_id
                    )
                )
            )
            delivery_views = []
            for delivery in deliveries:
                outcomes = list(
                    session.scalars(
                        select(OutcomeRecord).where(
                            OutcomeRecord.delivery_ref == delivery.external_id
                        )
                    )
                )
                delivery_views.append(
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
                                "claims": [
                                    {
                                        "id": claim.external_id,
                                        "statement": claim.statement,
                                        # Shown whatever it says. A transparency product
                                        # that reports only a funder's successes is not
                                        # a transparency product.
                                        "status": claim.status,
                                    }
                                    for claim in _claims_supported_by_outcome(
                                        session, outcome.external_id
                                    )
                                ],
                            }
                            for outcome in outcomes
                        ],
                    }
                )
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
        "funder": funding.funder_name,
        "programId": funding.program_ref,
        "receivedOn": funding.received_on,
        "received": received.as_dict(),
        "committed": committed.as_dict(),
        "spent": spent.as_dict(),
        # Reported alongside what was deployed rather than beneath it. A funder who is
        # only shown the part of their money that moved has been told half the answer.
        "uncommitted": uncommitted.as_dict(),
        "overcommitted": overcommitted.as_dict(),
        "allocations": allocation_views,
        "method": {
            "basis": "EXACT_BY_FUNDING_RECORD",
            "explanation": (
                "Each allocation names the single funding record it draws on, so the "
                "money below is this record's rather than a share of a pool. Where a "
                "funding record represents more than one contributor, their individual "
                "contributions cannot be separated and this traces the record as a whole."
            ),
        },
    }
