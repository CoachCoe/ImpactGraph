from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from .domain import Claim, ClaimStatus, PolicyDecision, PolicyRequirement, Result
from .persistence import (
    AttestationRecord,
    ClaimRecord,
    EvidenceRecord,
    ProgramRecord,
    ProvenanceEdgeRecord,
)

#: An attestation still stands unless it failed or is waiting for a signature.
ATTESTATION_STANDS = ("RECORDED", "CONFIRMED")


@dataclass(frozen=True)
class ConfirmedVerification:
    """One independent verifier attestation that is confirmed onchain."""

    issuer_id: str
    bundle_hash: str


@dataclass(frozen=True)
class VerificationContext:
    provenance_complete: bool
    evidence_registered: bool
    evidence_integrity: bool
    reconciliation_passes: bool
    operator_attestation_confirmed: bool
    verifications: tuple[ConfirmedVerification, ...]
    required_verifications: int
    current_bundle_hash: str
    operator_id: str

    @property
    def _current(self) -> tuple[ConfirmedVerification, ...]:
        """Attestations covering the evidence as it stands, from anyone."""
        if not self.current_bundle_hash:
            # An attestation whose bundle hash is also empty would otherwise compare equal
            # and count as covering a claim that has no bundle to cover.
            return ()
        return tuple(
            item for item in self.verifications if item.bundle_hash == self.current_bundle_hash
        )

    @property
    def qualifying_issuers(self) -> frozenset[str]:
        """Who independently verified the current bundle.

        A set, so one organisation attesting twice cannot make up the numbers, and the
        operator is excluded rather than counted and then objected to afterwards.
        """
        return frozenset(
            item.issuer_id for item in self._current if item.issuer_id != self.operator_id
        )

    @property
    def superseded_verifications(self) -> int:
        """Attestations that covered an earlier bundle, so are not counted."""
        return len(self.verifications) - len(self._current)

    @property
    def operator_verified(self) -> bool:
        return any(item.issuer_id == self.operator_id for item in self._current)


class VerificationPolicyService:
    version = "1.0"

    def evaluate(self, claim: Claim, context: VerificationContext) -> PolicyDecision:
        items = (
            self._require(
                "PROVENANCE_COMPLETE",
                context.provenance_complete,
                "Required provenance is complete",
                "Required provenance is incomplete",
            ),
            self._require(
                "EVIDENCE_REGISTERED",
                context.evidence_registered,
                "Required evidence commitments are confirmed onchain",
                "Required evidence is not confirmed onchain",
            ),
            self._require(
                "EVIDENCE_INTEGRITY",
                context.evidence_integrity,
                "Current evidence matches its commitments",
                "Evidence integrity check failed",
            ),
            self._require(
                "FINANCIAL_RECONCILIATION",
                context.reconciliation_passes,
                "Required deterministic reconciliation passed",
                "Financial reconciliation did not pass",
            ),
            self._require(
                "OPERATOR_ATTESTATION",
                context.operator_attestation_confirmed,
                # "Confirmed" would borrow the chain's word for a record that has never
                # been a chain signature, beside a requirement where it genuinely means one.
                "The operating organisation has attested this claim",
                "Required operator attestation is missing",
            ),
            self._require(
                "INDEPENDENT_VERIFICATION",
                len(context.qualifying_issuers) >= context.required_verifications,
                self._verification_count(context, "confirmed onchain"),
                self._verification_count(context, "confirmed onchain so far"),
            ),
            self._bundle_current(context),
            self._require(
                "ACTOR_SEPARATION",
                not context.operator_verified,
                "Every counted verifier is independent of the operator",
                "Operator cannot independently verify its own claim",
            ),
        )
        failed = any(item.status == Result.FAIL for item in items)
        if not failed:
            status = ClaimStatus.VERIFIED
        elif claim.status == ClaimStatus.VERIFIED:
            # A claim that was verified and now fails a requirement is challenged, not
            # verified. Falling back to claim.status here meant re-evaluation could raise
            # a status but never lower one, so a claim whose evidence had demonstrably
            # changed kept reporting VERIFIED.
            status = ClaimStatus.CHALLENGED
        else:
            status = claim.status
        return PolicyDecision(claim.id, status, self.version, items)

    @staticmethod
    def _bundle_current(context: VerificationContext) -> PolicyRequirement:
        """Whether the attestations being counted cover the evidence as it stands now.

        A stale attestation beside enough current ones is history, not an objection: it
        verified an earlier bundle and says nothing about this one. It is reported as a
        warning so a reader can see it, and does not block a claim that current verifiers
        have independently met the threshold on.
        """
        if not context.verifications:
            # Absent is not stale. This once read "Attestation does not cover the current
            # evidence bundle", a sentence asserting one exists and has gone out of date,
            # on the panel whose only job is to say accurately why a claim is not verified.
            return PolicyRequirement(
                "BUNDLE_CURRENT", Result.FAIL, "No independent attestation has been made yet"
            )
        if not context.qualifying_issuers:
            return PolicyRequirement(
                "BUNDLE_CURRENT",
                Result.FAIL,
                "No attestation covers the current evidence bundle",
            )
        if context.superseded_verifications:
            return PolicyRequirement(
                "BUNDLE_CURRENT",
                Result.WARNING,
                f"{context.superseded_verifications} earlier attestation(s) cover a "
                "previous evidence bundle and are not counted",
            )
        return PolicyRequirement(
            "BUNDLE_CURRENT",
            Result.PASS,
            "Every attestation is bound to the current verification bundle",
        )

    @staticmethod
    def _verification_count(context: VerificationContext, suffix: str) -> str:
        # The qualifying count, not the total: a reason reading "2 of 2" beside a FAIL,
        # because one of the two was stale or was the operator, is the panel contradicting
        # its own verdict.
        return (
            f"{len(context.qualifying_issuers)} of {context.required_verifications} required "
            f"independent verifications {suffix}"
        )

    @staticmethod
    def _require(name: str, passes: bool, success: str, failure: str) -> PolicyRequirement:
        return PolicyRequirement(
            name, Result.PASS if passes else Result.FAIL, success if passes else failure
        )


