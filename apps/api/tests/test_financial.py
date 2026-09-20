"""Financial ingestion, the allocation control, and reconciliation against real records."""

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from impactgraph.domain import Money, ReconciliationStatus
from impactgraph.evidence import MOCK_INVOICE_EXTRACTION, ReconciliationService
from impactgraph.financial import (
    EvidenceReconciliationService,
    FinancialIngestionService,
    FinancialLedger,
    MockFinancialDataProvider,
    ProviderTransaction,
    financial_summary,
)
from impactgraph.persistence import AllocationRecord, Base, FinancialTransactionRecord
from impactgraph.read_model import (
    ALLOCATION_ID,
    PROGRAM_ID,
    PROJECT_ID,
    seed_read_model,
)

PROGRAM = PROGRAM_ID


def factory(storage=None) -> sessionmaker[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    result = sessionmaker(engine, expire_on_commit=False)
    with result.begin() as session:
        seed_read_model(session, storage)
    return result


class OneOff:
    """A provider returning a single statement line, for controlling the scenario."""

    name = "test-bank"

    def __init__(self, *entries: ProviderTransaction) -> None:
        self.entries = list(entries)

    def fetch_statement(self, program_ref: str) -> list[ProviderTransaction]:
        return self.entries


def entry(source_ref: str, minor: int, memo: str = "", payee: str = "Aqua Systems Ltd."):
    return ProviderTransaction(
        source_ref=source_ref,
        payer_ref="org-global-water",
        payee_ref="vendor-aqua",
        payee_name=payee,
        amount=Money(minor, "USD"),
        occurred_on="2026-09-01",
        memo=memo,
    )


# --- ingestion ---------------------------------------------------------------------

def test_reimporting_a_statement_does_not_duplicate_payments(tmp_path):
    db = factory()
    service = FinancialIngestionService(MockFinancialDataProvider())
    with db.begin() as session:
        before = financial_summary(session, PROGRAM)["spent"]["amountMinor"]
        again = service.import_statement(session, program_ref=PROGRAM, allocation_ref=ALLOCATION_ID)
        after = financial_summary(session, PROGRAM)["spent"]["amountMinor"]
    assert again.imported == []
    assert len(again.skipped) == 3
    assert after == before, "a second import must not change the spend total"


def test_spend_beyond_the_allocation_is_rejected():
    db = factory()
    with db() as session:
        allocation = session.scalar(
            select(AllocationRecord).where(AllocationRecord.external_id == ALLOCATION_ID)
        )
        remaining = FinancialLedger.remaining(session, allocation).amount_minor
    over = FinancialIngestionService(OneOff(entry("over-1", remaining + 1, "too big")))
    with db.begin() as session:
        result = over.import_statement(session, program_ref=PROGRAM, allocation_ref=ALLOCATION_ID)
    assert result.imported == []
    assert result.rejected and "Exceeds the remaining allocation" in result.rejected[0]["reason"]
    with db() as session:
        assert (
            session.scalar(
                select(FinancialTransactionRecord).where(
                    FinancialTransactionRecord.source_ref == "over-1"
                )
            )
            is None
        )


def test_spend_up_to_the_allocation_is_accepted():
    db = factory()
    with db() as session:
        allocation = session.scalar(
            select(AllocationRecord).where(AllocationRecord.external_id == ALLOCATION_ID)
        )
        remaining = FinancialLedger.remaining(session, allocation).amount_minor
    exact = FinancialIngestionService(OneOff(entry("exact-1", remaining, "fills the allocation")))
    with db.begin() as session:
        result = exact.import_statement(session, program_ref=PROGRAM, allocation_ref=ALLOCATION_ID)
        assert result.imported
        assert FinancialLedger.remaining(session, allocation).amount_minor == 0


def test_the_ledger_balances():
    db = factory()
    with db() as session:
        summary = financial_summary(session, PROGRAM)
        assert (
            summary["committed"]["amountMinor"] + summary["uncommitted"]["amountMinor"]
            == summary["received"]["amountMinor"]
        )
        assert (
            summary["spent"]["amountMinor"] + summary["unspent"]["amountMinor"]
            == summary["committed"]["amountMinor"]
        )


# --- reconciliation ----------------------------------------------------------------

def reconcile(session, extraction):
    return EvidenceReconciliationService(ReconciliationService()).reconcile(
        session, project_ref=PROJECT_ID, extraction=extraction
    )


def test_an_invoice_reconciles_against_the_payment_resolved_from_the_ledger():
    db = factory()
    with db.begin() as session:
        result = reconcile(session, MOCK_INVOICE_EXTRACTION)
    assert result["status"] == ReconciliationStatus.PARTIAL_MATCH  # the GPS warning
    checks = {item["check"]: item["result"] for item in result["checks"]}
    assert checks["AMOUNT"] == "PASS"
    assert checks["TRANSACTION_REFERENCE"] == "PASS"
    assert checks["DELIVERY"] == "PASS"


def test_an_invoice_with_no_counterpart_payment_is_unmatched():
    db = factory()
    orphan = {**MOCK_INVOICE_EXTRACTION, "invoiceNumber": "INV-0000", "amountMinor": 111}
    with db.begin() as session:
        result = reconcile(session, orphan)
    # Distinct from CONFLICT: there is nothing to disagree with.
    assert result["status"] == ReconciliationStatus.UNMATCHED
    assert result["checks"][0]["check"] == "TRANSACTION_REFERENCE"


def test_an_invoice_contradicting_the_payment_it_cites_is_a_conflict():
    db = factory()
    # ftx-9184 also cites INV-8291, for a different amount. An invoice claiming that
    # amount with a different vendor must surface as CONFLICT, not vanish as UNMATCHED.
    contradictory = {
        **MOCK_INVOICE_EXTRACTION,
        "amountMinor": 96000,
        "vendor": "Someone Else Ltd.",
    }
    with db.begin() as session:
        result = reconcile(session, contradictory)
    assert result["status"] == ReconciliationStatus.CONFLICT
    failures = {item["check"] for item in result["checks"] if item["result"] == "FAIL"}
    assert "VENDOR" in failures


def test_reconciliation_records_its_verdict_on_the_payment():
    db = factory()
    with db.begin() as session:
        reconcile(session, MOCK_INVOICE_EXTRACTION)
    with db() as session:
        row = session.scalar(
            select(FinancialTransactionRecord).where(
                FinancialTransactionRecord.external_id == "ftx-9182"
            )
        )
        # The ledger shows which spend is evidenced, not just the evidence.
        assert row.match_status == "PARTIAL_MATCH"
        unevidenced = session.scalar(
            select(FinancialTransactionRecord).where(
                FinancialTransactionRecord.external_id == "ftx-9183"
            )
        )
        assert unevidenced.match_status == "UNMATCHED"


# --- money -------------------------------------------------------------------------

def test_money_refuses_the_mistakes_it_exists_to_prevent():
    with pytest.raises(TypeError):
        Money(42.0, "USD")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Money(100, "usd")
    with pytest.raises(ValueError):
        Money(1, "USD") + Money(1, "EUR")
