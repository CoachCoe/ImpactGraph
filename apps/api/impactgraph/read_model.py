from __future__ import annotations

import hashlib
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from .config import Settings
from .demo import INVOICE_BYTES
from .domain import Money
from .evidence import (
    MOCK_INVOICE_EXTRACTION,
    EvidenceStorage,
    FileEvidenceStorage,
    ReconciliationService,
)
from .extraction import review_required_fields
from .financial import (
    EvidenceReconciliationService,
    FinancialIngestionService,
    FinancialLedger,
    MockFinancialDataProvider,
    financial_summary,
)
from .hashing import claim_hash, sha256_bytes, verification_bundle_hash
from .persistence import (
    AllocationRecord,
    AttestationRecord,
    AuditLogRecord,
    BlockchainOperationRecord,
    ClaimRecord,
    DeliveryRecord,
    DomainEntityRecord,
    EvidenceRecord,
    FinancialTransactionRecord,
    FundingRecord,
    IdempotencyRecord,
    OrganizationRecord,
    OutboxRecord,
    OutcomeRecord,
    ProcessedChainEventRecord,
    ProgramRecord,
    ProvenanceEdgeRecord,
)
from .verification import EvidenceScoreService, claim_subgraph, evaluate_persisted_claim

#: Not yet put forward by the operator who wrote them, so not listed to everyone. A claim
#: is still readable by identifier in any state; this governs enumeration only.
DRAFT_CLAIM_STATUSES = ("DRAFT", "EVIDENCE_PENDING", "CREATION_FAILED")

PROGRAM_ID = "program-clean-water-kenya-2026"
PROJECT_ID = "project-water-12"
CLAIM_ID = "claim-water-12-200"
OUTCOME_ID = "outcome-water-12-200"
EVIDENCE_ID = "ev-inv-8291"
CLAIM_STATEMENT = "200 households gained access to clean drinking water."
_INTEGRITY_DETAIL = {
    "MATCH": "Integrity confirmed",
    "MISMATCH": "Integrity check failed",
    "NOT_CHECKED": "Integrity not yet checked",
}
_UNKNOWN_INTEGRITY = "Integrity status unavailable"
FUNDING_ID = "funding-jane-10000"
ALLOCATION_ID = "allocation-water-12-8500"
FINANCIAL_TRANSACTION_ID = "ftx-9182"
DELIVERY_ID = "delivery-water-12"
OPERATOR_ORG_REF = "org-global-water"


def stable_uuid(value: str):
    return uuid5(NAMESPACE_URL, f"https://impactgraph.local/{value}")