def claim_subgraph(
    session: Session, claim_id: str
) -> tuple[set[str], list[ProvenanceEdgeRecord]]:
    """Every entity reachable backwards from a claim, and the edges between them.

    Selecting all rows and then filtering by a set derived from those same rows is
    circular: it can only exclude what was already excluded. This walks incoming edges
    transitively from the claim, so an unrelated program's records are never in scope to
    begin with.
    """
    reachable = {claim_id}
    frontier = [claim_id]
    scoped: list[ProvenanceEdgeRecord] = []

    # One query per level rather than one for the whole table. The depth is the length of
    # the chain, not the size of the database, and these routes are public.
    while frontier:
        batch, frontier = frontier, []
        for edge in session.scalars(
            select(ProvenanceEdgeRecord).where(
                ProvenanceEdgeRecord.target_id.in_(batch),
                ProvenanceEdgeRecord.superseded_by.is_(None),
            )
        ):
            scoped.append(edge)
            if edge.source_id not in reachable:
                reachable.add(edge.source_id)
                frontier.append(edge.source_id)

    return reachable, scoped


def claim_provenance_complete(session: Session, claim_id: str) -> bool:
    """Validate the required connected path for one claim, never unrelated graph edges."""
    edges = list(session.scalars(select(ProvenanceEdgeRecord)))

    def sources(target_id: str, relationship: str, source_type: str) -> set[str]:
        return {
            edge.source_id
            for edge in edges
            if edge.target_id == target_id
            and edge.relationship == relationship
            and edge.source_type == source_type
            and edge.superseded_by is None
        }

    outcomes = sources(claim_id, "SUPPORTS", "OUTCOME")
    claim_evidence = sources(claim_id, "SUPPORTS", "EVIDENCE")
    if not outcomes or not claim_evidence:
        return False
    for outcome_id in outcomes:
        for delivery_id in sources(outcome_id, "PRODUCES", "DELIVERY"):
            delivery_evidence = sources(delivery_id, "EVIDENCES", "EVIDENCE")
            if not claim_evidence.intersection(delivery_evidence):
                continue
            for transaction_id in sources(delivery_id, "SUPPORTS", "FINANCIAL_TRANSACTION"):
                for allocation_id in sources(transaction_id, "PAYS", "ALLOCATION"):
                    if sources(allocation_id, "FUNDS", "FUNDING"):
                        return True
    return False


