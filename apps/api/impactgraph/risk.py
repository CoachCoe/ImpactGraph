"""The queue of things that looked wrong, and what a person decided about each one.

Scoped to one operating organisation. Detection deliberately does not cross tenants: an
invoice that appears in two organisations is either a coincidence or a matter for a
regulator, and neither is a reason to show one customer another customer's records.

A scan is idempotent. Findings are keyed by what they are about, so running the scan
hourly does not grow the queue -- a queue that refills with what a reviewer already
dismissed is one they stop opening.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .detection import (
    Finding,
    ImageFacts,
    InvoiceFacts,
    PaymentFacts,
    duplicate_invoices,
    reused_images,
    vendor_concentration,
)
from .persistence import (
    AuditLogRecord,
    DomainEntityRecord,
    EvidenceRecord,
    FinancialTransactionRecord,
    ProgramRecord,
    RiskFindingRecord,
)

OPEN_STATES = frozenset({"OPEN", "INVESTIGATING"})
#: The two states that make a false positive rate measurable. Neither can be reached
#: without a reviewer writing down why.
CLOSED_STATES = frozenset({"CONFIRMED", "DISMISSED"})
VALID_STATES = OPEN_STATES | CLOSED_STATES


class DispositionRefused(ValueError):
    """A finding cannot be closed without a reason."""


class ScanTooLarge(RuntimeError):
    """More images than an interactive scan will compare in a reasonable time."""


#: Near-duplicate comparison is quadratic within each hash bucket, so it grows faster than
#: the portfolio does. Around this many images it stops being something to wait for in a
#: browser: run `impactgraph risk-scan` out of band instead, which has no ceiling.
INTERACTIVE_IMAGE_LIMIT = 20_000


def _finding_id(organization_ref: str, finding: Finding) -> str:
    """Stable for as long as the finding is about the same thing.

    Derived from the finding's key and never from its measurements, so a share that moves
    with each new payment, or a third copy of an already-duplicated invoice, updates the
    existing row rather than minting a second one beside a reviewer's decision.
    """
    material = json.dumps(
        {"org": organization_ref, "key": list(finding.key)},
        separators=(",", ":"),
    )
    return f"risk-{hashlib.sha256(material.encode()).hexdigest()[:32]}"


def _programs_for(session: Session, organization_ref: str) -> list[str]:
    if not organization_ref:
        raise ValueError("a scan must name the organisation it is scoped to")
    return list(
        session.scalars(
            select(ProgramRecord.slug).where(ProgramRecord.operator_org_ref == organization_ref)
        )
    )


def _projects_for(session: Session, program_refs: Sequence[str]) -> dict[str, str]:
    """Project identifier to the programme that owns it.

    Resolved through the project entity's `program_id`, which is the actual ownership
    edge. Reading it from claims instead would make evidence invisible to detection until
    somebody filed a claim against its project -- and the moment a duplicate is worth
    catching is the moment the second copy is uploaded, which is weeks earlier.
    """
    if not program_refs:
        return {}
    rows = session.execute(
        select(DomainEntityRecord.external_id, ProgramRecord.slug)
        .join(ProgramRecord, DomainEntityRecord.program_id == ProgramRecord.id)
        .where(
            DomainEntityRecord.entity_type == "PROJECT",
            ProgramRecord.slug.in_(program_refs),
        )
    )
    return {project_ref: program_ref for project_ref, program_ref in rows}


def gather_invoices(session: Session, program_refs: Sequence[str]) -> list[InvoiceFacts]:
    """Invoice facts from extractions, for the programmes named and no others.

    Reads the field names `InvoiceExtraction` defines. An extraction is only ever the
    model's reading of a scanned page, so a missing vendor or number means this object
    takes no part in duplicate detection rather than matching every other unreadable one.
    """
    projects = _projects_for(session, program_refs)
    if not projects:
        return []
    facts: list[InvoiceFacts] = []
    rows = session.scalars(select(EvidenceRecord).where(EvidenceRecord.project_ref.in_(projects)))
    for evidence in rows:
        extraction = evidence.extraction or {}
        vendor = extraction.get("vendor")
        number = extraction.get("invoiceNumber")
        if not vendor or not number:
            continue
        amount = extraction.get("amountMinor")
        facts.append(
            InvoiceFacts(
                evidence_ref=evidence.external_id,
                program_ref=projects[evidence.project_ref],
                vendor=str(vendor),
                invoice_number=str(number),
                # Already minor units: the review boundary takes an integer, so there is
                # no decimal here to round and no float to launder into the comparison.
                total_minor=(
                    amount if isinstance(amount, int) and not isinstance(amount, bool) else 0
                ),
                currency=str(extraction.get("currency") or ""),
                issued_on=str(extraction.get("date") or ""),
            )
        )
    return facts


def gather_images(session: Session, program_refs: Sequence[str]) -> list[ImageFacts]:
    projects = _projects_for(session, program_refs)
    if not projects:
        return []
    rows = session.scalars(
        select(EvidenceRecord).where(
            EvidenceRecord.project_ref.in_(projects),
            EvidenceRecord.perceptual_hash.is_not(None),
        )
    )
    return [
        ImageFacts(
            evidence_ref=evidence.external_id,
            project_ref=evidence.project_ref,
            content_hash=evidence.content_hash,
            perceptual_hash=evidence.perceptual_hash or "",
        )
        for evidence in rows
    ]


def gather_payments(session: Session, program_refs: Sequence[str]) -> list[PaymentFacts]:
    if not program_refs:
        return []
    rows = session.scalars(
        select(FinancialTransactionRecord).where(
            FinancialTransactionRecord.program_ref.in_(program_refs),
            # A reversed payment did not happen, so counting it would misstate the share.
            FinancialTransactionRecord.settlement != "REVERSED",
        )
    )
    return [
        PaymentFacts(
            transaction_ref=transaction.external_id,
            program_ref=transaction.program_ref,
            payee_ref=transaction.payee_ref,
            payee_name=transaction.payee_name,
            amount_minor=transaction.amount_minor,
            currency=transaction.currency,
        )
        for transaction in rows
    ]


def scan(
    session: Session, organization_ref: str, *, image_limit: int | None = None
) -> list[RiskFindingRecord]:
    """Run every detector over one organisation's records and post the results.

    Nothing here touches a claim. The queue is the whole output.
    """
    program_refs = _programs_for(session, organization_ref)
    images = gather_images(session, program_refs)
    if image_limit is not None and len(images) > image_limit:
        raise ScanTooLarge(
            f"{len(images)} images is past the {image_limit} an interactive scan will "
            f"compare. Run `impactgraph risk-scan` for this organisation instead."
        )
    findings = [
        *duplicate_invoices(gather_invoices(session, program_refs)),
        *reused_images(images),
        *vendor_concentration(gather_payments(session, program_refs)),
    ]

    by_id = {_finding_id(organization_ref, finding): finding for finding in findings}
    existing = {
        record.external_id: record
        for record in session.scalars(
            select(RiskFindingRecord).where(RiskFindingRecord.external_id.in_(by_id))
        )
    } if by_id else {}

    posted: list[RiskFindingRecord] = []
    for external_id, finding in by_id.items():
        record = existing.get(external_id)
        if record is not None:
            # The reading is refreshed; the reviewer's decision about it is theirs.
            record.explanation = finding.explanation
            record.subjects = finding.subjects
            posted.append(record)
            continue
        record = RiskFindingRecord(
            external_id=external_id,
            organization_ref=organization_ref,
            kind=finding.kind,
            explanation=finding.explanation,
            subjects=finding.subjects,
            state="OPEN",
        )
        session.add(record)
        posted.append(record)
    return posted


def open_findings(session: Session, organization_ref: str) -> list[RiskFindingRecord]:
    if not organization_ref:
        raise ValueError("findings are only ever read for a named organisation")
    return list(
        session.scalars(
            select(RiskFindingRecord).where(
                RiskFindingRecord.organization_ref == organization_ref,
                RiskFindingRecord.state.in_(OPEN_STATES),
            )
        )
    )


def record_disposition(
    session: Session,
    *,
    external_id: str,
    organization_ref: str,
    state: str,
    actor_id: str,
    note: str = "",
    correlation_id: str = "",
) -> RiskFindingRecord:
    """Move a finding along, and require a reason before it can be closed.

    A queue that can be emptied without saying why measures nothing, and the false
    positive rate this detection has to be judged on is exactly what those reasons are.
    """
    if state not in VALID_STATES:
        raise DispositionRefused(f"{state} is not a disposition")
    finding = session.scalar(
        select(RiskFindingRecord).where(
            RiskFindingRecord.external_id == external_id,
            # Scoped in the query rather than checked afterwards, so a wrong identifier
            # from another tenant is indistinguishable from one that does not exist.
            RiskFindingRecord.organization_ref == organization_ref,
        )
    )
    if finding is None:
        raise LookupError(external_id)
    if state in CLOSED_STATES and not note.strip():
        raise DispositionRefused("closing a finding requires a reason")
    if finding.state in CLOSED_STATES and state in OPEN_STATES and not note.strip():
        # Reopening is legitimate -- new evidence arrives -- but it moves the precision
        # figure, so it is a decision that gets written down like any other.
        raise DispositionRefused("reopening a decided finding requires a reason")

    finding.state = state
    finding.assigned_to = actor_id
    if note.strip():
        finding.disposition_note = note.strip()
    finding.closed_at = datetime.now(UTC) if state in CLOSED_STATES else None
    session.add(
        AuditLogRecord(
            actor_id=actor_id,
            action=f"RISK_FINDING_{state}",
            entity_type="RISK_FINDING",
            entity_id=external_id,
            metadata_json={"kind": finding.kind},
            correlation_id=correlation_id,
        )
    )
    return finding


def precision(session: Session, organization_ref: str) -> dict:
    """How often each detector was right, from what reviewers actually decided.

    The only honest measure available: a detector's precision is whatever the people
    reviewing its output say it is. Recall is not measurable here at all -- nothing in
    this system knows about the fraud it never surfaced -- and reporting a number that
    implied otherwise would be worse than reporting none.

    Open and investigating findings are excluded rather than counted as either. A finding
    nobody has decided about is not yet evidence of anything.
    """
    if not organization_ref:
        raise ValueError("a precision measure is only ever for a named organisation")
    rows = session.execute(
        select(RiskFindingRecord.kind, RiskFindingRecord.state).where(
            RiskFindingRecord.organization_ref == organization_ref,
            RiskFindingRecord.state.in_(CLOSED_STATES),
        )
    )
    tally: dict[str, dict[str, int]] = {}
    for kind, state in rows:
        counts = tally.setdefault(kind, {"confirmed": 0, "dismissed": 0})
        counts["confirmed" if state == "CONFIRMED" else "dismissed"] += 1

    measured = {}
    for kind, counts in sorted(tally.items()):
        decided = counts["confirmed"] + counts["dismissed"]
        measured[kind] = {
            "confirmed": counts["confirmed"],
            "dismissed": counts["dismissed"],
            "decided": decided,
            # None rather than zero when nothing has been decided: a detector nobody has
            # judged has no precision, and 0.0 would read as one that is always wrong.
            "precision": round(counts["confirmed"] / decided, 4) if decided else None,
        }
    return measured
