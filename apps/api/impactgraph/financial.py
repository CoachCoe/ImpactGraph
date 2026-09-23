"""Financial data ingestion and reconciliation.

ImpactGraph never moves the program's money. It imports what a financial data provider
reports, records it as an observed payment, and checks that reported spend stays inside
what was allocated. Everything downstream -- provenance, evidence reconciliation, the
claim -- hangs off these records rather than off values written into a request handler.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from .domain import Money
from .persistence import (
    AllocationRecord,
    DeliveryRecord,
    FinancialTransactionRecord,
    FundingRecord,
    public_funder_name,
)


@dataclass(frozen=True)
class ProviderTransaction:
    """One payment as a provider reports it."""

    source_ref: str
    payer_ref: str
    payee_ref: str
    payee_name: str
    amount: Money
    occurred_on: str
    memo: str = ""


class FinancialDataProvider(Protocol):
    """A bank or payment provider feed.

    Deliberately read-only: ImpactGraph observes financial activity, it does not initiate
    it. A real adapter (Stripe, an open-banking aggregator, a CSV drop) implements this
    without anything downstream changing.
    """

    name: str

    def fetch_statement(self, program_ref: str) -> list[ProviderTransaction]: ...


class MockFinancialDataProvider:
    """Deterministic statement for the Clean Water Kenya showcase.

    It reports more than the happy path on purpose: one payment matches the invoice, one
    has no supporting evidence, and one contradicts the invoice it references. Without
    those, UNMATCHED and CONFLICT would be unreachable states that nothing could produce.
    """

    name = "mock-bank"

    def fetch_statement(self, program_ref: str) -> list[ProviderTransaction]:
        if program_ref != "program-clean-water-kenya-2026":
            return []
        return [
            ProviderTransaction(
                source_ref="mock-bank-9182",
                payer_ref="org-global-water",
                payee_ref="vendor-aqua",
                payee_name="Aqua Systems Ltd.",
                amount=Money(420000, "USD"),
                occurred_on="2026-08-17",
                memo="INV-8291 AquaPure X200 x2",
            ),
            ProviderTransaction(
                source_ref="mock-bank-9183",
                payer_ref="org-global-water",
                payee_ref="vendor-transit",
                payee_name="Kisumu Freight Co.",
                amount=Money(38000, "USD"),
                occurred_on="2026-08-19",
                memo="Delivery haulage, no invoice supplied",
            ),
            ProviderTransaction(
                source_ref="mock-bank-9184",
                payer_ref="org-global-water",
                payee_ref="vendor-aqua",
                payee_name="Aqua Systems Ltd.",
                amount=Money(96000, "USD"),
                occurred_on="2026-08-24",
                memo="INV-8291 supplementary charge",
            ),
        ]


@dataclass(frozen=True)
class ImportResult:
    imported: list[str]
    skipped: list[str]
    rejected: list[dict[str, str]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "imported": self.imported,
            "skipped": self.skipped,
            "rejected": self.rejected,
        }


class FinancialLedger:
    """Balance questions answered from the records, not from a cached total."""

    @staticmethod
    def allocation_for_project(session: Session, project_ref: str) -> AllocationRecord | None:
        return session.scalar(
            select(AllocationRecord).where(AllocationRecord.project_ref == project_ref)
        )

    @staticmethod
    def spent_against(session: Session, allocation_ref: str, currency: str) -> Money:
        rows = session.scalars(
            select(FinancialTransactionRecord).where(
                FinancialTransactionRecord.allocation_ref == allocation_ref
            )
        )
        total = Money.zero(currency)
        for row in rows:
            total = total + Money(row.amount_minor, row.currency)
        return total

    @classmethod
    def remaining(cls, session: Session, allocation: AllocationRecord) -> Money:
        allocated = Money(allocation.amount_minor, allocation.currency)
        return allocated - cls.spent_against(session, allocation.external_id, allocation.currency)


class FinancialIngestionService:
    """Imports provider statements into observed financial transactions."""

    def __init__(self, provider: FinancialDataProvider) -> None:
        self.provider = provider

    def import_statement(
        self,
        session: Session,
        *,
        program_ref: str,
        allocation_ref: str | None = None,
    ) -> ImportResult:
        imported: list[str] = []
        skipped: list[str] = []
        rejected: list[dict[str, str]] = []

        allocation = (
            session.scalar(
                select(AllocationRecord).where(AllocationRecord.external_id == allocation_ref)
            )
            if allocation_ref
            else session.scalar(
                select(AllocationRecord).where(AllocationRecord.program_ref == program_ref)
            )
        )

        for entry in self.provider.fetch_statement(program_ref):
            existing = session.scalar(
                select(FinancialTransactionRecord).where(
                    FinancialTransactionRecord.provider == self.provider.name,
                    FinancialTransactionRecord.source_ref == entry.source_ref,
                )
            )
            if existing is not None:
                # Re-importing a statement must not duplicate a payment.
                skipped.append(entry.source_ref)
                continue

            if allocation is not None:
                remaining = FinancialLedger.remaining(session, allocation)
                if entry.amount.amount_minor > remaining.amount_minor:
                    rejected.append(
                        {
                            "sourceRef": entry.source_ref,
                            "reason": f"Exceeds the remaining allocation of {remaining}",
                        }
                    )
                    continue

            external_id = f"ftx-{entry.source_ref.rsplit('-', 1)[-1]}"
            session.add(
                FinancialTransactionRecord(
                    external_id=external_id,
                    program_ref=program_ref,
                    allocation_ref=allocation.external_id if allocation else None,
                    payer_ref=entry.payer_ref,
                    payee_ref=entry.payee_ref,
                    payee_name=entry.payee_name,
                    amount_minor=entry.amount.amount_minor,
                    currency=entry.amount.currency,
                    occurred_on=entry.occurred_on,
                    memo=entry.memo,
                    provider=self.provider.name,
                    source_ref=entry.source_ref,
                    match_status="UNMATCHED",
                )
            )
            session.flush()
            imported.append(external_id)

        return ImportResult(imported, skipped, rejected)


def financial_summary(session: Session, program_ref: str) -> dict[str, Any]:
    """Where the money came from, what it was committed to, and where it went."""
    funding = list(
        session.scalars(select(FundingRecord).where(FundingRecord.program_ref == program_ref))
    )
    allocations = list(
        session.scalars(select(AllocationRecord).where(AllocationRecord.program_ref == program_ref))
    )
    transactions = list(
        session.scalars(
            select(FinancialTransactionRecord)
            .where(FinancialTransactionRecord.program_ref == program_ref)
            .order_by(FinancialTransactionRecord.occurred_on)
        )
    )
    rows = funding or allocations or transactions
    currency = rows[0].currency if rows else "USD"

    received = Money.zero(currency)
    for row in funding:
        received = received + Money(row.amount_minor, row.currency)
    committed = Money.zero(currency)
    for row in allocations:
        committed = committed + Money(row.amount_minor, row.currency)
    spent = Money.zero(currency)
    for row in transactions:
        spent = spent + Money(row.amount_minor, row.currency)

    counts: dict[str, int] = {}
    for row in transactions:
        counts[row.match_status] = counts.get(row.match_status, 0) + 1

    return {
        "programId": program_ref,
        "received": received.as_dict(),
        "committed": committed.as_dict(),
        "spent": spent.as_dict(),
        "uncommitted": (received - committed).as_dict(),
        "unspent": (committed - spent).as_dict(),
        "matchCounts": counts,
        "funding": [
            {
                "id": row.external_id,
                "funder": public_funder_name(row),
                "amount": Money(row.amount_minor, row.currency).as_dict(),
                "receivedOn": row.received_on,
                "sourceRef": row.source_ref,
            }
            for row in funding
        ],
        "allocations": [
            {
                "id": row.external_id,
                "projectId": row.project_ref,
                "purpose": row.purpose,
                "amount": Money(row.amount_minor, row.currency).as_dict(),
                "spent": FinancialLedger.spent_against(
                    session, row.external_id, row.currency
                ).as_dict(),
                "remaining": FinancialLedger.remaining(session, row).as_dict(),
            }
            for row in allocations
        ],
        "transactions": [transaction_response(row) for row in transactions],
        "generatedAt": datetime.now(UTC).isoformat(),
    }


def transaction_response(row: FinancialTransactionRecord) -> dict[str, Any]:
    return {
        "id": row.external_id,
        "programId": row.program_ref,
        "allocationId": row.allocation_ref,
        "payerRef": row.payer_ref,
        "payeeRef": row.payee_ref,
        "payee": row.payee_name,
        "amount": Money(row.amount_minor, row.currency).as_dict(),
        "occurredOn": row.occurred_on,
        "memo": row.memo,
        "provider": row.provider,
        "sourceRef": row.source_ref,
        "matchStatus": row.match_status,
    }



class EvidenceReconciliationService:
    """Resolves the payment and delivery an invoice claims, then reconciles against them.

    This used to live in the HTTP handler as six literals that happened to equal what the
    mock extractor returned, so every document reconciled against the same expectations
    and the comparison proved nothing.
    """

    def __init__(self, reconciler: Any) -> None:
        self.reconciler = reconciler

    @staticmethod
    def resolve_transaction(
        session: Session, project_ref: str, extraction: dict[str, Any]
    ) -> FinancialTransactionRecord | None:
        """Find the observed payment an invoice refers to.

        Preference order: a payment whose memo cites the invoice number *and* whose amount
        agrees; then any payment citing the invoice number, so a contradiction surfaces as
        CONFLICT rather than disappearing as UNMATCHED; then a payment to the same payee
        for the same amount.
        """
        allocation = FinancialLedger.allocation_for_project(session, project_ref)
        candidates = list(
            session.scalars(
                select(FinancialTransactionRecord).where(
                    FinancialTransactionRecord.allocation_ref
                    == (allocation.external_id if allocation else None)
                )
            )
        )
        invoice_number = str(extraction.get("invoiceNumber", "")).strip()
        amount = extraction.get("amountMinor")
        vendor = extraction.get("vendor")

        cited = [row for row in candidates if invoice_number and invoice_number in (row.memo or "")]
        for row in cited:
            if row.amount_minor == amount:
                return row
        if cited:
            return cited[0]
        for row in candidates:
            if row.payee_name == vendor and row.amount_minor == amount:
                return row
        return None

    @staticmethod
    def resolve_delivery(
        session: Session, transaction: FinancialTransactionRecord | None
    ) -> DeliveryRecord | None:
        if transaction is None:
            return None
        return session.scalar(
            select(DeliveryRecord).where(
                DeliveryRecord.financial_transaction_ref == transaction.external_id
            )
        )

    def reconcile(
        self,
        session: Session,
        *,
        project_ref: str,
        extraction: dict[str, Any],
        completion_report_present: bool = True,
        photos_have_gps: list[bool] | None = None,
    ) -> dict[str, Any]:
        transaction = self.resolve_transaction(session, project_ref, extraction)
        delivery = self.resolve_delivery(session, transaction)
        result = self.reconciler.reconcile_invoice(
            extraction,
            (
                {
                    "id": transaction.external_id,
                    "payee": transaction.payee_name,
                    "amountMinor": transaction.amount_minor,
                    "currency": transaction.currency,
                    "memo": transaction.memo,
                }
                if transaction
                else None
            ),
            (
                {
                    "id": delivery.external_id,
                    "projectId": delivery.project_ref,
                    "financialTransactionId": delivery.financial_transaction_ref,
                    "item": delivery.item,
                    "quantity": delivery.quantity,
                }
                if delivery
                else None
            ),
            completion_report_present,
            photos_have_gps if photos_have_gps is not None else [True, False],
        )
        # Record the outcome on the payment so the ledger shows which spend is evidenced.
        if transaction is not None:
            transaction.match_status = str(
                result["status"].value if hasattr(result["status"], "value") else result["status"]
            )
        return result
