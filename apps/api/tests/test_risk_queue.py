"""The queue, and the two things it must never do: cross a tenant, or decide anything.

Detection that reaches into another organisation's records would be a data breach dressed
as a safeguard, and a finding that changed a claim by itself would make this system the
author of an accusation rather than the thing that raised it.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from impactgraph.detection import perceptual_hash
from impactgraph.evidence import MOCK_INVOICE_EXTRACTION
from impactgraph.main import InvoiceExtraction
from impactgraph.persistence import (
    AuditLogRecord,
    ClaimRecord,
    EvidenceRecord,
    FinancialTransactionRecord,
    ProgramRecord,
    RiskFindingRecord,
)
from impactgraph.risk import (
    DispositionRefused,
    gather_invoices,
    open_findings,
    precision,
    record_disposition,
    scan,
)
from tests.support import memory_session


@pytest.fixture
def session():
    return memory_session()


def _program(session, slug: str, org: str) -> None:
    session.add(
        ProgramRecord(
            slug=slug,
            name=slug,
            operator_name=org,
            operator_org_ref=org,
            region="Region",
            status="ACTIVE",
        )
    )


def _claim(session, *, program: str, project: str) -> None:
    session.add(
        ClaimRecord(
            external_id=f"claim-{project}",
            program_ref=program,
            project_ref=project,
            statement="A delivery happened",
            payload_hash="sha256:" + "0" * 64,
            status="DRAFT",
            verification_policy_version="1.0.0",
        )
    )


def _invoice_evidence(session, *, ref: str, project: str, number: str, vendor="Acme Supplies"):
    """Built from the extraction the system actually produces, not a shape invented here.

    Deriving the fixture from `MOCK_INVOICE_EXTRACTION` means a change to the extraction's
    field names breaks these tests rather than quietly leaving the detector reading keys
    nothing writes.
    """
    extraction = dict(MOCK_INVOICE_EXTRACTION)
    extraction["invoiceNumber"] = number
    extraction["vendor"] = vendor
    session.add(
        EvidenceRecord(
            external_id=ref,
            project_ref=project,
            evidence_type="INVOICE",
            storage_uri=f"file://{ref}",
            content_hash=f"sha256:{ref:0>64}",
            mime_type="application/pdf",
            visibility="PRIVATE",
            workflow_status="UPLOADED",
            analysis_status="DONE",
            integrity_status="OK",
            blockchain_status="NOT_STARTED",
            metadata_json={},
            extraction=extraction,
        )
    )


def test_the_detector_reads_the_fields_the_extraction_actually_defines():
    """The review boundary and the detector have to agree on field names. They did not
    the first time: this reader was written against `vendorName`/`totalAmount`, which
    nothing produces, and a fixture written to match would have hidden it."""
    for field in ("vendor", "invoiceNumber", "amountMinor", "currency", "date"):
        assert field in MOCK_INVOICE_EXTRACTION
        assert field in InvoiceExtraction.model_fields


def test_an_extraction_the_system_produces_yields_usable_invoice_facts(session):
    with session.begin():
        _program(session, "prog-a", "org-water")
        _claim(session, program="prog-a", project="proj-a")
        _invoice_evidence(session, ref="ev1", project="proj-a", number="INV-8291")

    with session.begin():
        facts = gather_invoices(session, ["prog-a"])

    assert len(facts) == 1
    assert facts[0].invoice_number == "INV-8291"
    assert facts[0].vendor == "Acme Supplies"
    assert facts[0].total_minor == MOCK_INVOICE_EXTRACTION["amountMinor"]
    assert facts[0].currency == MOCK_INVOICE_EXTRACTION["currency"]
    assert facts[0].issued_on == MOCK_INVOICE_EXTRACTION["date"]


def test_the_same_invoice_in_two_programmes_of_one_organisation_is_found(session):
    with session.begin():
        _program(session, "prog-a", "org-water")
        _program(session, "prog-b", "org-water")
        _claim(session, program="prog-a", project="proj-a")
        _claim(session, program="prog-b", project="proj-b")
        _invoice_evidence(session, ref="ev1", project="proj-a", number="INV-900")
        _invoice_evidence(session, ref="ev2", project="proj-b", number="INV-900")

    with session.begin():
        findings = scan(session, "org-water")

    assert [f.kind for f in findings] == ["DUPLICATE_INVOICE"]
    assert findings[0].organization_ref == "org-water"


def test_detection_does_not_reach_across_organisations(session):
    """Two customers holding the same invoice number is a coincidence or a matter for a
    regulator. Neither is a reason to show one customer the other's records."""
    with session.begin():
        _program(session, "prog-a", "org-water")
        _program(session, "prog-b", "org-shelter")
        _claim(session, program="prog-a", project="proj-a")
        _claim(session, program="prog-b", project="proj-b")
        _invoice_evidence(session, ref="ev1", project="proj-a", number="INV-900")
        _invoice_evidence(session, ref="ev2", project="proj-b", number="INV-900")

    with session.begin():
        assert scan(session, "org-water") == []
    with session.begin():
        assert scan(session, "org-shelter") == []