def seed_read_model(session: Session, storage: EvidenceStorage | None = None) -> None:
    """Idempotently installs the deterministic showcase without deleting user records."""
    # Restore the showcase bytes before the early return below. Seeding is what makes the
    # demo repeatable, so it has to undo a tamper even when the rows are already present.
    evidence_store = storage or FileEvidenceStorage(Settings.from_env().evidence_storage_path)
    storage_uri = evidence_store.uri_for(EVIDENCE_ID)
    evidence_store.overwrite(storage_uri, INVOICE_BYTES)
    if session.scalar(select(ProgramRecord).where(ProgramRecord.slug == PROGRAM_ID)):
        return
    program_uuid = stable_uuid(PROGRAM_ID)
    session.add(
        ProgramRecord(
            id=program_uuid,
            slug=PROGRAM_ID,
            name="Clean Water Kenya 2026",
            operator_name="Global Water Initiative",
            operator_org_ref=OPERATOR_ORG_REF,
            region="Kisumu County, Kenya",
            status="ACTIVE",
            # `bootstrap-chain` creates this program's registry entity and waits for the
            # receipt, so by the time anything can reference it the chain has it. A program
            # created through the API starts PENDING and is confirmed by the worker.
            chain_status="CONFIRMED",
        )
    )
    # The models intentionally avoid broad ORM relationships; establish the FK parent
    # before adding graph entities so ordering is explicit and database-portable.
    session.flush()
    # Program and project summaries stay as presentation rows; the financial and delivery
    # entities below are real records with typed money, not JSON blobs.
    entities = [
        (
            PROGRAM_ID,
            "PROGRAM",
            {
                "filtrationSystems": 12,
                "peopleServed": 2840,
                "verificationPercent": 92,
            },
        ),
        (
            PROJECT_ID,
            "PROJECT",
            {"name": "Water Project #12", "deliveries": 1, "evidenceObjects": 5},
        ),
    ]
    for external_id, entity_type, data in entities:
        session.add(
            DomainEntityRecord(
                external_id=external_id,
                entity_type=entity_type,
                program_id=program_uuid,
                data=data,
                blockchain_status="CONFIRMED",
            )
        )

    session.add(
        FundingRecord(
            external_id=FUNDING_ID,
            program_ref=PROGRAM_ID,
            funder_name="Jane Smith",
            amount_minor=1000000,
            currency="USD",
            received_on="2026-07-02",
            source_ref="mock-bank-inbound-4471",
        )
    )
    session.add(
        FundingRecord(
            external_id="funding-institutional-90000",
            program_ref=PROGRAM_ID,
            funder_name="Institutional funding pool",
            amount_minor=9000000,
            currency="USD",
            received_on="2026-07-01",
            source_ref="mock-bank-inbound-4400",
        )
    )
    session.add(
        AllocationRecord(
            external_id=ALLOCATION_ID,
            program_ref=PROGRAM_ID,
            project_ref=PROJECT_ID,
            funding_ref=FUNDING_ID,
            purpose="Water Project #12",
            amount_minor=850000,
            currency="USD",
        )
    )
    session.add(
        AllocationRecord(
            external_id="allocation-program-operations-91500",
            program_ref=PROGRAM_ID,
            project_ref="program-portfolio-projects",
            funding_ref="funding-institutional-90000",
            purpose="Remaining Clean Water Kenya portfolio",
            amount_minor=9150000,
            currency="USD",
        )
    )
    session.flush()

    # Observed payments arrive through the provider rather than being written here.
    FinancialIngestionService(MockFinancialDataProvider()).import_statement(
        session, program_ref=PROGRAM_ID, allocation_ref=ALLOCATION_ID
    )
    # The two deliberately anomalous provider rows belong to the wider portfolio, not
    # Water Project #12. Keeping them visible exercises reconciliation states without
    # inflating the showcase project's $4,200 spend.
    for external_id in ("ftx-9183", "ftx-9184"):
        transaction = session.scalar(
            select(FinancialTransactionRecord).where(
                FinancialTransactionRecord.external_id == external_id
            )
        )
        if transaction:
            transaction.allocation_ref = "allocation-program-operations-91500"
    session.add(
        FinancialTransactionRecord(
            external_id="ftx-portfolio-deployment",
            program_ref=PROGRAM_ID,
            allocation_ref="allocation-program-operations-91500",
            payer_ref=OPERATOR_ORG_REF,
            payee_ref="portfolio-delivery-partners",
            payee_name="Clean Water Kenya delivery partners",
            amount_minor=8188000,
            currency="USD",
            occurred_on="2026-08-30",
            memo="Aggregated observed deployment across remaining projects",
            provider="mock-bank",
            source_ref="mock-bank-portfolio-deployment",
            match_status="MATCHED",
        )
    )

    session.add(
        DeliveryRecord(
            external_id=DELIVERY_ID,
            program_ref=PROGRAM_ID,
            project_ref=PROJECT_ID,
            financial_transaction_ref=FINANCIAL_TRANSACTION_ID,
            item="AquaPure X200",
            quantity=2,
            delivered_on="2026-08-21",
        )
    )
    session.add(
        OutcomeRecord(
            external_id=OUTCOME_ID,
            program_ref=PROGRAM_ID,
            delivery_ref=DELIVERY_ID,
            metric="Households with access to clean drinking water",
            value=200,
            unit="households",
            region="Kisumu County",
            # The programme is fictional, so the method says so. A line reading like a
            # real survey instrument would make the demonstration assert something nobody
            # measured, which is the failure every other fixture here is careful to avoid.
            method=(
                "Not measured. This programme is a demonstration fixture and the figure "
                "is illustrative; a real outcome would name its instrument, its sample "
                "and its window here."
            ),
            source="Seeded demonstration data",
            confidence_percent=None,
        )
    )
    session.flush()

    evidence_hash = sha256_bytes(INVOICE_BYTES)
    session.add(
        EvidenceRecord(
            external_id=EVIDENCE_ID,
            project_ref=PROJECT_ID,
            evidence_type="INVOICE",
            storage_uri=storage_uri,
            content_hash=evidence_hash,
            mime_type="text/plain",
            visibility="PUBLIC",
            workflow_status="SUBMITTED_FOR_VERIFICATION",
            analysis_status="COMPLETED",
            # Neither is something a seed can know. `impactgraph.cli seed` registers the
            # commitment when a registry is configured and records CONFIRMED from the real
            # receipt; the integrity endpoint is the only thing that may record a MATCH.
            integrity_status="NOT_CHECKED",
            blockchain_status="NOT_STARTED",
            metadata_json={
                "filename": "INV-8291.txt",
                "uploadedBy": "Global Water Initiative",
                "source": "Operator upload",
                "programId": PROGRAM_ID,
                "registeredContentHash": evidence_hash,
                # No blockchainReference: a fabricated transaction hash here made
                # registry-backed integrity verification fail against a real chain, and
                # asserted an onchain registration that never happened. `impactgraph.cli
                # seed` records a real one when a registry is configured.
            },
            extraction=dict(MOCK_INVOICE_EXTRACTION),
            # Reconciled against the imported payment and the recorded delivery, so the
            # seeded result is whatever the service actually produces.
            reconciliation=EvidenceReconciliationService(ReconciliationService()).reconcile(
                session,
                project_ref=PROJECT_ID,
                extraction=MOCK_INVOICE_EXTRACTION,
            ),
        )
    )
    payload_hash = claim_hash(CLAIM_ID, CLAIM_STATEMENT, OUTCOME_ID)
    bundle_hash = verification_bundle_hash(
        CLAIM_ID,
        payload_hash,
        [evidence_hash],
        OUTCOME_ID,
        ["funding-jane-10000", "allocation-water-12-8500", "ftx-9182", "delivery-water-12"],
        "1.0",
    )
    session.add(
        ClaimRecord(
            external_id=CLAIM_ID,
            program_ref=PROGRAM_ID,
            project_ref=PROJECT_ID,
            statement=CLAIM_STATEMENT,
            payload_hash=payload_hash,
            status="VERIFICATION_PENDING",
            verification_policy_version="1.0",
            verification_bundle_hash=bundle_hash,
        )
    )
    session.add(
        AttestationRecord(
            external_id="att-operator-water-12",
            attestation_type="OPERATOR",
            subject_type="CLAIM",
            subject_id=CLAIM_ID,
            issuer_id=OPERATOR_ORG_REF,
            # No wallet and no transaction hash, because neither exists. The operator
            # attestation is the operating organisation putting its name to the claim
            # inside this system -- a database record, never a chain signature. It used to
            # carry a placeholder wallet and hash and the status CONFIRMED, which asserted
            # an onchain signature that never happened, twelve lines above the comment
            # explaining why exactly that was removed from the evidence record below.
            issuer_wallet=None,
            statement_hash=payload_hash,
            verification_bundle_hash=bundle_hash,
            transaction_hash=None,
            status="RECORDED",
        )
    )
    edges = [
        ("FUNDING", "funding-jane-10000", "FUNDS", "ALLOCATION", "allocation-water-12-8500"),
        ("ALLOCATION", "allocation-water-12-8500", "PAYS", "FINANCIAL_TRANSACTION", "ftx-9182"),
        ("FINANCIAL_TRANSACTION", "ftx-9182", "SUPPORTS", "DELIVERY", "delivery-water-12"),
        ("EVIDENCE", EVIDENCE_ID, "EVIDENCES", "DELIVERY", "delivery-water-12"),
        ("DELIVERY", "delivery-water-12", "PRODUCES", "OUTCOME", OUTCOME_ID),
        ("OUTCOME", OUTCOME_ID, "SUPPORTS", "CLAIM", CLAIM_ID),
        ("EVIDENCE", EVIDENCE_ID, "SUPPORTS", "CLAIM", CLAIM_ID),
    ]
    for source_type, source_id, relationship, target_type, target_id in edges:
        session.add(
            ProvenanceEdgeRecord(
                source_type=source_type,
                source_id=source_id,
                relationship=relationship,
                target_type=target_type,
                target_id=target_id,
                # link_provenance has no caller: no edge in this system has ever been
                # written to a chain. Claiming otherwise made the donor-facing graph draw
                # every segment as onchain-attested on the strength of a seed literal.
                confirmed_onchain=False,
            )
        )


