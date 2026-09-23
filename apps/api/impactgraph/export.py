"""Letting someone leave with the record.

Everything here is inspectable through pages this application renders, which means every
check a reader makes is one this application agreed to show them. An export is the point
at which that stops being true: a journalist, an auditor or a rival can take the register
away and interrogate it with their own tools.

Rows carry the hashes needed to check them independently, because an export that cannot be
reconciled against the registry is a spreadsheet of assertions.

Column names are an interface. Anything anyone automates against becomes one whether it
was meant to or not, so they are stated here, tested, and changed deliberately.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable, Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .persistence import (
    AllocationRecord,
    ClaimRecord,
    DeliveryRecord,
    EvidenceRecord,
    FinancialTransactionRecord,
    FundingRecord,
    OutcomeRecord,
    public_funder_name,
)
from .verification import claim_subgraph

MONEY_TRAIL_COLUMNS = (
    "record_type",
    "id",
    "counterparty",
    "amount_minor",
    "currency",
    "occurred_on",
    "allocation_id",
    "match_status",
    "memo",
)

PROVENANCE_COLUMNS = (
    "claim_id",
    "source_type",
    "source_id",
    "relationship",
    "target_type",
    "target_id",
    "confirmed_onchain",
)

OUTCOME_COLUMNS = (
    "outcome_id",
    "program_id",
    "metric",
    "value",
    "unit",
    "region",
    "method",
    "source",
    "confidence_percent",
)

EVIDENCE_COLUMNS = (
    "evidence_id",
    "type",
    "visibility",
    "workflow_status",
    "integrity_status",
    "content_hash",
    "transaction_hash",
)


#: A spreadsheet evaluates a cell beginning with any of these as a formula. Quoting is
#: not protection: Excel, LibreOffice and Sheets strip the quotes and evaluate what is
#: left.
_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")


def _defuse(value: Any) -> Any:
    """Stop a cell being read as a formula by whoever opens the file.

    Payee names and memos arrive from a payment provider; methods and sources are typed
    by an operator. This product's premise is that data from parties nobody controls flows
    in, and an export is the route by which it flows back out into someone's spreadsheet.
    """
    if isinstance(value, str) and value.startswith(_FORMULA_LEAD):
        return "'" + value
    return value


def _render(columns: Iterable[str], rows: Iterable[dict[str, Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(columns), extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _defuse(value) for key, value in row.items()})
    return buffer.getvalue()


def money_trail_csv(session: Session, program_ref: str) -> str:
    """Funding in, what it was committed to, and what was observed leaving."""
    rows: list[dict[str, Any]] = []
    for item in session.scalars(
        select(FundingRecord).where(FundingRecord.program_ref == program_ref)
    ):
        rows.append(
            {
                "record_type": "FUNDING",
                "id": item.external_id,
                # The same rule as every other surface. This one leaves the building and
                # is opened in somebody else's spreadsheet, so it is the last place a
                # private individual's name should have survived.
                "counterparty": public_funder_name(item),
                "amount_minor": item.amount_minor,
                "currency": item.currency,
                "occurred_on": item.received_on,
                "memo": item.source_ref,
            }
        )
    for item in session.scalars(
        select(AllocationRecord).where(AllocationRecord.program_ref == program_ref)
    ):
        rows.append(
            {
                "record_type": "ALLOCATION",
                "id": item.external_id,
                "counterparty": item.purpose,
                "amount_minor": item.amount_minor,
                "currency": item.currency,
                "allocation_id": item.funding_ref,
            }
        )
    for item in session.scalars(
        select(FinancialTransactionRecord)
        .where(FinancialTransactionRecord.program_ref == program_ref)
        .order_by(FinancialTransactionRecord.occurred_on)
    ):
        rows.append(
            {
                "record_type": "PAYMENT",
                "id": item.external_id,
                "counterparty": item.payee_name,
                "amount_minor": item.amount_minor,
                "currency": item.currency,
                "occurred_on": item.occurred_on,
                "allocation_id": item.allocation_ref or "",
                # Exported as recorded. A payment nothing evidences leaves as UNMATCHED
                # rather than being quietly dropped from the file.
                "match_status": item.match_status,
                "memo": item.memo,
            }
        )
    for item in session.scalars(
        select(DeliveryRecord).where(DeliveryRecord.program_ref == program_ref)
    ):
        rows.append(
            {
                "record_type": "DELIVERY",
                "id": item.external_id,
                "counterparty": f"{item.quantity} x {item.item}",
                "occurred_on": item.delivered_on,
                "allocation_id": item.financial_transaction_ref or "",
            }
        )
    return _render(MONEY_TRAIL_COLUMNS, rows)


def outcomes_csv(session: Session, program_ref: str) -> str:
    """Outcomes and the method behind each, which is the qualification that travels."""
    return _render(
        OUTCOME_COLUMNS,
        (
            {
                "outcome_id": item.external_id,
                "program_id": item.program_ref,
                "metric": item.metric,
                "value": item.value,
                "unit": item.unit,
                "region": item.region,
                "method": item.method,
                "source": item.source,
                "confidence_percent": (
                    "" if item.confidence_percent is None else item.confidence_percent
                ),
            }
            for item in session.scalars(
                select(OutcomeRecord).where(OutcomeRecord.program_ref == program_ref)
            )
        ),
    )


def provenance_csv(session: Session, claim_id: str) -> str:
    """The edges this claim rests on, so the graph can be rebuilt by whoever holds it."""
    _, edges = claim_subgraph(session, claim_id)
    return _render(
        PROVENANCE_COLUMNS,
        (
            {
                "claim_id": claim_id,
                "source_type": edge.source_type,
                "source_id": edge.source_id,
                "relationship": edge.relationship,
                "target_type": edge.target_type,
                "target_id": edge.target_id,
                "confirmed_onchain": str(edge.confirmed_onchain).lower(),
            }
            for edge in sorted(edges, key=lambda item: (item.source_id, item.target_id))
        ),
    )


def evidence_csv(
    session: Session, claim_id: str, *, readable: Callable[[str], bool]
) -> str:
    """Evidence supporting a claim, with the commitment needed to check it.

    `readable` is the caller's own visibility check, passed in rather than reimplemented,
    because an export that decides for itself who may read what will drift away from the
    endpoint that decides it for everything else.
    """
    entity_ids, _ = claim_subgraph(session, claim_id)
    records = session.scalars(
        select(EvidenceRecord).where(EvidenceRecord.external_id.in_(entity_ids))
    )
    rows = []
    for item in records:
        if not readable(item.external_id):
            continue
        reference = (item.metadata_json or {}).get("blockchainReference") or {}
        rows.append(
            {
                "evidence_id": item.external_id,
                "type": item.evidence_type,
                "visibility": item.visibility,
                "workflow_status": item.workflow_status,
                "integrity_status": item.integrity_status,
                # The commitment, so a holder of this file can check the bytes themselves
                # against the registry rather than taking the status on trust.
                "content_hash": item.content_hash,
                "transaction_hash": reference.get("transactionHash", ""),
            }
        )
    return _render(EVIDENCE_COLUMNS, rows)


def claim_exists(session: Session, claim_id: str) -> bool:
    return (
        session.scalar(select(ClaimRecord).where(ClaimRecord.external_id == claim_id))
        is not None
    )