def test_a_scan_must_name_the_organisation_it_is_scoped_to(session):
    """An empty scope would otherwise match every programme with no operator set."""
    with session.begin(), pytest.raises(ValueError):
        scan(session, "")


def test_a_finding_never_changes_a_claim(session):
    with session.begin():
        _program(session, "prog-a", "org-water")
        _program(session, "prog-b", "org-water")
        _claim(session, program="prog-a", project="proj-a")
        _claim(session, program="prog-b", project="proj-b")
        _invoice_evidence(session, ref="ev1", project="proj-a", number="INV-900")
        _invoice_evidence(session, ref="ev2", project="proj-b", number="INV-900")

    with session.begin():
        before = {c.external_id: c.status for c in session.scalars(select(ClaimRecord))}
        assert scan(session, "org-water")

    with session.begin():
        after = {c.external_id: c.status for c in session.scalars(select(ClaimRecord))}
    assert after == before


def test_rescanning_does_not_grow_the_queue(session):
    """A queue that refills with what a reviewer already dismissed is one they stop
    opening."""
    with session.begin():
        _program(session, "prog-a", "org-water")
        _program(session, "prog-b", "org-water")
        _claim(session, program="prog-a", project="proj-a")
        _claim(session, program="prog-b", project="proj-b")
        _invoice_evidence(session, ref="ev1", project="proj-a", number="INV-900")
        _invoice_evidence(session, ref="ev2", project="proj-b", number="INV-900")

    with session.begin():
        first = scan(session, "org-water")[0].external_id
    with session.begin():
        scan(session, "org-water")
    with session.begin():
        rows = list(session.scalars(select(RiskFindingRecord)))

    assert len(rows) == 1
    assert rows[0].external_id == first


def test_a_dismissed_finding_does_not_come_back_open(session):
    with session.begin():
        _program(session, "prog-a", "org-water")
        _program(session, "prog-b", "org-water")
        _claim(session, program="prog-a", project="proj-a")
        _claim(session, program="prog-b", project="proj-b")
        _invoice_evidence(session, ref="ev1", project="proj-a", number="INV-900")
        _invoice_evidence(session, ref="ev2", project="proj-b", number="INV-900")
    with session.begin():
        external_id = scan(session, "org-water")[0].external_id
    with session.begin():
        record_disposition(
            session,
            external_id=external_id,
            organization_ref="org-water",
            state="DISMISSED",
            actor_id="reviewer-1",
            note="Two funders split one order deliberately; the split is documented.",
        )

    with session.begin():
        scan(session, "org-water")
    with session.begin():
        assert open_findings(session, "org-water") == []
        assert session.scalar(
            select(RiskFindingRecord).where(RiskFindingRecord.external_id == external_id)
        ).state == "DISMISSED"


def test_a_finding_cannot_be_closed_without_a_reason(session):
    """A queue that can be emptied without saying why measures nothing, and the false
    positive rate this has to be judged on is exactly what those reasons are."""
    with session.begin():
        _program(session, "prog-a", "org-water")
        _program(session, "prog-b", "org-water")
        _claim(session, program="prog-a", project="proj-a")
        _claim(session, program="prog-b", project="proj-b")
        _invoice_evidence(session, ref="ev1", project="proj-a", number="INV-900")
        _invoice_evidence(session, ref="ev2", project="proj-b", number="INV-900")
    with session.begin():
        external_id = scan(session, "org-water")[0].external_id

    for state in ("CONFIRMED", "DISMISSED"):
        with session.begin(), pytest.raises(DispositionRefused):
            record_disposition(
                session,
                external_id=external_id,
                organization_ref="org-water",
                state=state,
                actor_id="reviewer-1",
                note="   ",
            )

    with session.begin():
        assert session.scalar(
            select(RiskFindingRecord).where(RiskFindingRecord.external_id == external_id)
        ).state == "OPEN"