def reset_read_model(session: Session, storage: EvidenceStorage | None = None) -> None:
    """Delete only the explicitly selected local demo database, then deterministically seed."""
    for model in (
        ProcessedChainEventRecord,
        OutboxRecord,
        BlockchainOperationRecord,
        IdempotencyRecord,
        AuditLogRecord,
        ProvenanceEdgeRecord,
        AttestationRecord,
        ClaimRecord,
        EvidenceRecord,
        OutcomeRecord,
        DeliveryRecord,
        FinancialTransactionRecord,
        AllocationRecord,
        FundingRecord,
        DomainEntityRecord,
        ProgramRecord,
    ):
        session.execute(delete(model))
    session.flush()
    seed_read_model(session, storage)


def public_evidence_reference(external_id: str, visibility: str) -> str:
    """What to call a piece of evidence on a graph anyone can read.

    Provenance is public on purpose: a reader has to see that the chain is complete. The
    identifier is not neutral, though. The seeded record is `ev-inv-8291`, so the naming
    convention this system establishes puts the document's own number inside its id, and
    publishing that discloses the thing a restricted document is restricted for.

    The same opaque reference is used for the node, for both ends of every edge, and for
    the claim's evidence list, because a redaction applied to one surface and not the
    others is not a redaction.
    """
    if visibility == "PUBLIC":
        return external_id
    return "evidence-" + hashlib.sha256(external_id.encode("utf-8")).hexdigest()[:12]