def evaluate_persisted_claim(
    session: Session, claim: ClaimRecord
) -> tuple[PolicyDecision, list[EvidenceRecord], list[AttestationRecord]]:
    """Build the sole persisted policy context used by reads and chain confirmation."""
    evidence_ids = list(
        session.scalars(
            select(ProvenanceEdgeRecord.source_id).where(
                ProvenanceEdgeRecord.source_type == "EVIDENCE",
                ProvenanceEdgeRecord.relationship == "SUPPORTS",
                ProvenanceEdgeRecord.target_id == claim.external_id,
                ProvenanceEdgeRecord.superseded_by.is_(None),
            )
        )
    )
    evidence = list(
        session.scalars(select(EvidenceRecord).where(EvidenceRecord.external_id.in_(evidence_ids)))
    )
    attestations = list(
        session.scalars(
            select(AttestationRecord).where(
                AttestationRecord.subject_id == claim.external_id,
                AttestationRecord.subject_type == "CLAIM",
                AttestationRecord.revoked_by_attestation_id.is_(None),
                AttestationRecord.status.in_(ATTESTATION_STANDS),
            )
        )
    )
    # Two different claims, so two different bars. An operator attestation is the
    # operating organisation putting its name to a claim inside this system; it is a
    # database record and has never been a chain signature. The independent verification
    # is the one this product asks a reader to trust a chain for, so nothing short of a
    # confirmed onchain attestation counts for it.
    operator = next((a for a in attestations if a.attestation_type == "OPERATOR"), None)
    verifiers = [
        a
        for a in attestations
        if a.attestation_type == "INDEPENDENT_VERIFIER" and a.status == "CONFIRMED"
    ]
    program = session.scalar(
        select(ProgramRecord).where(ProgramRecord.slug == claim.program_ref)
    )
    operator_id = program.operator_org_ref if program else ""
    context = VerificationContext(
        provenance_complete=claim_provenance_complete(session, claim.external_id),
        evidence_registered=bool(evidence)
        and len(evidence) == len(set(evidence_ids))
        and all(item.blockchain_status == "CONFIRMED" for item in evidence),
        evidence_integrity=bool(evidence)
        and all(item.integrity_status == "MATCH" for item in evidence),
        reconciliation_passes=bool(evidence)
        and all(
            item.reconciliation
            and item.reconciliation.get("status") in {"MATCHED", "PARTIAL_MATCH"}
            for item in evidence
        ),
        operator_attestation_confirmed=operator is not None,
        verifications=tuple(
            ConfirmedVerification(a.issuer_id, a.verification_bundle_hash) for a in verifiers
        ),
        required_verifications=max(1, program.verification_threshold if program else 1),
        current_bundle_hash=claim.verification_bundle_hash or "",
        operator_id=operator_id,
    )
    domain_claim = Claim(
        id=claim.external_id,
        program_id=claim.program_ref,
        project_id=claim.project_ref,
        statement=claim.statement,
        payload_hash=claim.payload_hash,
        status=ClaimStatus(claim.status),
        evidence_ids=evidence_ids,
        verification_bundle_hash=claim.verification_bundle_hash,
        verified_at=claim.verified_at,
        verification_policy_version=claim.verification_policy_version,
    )
    return VerificationPolicyService().evaluate(domain_claim, context), evidence, verifiers


class EvidenceScoreService:
    weights: ClassVar[dict[str, int]] = {
        "financialReconciliation": 25,
        "evidenceIntegrity": 20,
        "operatorAttestation": 15,
        "independentVerification": 25,
        "locationCorroboration": 10,
        "evidenceConsistency": 5,
    }

    def score(
        self,
        *,
        financial: bool,
        integrity: bool,
        operator: bool,
        verifier: bool,
        location_points: int,
        consistency: bool,
    ) -> dict:
        values = {
            "financialReconciliation": 25 if financial else 0,
            "evidenceIntegrity": 20 if integrity else 0,
            "operatorAttestation": 15 if operator else 0,
            "independentVerification": 25 if verifier else 0,
            "locationCorroboration": max(0, min(10, location_points)),
            "evidenceConsistency": 5 if consistency else 0,
        }
        return {
            "total": sum(values.values()),
            "components": [
                {"component": key, "score": value, "maximum": self.weights[key]}
                for key, value in values.items()
            ],
        }


def claims_supported_by(session: Session, evidence_id: str) -> list[ClaimRecord]:
    """Every claim whose verification rests on this evidence."""
    claim_ids = list(
        session.scalars(
            select(ProvenanceEdgeRecord.target_id).where(
                ProvenanceEdgeRecord.source_id == evidence_id,
                ProvenanceEdgeRecord.source_type == "EVIDENCE",
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


def restate_claims_for(
    session: Session, evidence_id: str, correlation_id: str = ""
) -> list[str]:
    """Re-decide the claims this evidence supports, and persist any status that fell.

    An integrity mismatch is detected on the evidence, but it is the claim that carries
    the trust signal a donor reads. Without this the claim kept its VERIFIED badge while
    the requirement list beneath it showed the integrity check failing.
    """
    from .notifications import enqueue_claim_status_change

    changed: list[str] = []
    for claim in claims_supported_by(session, evidence_id):
        decision, _, _ = evaluate_persisted_claim(session, claim)
        if decision.status.value != claim.status:
            claim.status = decision.status.value
            if decision.status != ClaimStatus.VERIFIED:
                claim.verified_at = None
            # In this transaction, not after it. A message about a status that then
            # rolled back has told someone something untrue and cannot be recalled.
            enqueue_claim_status_change(
                session,
                claim_id=claim.external_id,
                status=claim.status,
                correlation_id=correlation_id,
            )
            changed.append(claim.external_id)
    return changed