def test_investigating_does_not_need_a_reason_yet(session):
    with session.begin():
        _program(session, "prog-a", "org-water")
        _program(session, "prog-b", "org-water")
        _claim(session, program="prog-a", project="proj-a")
        _claim(session, program="prog-b", project="proj-b")
        _invoice_evidence(session, ref="ev1", project="proj-a", number="INV-900")
        _invoice_evidence(session, ref="ev2", project="proj-b", number="INV-900")
    with session.begin():
        external_id = scan(session, "org-water")[0].external_id
    with session.begin():
        finding = record_disposition(
            session,
            external_id=external_id,
            organization_ref="org-water",
            state="INVESTIGATING",
            actor_id="reviewer-1",
        )
        assert finding.state == "INVESTIGATING"
        assert finding.closed_at is None


def test_another_organisation_cannot_dispose_of_a_finding(session):
    with session.begin():
        _program(session, "prog-a", "org-water")
        _program(session, "prog-b", "org-water")
        _claim(session, program="prog-a", project="proj-a")
        _claim(session, program="prog-b", project="proj-b")
        _invoice_evidence(session, ref="ev1", project="proj-a", number="INV-900")
        _invoice_evidence(session, ref="ev2", project="proj-b", number="INV-900")
    with session.begin():
        external_id = scan(session, "org-water")[0].external_id

    with session.begin(), pytest.raises(LookupError):
        record_disposition(
            session,
            external_id=external_id,
            organization_ref="org-shelter",
            state="DISMISSED",
            actor_id="intruder",
            note="nothing to see",
        )


def test_closing_a_finding_is_written_to_the_audit_log(session):
    with session.begin():
        _program(session, "prog-a", "org-water")
        _program(session, "prog-b", "org-water")
        _claim(session, program="prog-a", project="proj-a")
        _claim(session, program="prog-b", project="proj-b")
        _invoice_evidence(session, ref="ev1", project="proj-a", number="INV-900")
        _invoice_evidence(session, ref="ev2", project="proj-b", number="INV-900")
    with session.begin():
        external_id = scan(session, "org-water")[0].external_id
    with session.begin():
        record_disposition(
            session,
            external_id=external_id,
            organization_ref="org-water",
            state="CONFIRMED",
            actor_id="reviewer-1",
            note="Confirmed with the vendor: one invoice, billed twice.",
            correlation_id="corr-1",
        )

    with session.begin():
        entry = session.scalar(
            select(AuditLogRecord).where(AuditLogRecord.entity_type == "RISK_FINDING")
        )
    assert entry.action == "RISK_FINDING_CONFIRMED"
    assert entry.actor_id == "reviewer-1"


def test_a_reversed_payment_does_not_count_towards_concentration(session):
    """A reversed payment did not happen, so counting it would misstate the share."""
    with session.begin():
        _program(session, "prog-a", "org-water")
        for index in range(5):
            session.add(
                FinancialTransactionRecord(
                    external_id=f"tx-{index}",
                    program_ref="prog-a",
                    payer_ref="funder",
                    payee_ref="acme",
                    payee_name="Acme Supplies",
                    occurred_on="2026-03-01",
                    provider="mock",
                    source_ref=f"src-{index}",
                    amount_minor=1000,
                    currency="GBP",
                    settlement="REVERSED" if index == 0 else "SETTLED",
                )
            )
        session.add(
            FinancialTransactionRecord(
                external_id="tx-other",
                program_ref="prog-a",
                payer_ref="funder",
                payee_ref="other",
                payee_name="Other Ltd",
                occurred_on="2026-03-01",
                provider="mock",
                source_ref="src-other",
                amount_minor=1000,
                currency="GBP",
                settlement="SETTLED",
            )
        )

    with session.begin():
        findings = scan(session, "org-water")

    # Four settled payments to acme and one to other is five payments, at the minimum,
    # and 4000 of 5000 is 80% -- over the threshold. Counting the reversed payment would
    # have made it 5000 of 6000.
    assert [f.kind for f in findings] == ["VENDOR_CONCENTRATION"]
    assert findings[0].subjects["totalMinor"] == 5000
    assert findings[0].subjects["paidMinor"] == 4000