def _evidence_title(item: EvidenceRecord, reference: str) -> str:
    if item.visibility != "PUBLIC":
        return "Restricted evidence"
    return str((item.extraction or {}).get("invoiceNumber") or reference)


class TransparencyReadRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def programs(self) -> list[dict[str, Any]]:
        return [
            self.program(record.slug)
            for record in self.session.scalars(select(ProgramRecord).order_by(ProgramRecord.name))
        ]

    def program(self, program_id: str) -> dict[str, Any]:
        record = self.session.scalar(select(ProgramRecord).where(ProgramRecord.slug == program_id))
        if record is None:
            raise LookupError("Program not found")
        metrics = self.session.scalar(
            select(DomainEntityRecord).where(DomainEntityRecord.external_id == program_id)
        )
        data = dict(metrics.data) if metrics else {}
        # Funded and deployed are derived from the ledger, not carried in a summary blob
        # that could disagree with the transactions it claims to total. The delivery and
        # outcome totals are derived for the same reason: the blob said 12 systems and
        # 2,840 people while the records it linked to said 2 and 200, and the dashboard
        # hyperlinked each figure to the record that contradicted it.
        data.pop("filtrationSystems", None)
        data.pop("peopleServed", None)
        # Nothing computes a verification percentage; serving one invites it to be rendered.
        data.pop("verificationPercent", None)
        ledger = financial_summary(self.session, program_id)
        delivered = self.session.scalar(
            select(func.coalesce(func.sum(DeliveryRecord.quantity), 0)).where(
                DeliveryRecord.program_ref == program_id
            )
        )
        served = self.session.scalar(
            select(func.coalesce(func.sum(OutcomeRecord.value), 0)).where(
                OutcomeRecord.program_ref == program_id
            )
        )
        return {
            "id": record.slug,
            "name": record.name,
            "operator": record.operator_name,
            "region": record.region,
            "status": record.status,
            "funding": ledger["received"],
            "deployed": ledger["spent"],
            "filtrationSystems": int(delivered or 0),
            "peopleServed": int(served or 0),
            **data,
            "featuredClaimId": CLAIM_ID,
        }

    def project(self, project_id: str) -> dict[str, Any]:
        record = self.session.scalar(
            select(DomainEntityRecord).where(
                DomainEntityRecord.external_id == project_id,
                DomainEntityRecord.entity_type == "PROJECT",
            )
        )
        if record is None:
            raise LookupError("Project not found")
        claim = self.session.scalar(
            select(ClaimRecord).where(ClaimRecord.project_ref == project_id)
        )
        allocation = FinancialLedger.allocation_for_project(self.session, project_id)
        return {
            "id": project_id,
            "programId": PROGRAM_ID,
            **record.data,
            "budget": (
                Money(allocation.amount_minor, allocation.currency).as_dict()
                if allocation
                else None
            ),
            "spent": (
                FinancialLedger.spent_against(
                    self.session, allocation.external_id, allocation.currency
                ).as_dict()
                if allocation
                else None
            ),
            "remaining": (
                FinancialLedger.remaining(self.session, allocation).as_dict()
                if allocation
                else None
            ),
            "verification": claim.status if claim else "DRAFT",
            "actions": ["IMPORT_FINANCIAL_STATEMENT", "RECORD_DELIVERY", "UPLOAD_EVIDENCE"],
        }

    def claims(
        self,
        status: str | None = None,
        program_id: str | None = None,
        operator_org_ref: str | None = None,
    ) -> list[dict[str, Any]]:
        """The claims themselves, so a workspace can show a queue rather than one constant.

        Summaries: a verifier picking work needs to know which claim and how far along it
        is, and the bundle they are about to sign is read separately once they choose it.

        `operator_org_ref` is the organisation whose drafts may be included. Anyone may
        read a claim by its identifier, which is the transparency this product exists for,
        but enumeration is not addressability: listing every claim would publish an
        organisation's unfinished statements the moment they were written, before any
        evidence supports them and before anyone chose to put them forward. So a claim
        appears to everyone only once it has been submitted for verification, and its own
        operator sees its drafts as well.
        """
        query = select(ClaimRecord).order_by(ClaimRecord.created_at)
        if operator_org_ref is None:
            query = query.where(ClaimRecord.status.notin_(DRAFT_CLAIM_STATUSES))
        else:
            owned = select(ProgramRecord.slug).where(
                ProgramRecord.operator_org_ref == operator_org_ref
            )
            query = query.where(
                or_(
                    ClaimRecord.status.notin_(DRAFT_CLAIM_STATUSES),
                    ClaimRecord.program_ref.in_(owned),
                )
            )
        if status:
            query = query.where(ClaimRecord.status == status)
        if program_id:
            query = query.where(ClaimRecord.program_ref == program_id)
        return [
            {
                "id": record.external_id,
                "programId": record.program_ref,
                "projectId": record.project_ref,
                "statement": record.statement,
                "status": record.status,
                "verifiedAt": record.verified_at.isoformat() if record.verified_at else None,
            }
            for record in self.session.scalars(query)
        ]

    def claim(self, claim_id: str) -> dict[str, Any]:
        record = self._claim(claim_id)
        attestations = list(
            self.session.scalars(
                select(AttestationRecord).where(AttestationRecord.subject_id == claim_id)
            )
        )
        organizations = {
            record.external_id: record.name
            for record in self.session.scalars(select(OrganizationRecord))
        }
        return {
            "id": record.external_id,
            "programId": record.program_ref,
            "projectId": record.project_ref,
            "statement": record.statement,
            "status": record.status,
            "payloadHash": record.payload_hash,
            "verificationBundleHash": record.verification_bundle_hash,
            "policyVersion": record.verification_policy_version,
            "verifiedAt": record.verified_at.isoformat() if record.verified_at else None,
            "evidenceIds": self._supporting_evidence_ids(record.external_id),
            "attestations": [
                {
                    "id": item.external_id,
                    "type": item.attestation_type,
                    # The real organisation, not a constant keyed on the type. Any
                    # verifier whatsoever was previously displayed as "ImpactVerify".
                    "issuer": organizations.get(item.issuer_id, item.issuer_id),
                    "wallet": item.issuer_wallet,
                    "status": item.status,
                    # Whether this was signed on a chain or recorded in this system.
                    # An operator attestation has never been a chain signature, and
                    # showing it beside one that is, identically, overstated it.
                    "onchain": item.transaction_hash is not None,
                    "transactionHash": item.transaction_hash,
                    "verificationBundleHash": item.verification_bundle_hash,
                }
                for item in attestations
            ],
        }

    def evidence(self, evidence_id: str) -> dict[str, Any]:
        record = self.session.scalar(
            select(EvidenceRecord).where(EvidenceRecord.external_id == evidence_id)
        )
        if record is None:
            raise LookupError("Evidence not found")
        return {
            "id": record.external_id,
            "type": record.evidence_type,
            "projectId": record.project_ref,
            "contentHash": record.content_hash,
            "mimeType": record.mime_type,
            "visibility": record.visibility,
            "workflowStatus": record.workflow_status,
            "analysisStatus": record.analysis_status,
            "integrityStatus": record.integrity_status,
            "blockchainStatus": record.blockchain_status,
            "extraction": record.extraction,
            # The same list the analysis response carries. Without it a client that
            # reloaded the page could no longer tell which fields /review will demand,
            # and would be refused with no way to know what to confirm.
            "reviewRequired": (
                review_required_fields(record.extraction) if record.extraction else []
            ),
            "reconciliation": record.reconciliation,
            **record.metadata_json,
        }

    def _supporting_evidence_ids(self, claim_id: str) -> list[str]:
        """The evidence actually linked to this claim.

        The showcase evidence id was returned for every claim; the same query the
        verification policy uses is the one that belongs here.
        """
        # Redacted like the graph is. This list is the third public surface the
        # identifier reached, and a redaction that misses one of them is not one.
        supporting = list(
            self.session.scalars(
                select(ProvenanceEdgeRecord.source_id).where(
                    ProvenanceEdgeRecord.source_type == "EVIDENCE",
                    ProvenanceEdgeRecord.relationship == "SUPPORTS",
                    ProvenanceEdgeRecord.target_id == claim_id,
                    ProvenanceEdgeRecord.superseded_by.is_(None),
                )
            )
        )
        if not supporting:
            return []
        visibilities = {
            item.external_id: item.visibility
            for item in self.session.scalars(
                select(EvidenceRecord).where(EvidenceRecord.external_id.in_(supporting))
            )
        }
        return [
            public_evidence_reference(item, visibilities.get(item, "RESTRICTED"))
            for item in supporting
        ]

    def _financial_nodes(self, entity_ids: set[str]) -> list[dict[str, Any]]:
        """Provenance nodes for the given entities, built from the typed records.

        Every select here was previously unscoped, so one claim's graph contained every
        program's funding, allocations, deliveries and outcomes.
        """

        def amount(row: Any) -> str:
            return f"{Money(row.amount_minor, row.currency).amount_minor / 100:,.2f} {row.currency}"

        nodes: list[dict[str, Any]] = []
        for row in self.session.scalars(
            select(FundingRecord).where(FundingRecord.external_id.in_(entity_ids))
        ):
            nodes.append(
                {
                    "id": row.external_id,
                    "type": "FUNDING",
                    "title": row.funder_name,
                    "detail": f"{amount(row)} contributed",
                }
            )
        for row in self.session.scalars(
            select(AllocationRecord).where(AllocationRecord.external_id.in_(entity_ids))
        ):
            nodes.append(
                {
                    "id": row.external_id,
                    "type": "ALLOCATION",
                    "title": row.purpose,
                    "detail": f"{amount(row)} allocated",
                }
            )
        for row in self.session.scalars(
            select(FinancialTransactionRecord).where(
                FinancialTransactionRecord.external_id.in_(entity_ids)
            )
        ):
            nodes.append(
                {
                    "id": row.external_id,
                    "type": "FINANCIAL_TRANSACTION",
                    "title": row.payee_name,
                    "detail": f"{amount(row)} observed payment",
                }
            )
        for row in self.session.scalars(
            select(DeliveryRecord).where(DeliveryRecord.external_id.in_(entity_ids))
        ):
            nodes.append(
                {
                    "id": row.external_id,
                    "type": "DELIVERY",
                    "title": f"{row.quantity} × {row.item}",
                    "detail": f"Delivered {row.delivered_on}",
                }
            )
        for row in self.session.scalars(
            select(OutcomeRecord).where(OutcomeRecord.external_id.in_(entity_ids))
        ):
            nodes.append(
                {
                    "id": row.external_id,
                    "type": "OUTCOME",
                    "title": f"{row.value} {row.unit} served",
                    "detail": row.region,
                    "method": row.method,
                    "source": row.source,
                    "confidencePercent": row.confidence_percent,
                }
            )
        return nodes

    def provenance(self, claim_id: str) -> dict[str, Any]:
        self._claim(claim_id)
        claim = self.claim(claim_id)
        entity_ids, scoped_edges = claim_subgraph(self.session, claim_id)
        nodes = self._financial_nodes(entity_ids)
        evidence_records = list(
            self.session.scalars(
                select(EvidenceRecord).where(EvidenceRecord.external_id.in_(entity_ids))
            )
        )
        # One mapping, applied to the nodes and to both ends of every edge below.
        references = {
            item.external_id: public_evidence_reference(item.external_id, item.visibility)
            for item in evidence_records
        }
        nodes.extend(
            {
                "id": references[item.external_id],
                "type": "EVIDENCE",
                # The real status, not a fixed "Integrity confirmed" label that would
                # keep reassuring a reader after the evidence stopped matching.
                "detail": _INTEGRITY_DETAIL.get(item.integrity_status, _UNKNOWN_INTEGRITY),
                "title": _evidence_title(item, references[item.external_id]),
            }
            for item in evidence_records
        )
        nodes.append(
            {
                "id": claim_id,
                "type": "CLAIM",
                "title": claim["statement"],
                "detail": claim["status"],
            }
        )
        edges = scoped_edges
        return {
            "nodes": nodes,
            "edges": [
                {
                    "source": references.get(edge.source_id, edge.source_id),
                    "relationship": edge.relationship,
                    "target": references.get(edge.target_id, edge.target_id),
                    "confirmedOnchain": edge.confirmed_onchain,
                }
                for edge in edges
            ],
        }

    def verification(self, claim_id: str) -> dict[str, Any]:
        claim = self._claim(claim_id)
        decision, evidence_records, verifiers = evaluate_persisted_claim(self.session, claim)
        operator = next(
            (item for item in decision.requirements if item.requirement == "OPERATOR_ATTESTATION"),
            None,
        )
        integrity = all(item.integrity_status == "MATCH" for item in evidence_records)
        score = EvidenceScoreService().score(
            financial=all(
                item.reconciliation
                and item.reconciliation.get("status") in {"MATCHED", "PARTIAL_MATCH"}
                for item in evidence_records
            ),
            integrity=bool(evidence_records) and integrity,
            operator=operator is not None and operator.status.value == "PASS",
            verifier=bool(verifiers),
            location_points=7,
            consistency=True,
        )
        return {
            "claimId": claim_id,
            "status": claim.status,
            "policyVersion": claim.verification_policy_version,
            "requirements": [
                {
                    "requirement": item.requirement,
                    "status": item.status.value,
                    "reason": item.reason,
                }
                for item in decision.requirements
            ],
            "evidenceScore": score,
            "verificationBundleHash": claim.verification_bundle_hash,
        }

    def _claim(self, claim_id: str) -> ClaimRecord:
        record = self.session.scalar(select(ClaimRecord).where(ClaimRecord.external_id == claim_id))
        if record is None:
            raise LookupError("Claim not found")
        return record