def test_a_reused_photograph_across_projects_is_found_through_the_scan(session):
    import io

    from PIL import Image

    image = Image.new("RGB", (64, 48), (120, 30, 200))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    digest = perceptual_hash(buffer.getvalue())

    with session.begin():
        _program(session, "prog-a", "org-water")
        _claim(session, program="prog-a", project="proj-a")
        _claim(session, program="prog-a", project="proj-b")
        for ref, project in (("ph1", "proj-a"), ("ph2", "proj-b")):
            session.add(
                EvidenceRecord(
                    external_id=ref,
                    project_ref=project,
                    evidence_type="PHOTO",
                    storage_uri=f"file://{ref}",
                    content_hash=f"sha256:{ref:0>64}",
                    mime_type="image/png",
                    visibility="PRIVATE",
                    workflow_status="UPLOADED",
                    analysis_status="DONE",
                    integrity_status="OK",
                    blockchain_status="NOT_STARTED",
                    metadata_json={},
                    perceptual_hash=digest,
                )
            )

    with session.begin():
        findings = scan(session, "org-water")

    assert [f.kind for f in findings] == ["REUSED_IMAGE"]
    assert findings[0].subjects["projects"] == ["proj-a", "proj-b"]


def _seed_finding(session, *, external_id: str, org: str, kind: str, state: str) -> None:
    session.add(
        RiskFindingRecord(
            external_id=external_id,
            organization_ref=org,
            kind=kind,
            explanation="Something looked wrong.",
            subjects={"evidence": ["ev1"]},
            state=state,
            disposition_note="Decided." if state in {"CONFIRMED", "DISMISSED"} else None,
        )
    )


def test_precision_is_measured_from_what_reviewers_decided(session):
    with session.begin():
        for index, state in enumerate(["CONFIRMED", "CONFIRMED", "CONFIRMED", "DISMISSED"]):
            _seed_finding(
                session, external_id=f"r-{index}", org="org-water",
                kind="DUPLICATE_INVOICE", state=state,
            )

    with session.begin():
        measured = precision(session, "org-water")

    assert measured["DUPLICATE_INVOICE"]["precision"] == 0.75
    assert measured["DUPLICATE_INVOICE"]["decided"] == 4


def test_an_undecided_finding_is_not_counted_as_either(session):
    """A finding nobody has looked at is not evidence the detector was right or wrong."""
    with session.begin():
        _seed_finding(session, external_id="r-a", org="org-water",
                      kind="REUSED_IMAGE", state="CONFIRMED")
        _seed_finding(session, external_id="r-b", org="org-water",
                      kind="REUSED_IMAGE", state="OPEN")
        _seed_finding(session, external_id="r-c", org="org-water",
                      kind="REUSED_IMAGE", state="INVESTIGATING")

    with session.begin():
        measured = precision(session, "org-water")

    assert measured["REUSED_IMAGE"]["decided"] == 1
    assert measured["REUSED_IMAGE"]["precision"] == 1.0


def test_a_detector_nobody_has_judged_has_no_precision_rather_than_zero(session):
    """0.0 would read as a detector that is always wrong."""
    with session.begin():
        _seed_finding(session, external_id="r-a", org="org-water",
                      kind="VENDOR_CONCENTRATION", state="OPEN")

    with session.begin():
        assert precision(session, "org-water") == {}


def test_precision_does_not_pool_organisations(session):
    with session.begin():
        _seed_finding(session, external_id="r-a", org="org-water",
                      kind="DUPLICATE_INVOICE", state="CONFIRMED")
        _seed_finding(session, external_id="r-b", org="org-shelter",
                      kind="DUPLICATE_INVOICE", state="DISMISSED")

    with session.begin():
        assert precision(session, "org-water")["DUPLICATE_INVOICE"]["precision"] == 1.0
        assert precision(session, "org-shelter")["DUPLICATE_INVOICE"]["precision"] == 0.0
