from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class Role(StrEnum):
    DONOR = "DONOR"
    OPERATOR = "OPERATOR"
    VERIFIER = "VERIFIER"
    ADMIN = "ADMIN"


class EvidenceWorkflowStatus(StrEnum):
    UPLOADED = "UPLOADED"
    ANALYZING = "ANALYZING"
    ANALYZED = "ANALYZED"
    REVIEWED = "REVIEWED"
    REGISTRATION_PENDING = "REGISTRATION_PENDING"
    REGISTERED_ONCHAIN = "REGISTERED_ONCHAIN"
    SUBMITTED_FOR_VERIFICATION = "SUBMITTED_FOR_VERIFICATION"
    ANALYSIS_FAILED = "ANALYSIS_FAILED"
    REGISTRATION_FAILED = "REGISTRATION_FAILED"


class AnalysisStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class IntegrityStatus(StrEnum):
    NOT_CHECKED = "NOT_CHECKED"
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"


class BlockchainStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    CREATED = "CREATED"
    AWAITING_SIGNATURE = "AWAITING_SIGNATURE"
    SUBMITTED = "SUBMITTED"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    REPLACED = "REPLACED"
    DROPPED = "DROPPED"


class ClaimStatus(StrEnum):
    DRAFT = "DRAFT"
    EVIDENCE_PENDING = "EVIDENCE_PENDING"
    READY_FOR_VERIFICATION = "READY_FOR_VERIFICATION"
    VERIFICATION_PENDING = "VERIFICATION_PENDING"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    CHALLENGED = "CHALLENGED"
    SUPERSEDED = "SUPERSEDED"
    REVOKED = "REVOKED"


class Result(StrEnum):
    PASS = "PASS"
    WARNING = "WARNING"
    FAIL = "FAIL"


class ReconciliationStatus(StrEnum):
    MATCHED = "MATCHED"
    PARTIAL_MATCH = "PARTIAL_MATCH"
    UNMATCHED = "UNMATCHED"
    CONFLICT = "CONFLICT"


class Visibility(StrEnum):
    PUBLIC = "PUBLIC"
    RESTRICTED = "RESTRICTED"
    INTERNAL = "INTERNAL"


@dataclass(frozen=True)
class Money:
    """An amount of money.

    Integer minor units and an ISO currency, never a float: 4200.00 USD is 420000 and
    "USD". Binary floating point cannot represent most decimal amounts exactly, and money
    that is a fraction of a cent out is money that does not reconcile.
    """

    amount_minor: int
    currency: str

    def __post_init__(self) -> None:
        # bool is a subclass of int, and a float amount is the mistake this type exists
        # to prevent, so neither is accepted.
        if isinstance(self.amount_minor, bool) or not isinstance(self.amount_minor, int):
            raise TypeError("amount_minor must be an integer number of minor units")
        if self.amount_minor < 0:
            raise ValueError("amount_minor must not be negative")
        if len(self.currency) != 3 or not self.currency.isalpha() or not self.currency.isupper():
            raise ValueError("currency must be a three-letter uppercase ISO 4217 code")

    def __add__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(self.amount_minor + other.amount_minor, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(self.amount_minor - other.amount_minor, self.currency)

    def _same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise ValueError(
                f"Refusing to combine {self.currency} and {other.currency} without an "
                "explicit conversion and a recorded rate"
            )

    @classmethod
    def zero(cls, currency: str) -> Money:
        return cls(0, currency)

    def as_dict(self) -> dict[str, object]:
        return {"amountMinor": self.amount_minor, "currency": self.currency}

    def __str__(self) -> str:
        """For humans. Minor units are for storage and arithmetic, not for messages."""
        return f"{self.amount_minor / 100:,.2f} {self.currency}"


@dataclass
class Evidence:
    id: str
    project_id: str
    evidence_type: str
    storage_uri: str
    content_hash: str
    mime_type: str
    uploaded_by: str
    source: str
    visibility: Visibility = Visibility.RESTRICTED
    workflow_status: EvidenceWorkflowStatus = EvidenceWorkflowStatus.UPLOADED
    analysis_status: AnalysisStatus = AnalysisStatus.NOT_STARTED
    integrity_status: IntegrityStatus = IntegrityStatus.NOT_CHECKED
    blockchain_status: BlockchainStatus = BlockchainStatus.NOT_STARTED
    extraction: dict[str, Any] | None = None
    provider_metadata: dict[str, Any] | None = None
    reconciliation: dict[str, Any] | None = None
    blockchain_reference: dict[str, Any] | None = None
    uploaded_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class Claim:
    id: str
    program_id: str
    project_id: str
    statement: str
    payload_hash: str
    status: ClaimStatus = ClaimStatus.DRAFT
    evidence_ids: list[str] = field(default_factory=list)
    outcome_id: str = ""
    verification_bundle_hash: str | None = None
    verified_at: datetime | None = None
    verification_policy_version: str = "1.0"
    attestation_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PolicyRequirement:
    requirement: str
    status: Result
    reason: str


@dataclass(frozen=True)
class PolicyDecision:
    claim_id: str
    status: ClaimStatus
    policy_version: str
    requirements: tuple[PolicyRequirement, ...]

    @property
    def satisfied(self) -> bool:
        return all(item.status != Result.FAIL for item in self.requirements)


EVIDENCE_TRANSITIONS: dict[EvidenceWorkflowStatus, set[EvidenceWorkflowStatus]] = {
    EvidenceWorkflowStatus.UPLOADED: {EvidenceWorkflowStatus.ANALYZING},
    EvidenceWorkflowStatus.ANALYZING: {
        EvidenceWorkflowStatus.ANALYZED,
        EvidenceWorkflowStatus.ANALYSIS_FAILED,
    },
    EvidenceWorkflowStatus.ANALYSIS_FAILED: {EvidenceWorkflowStatus.ANALYZING},
    EvidenceWorkflowStatus.ANALYZED: {EvidenceWorkflowStatus.REVIEWED},
    EvidenceWorkflowStatus.REVIEWED: {EvidenceWorkflowStatus.REGISTRATION_PENDING},
    EvidenceWorkflowStatus.REGISTRATION_PENDING: {
        EvidenceWorkflowStatus.REGISTERED_ONCHAIN,
        EvidenceWorkflowStatus.REGISTRATION_FAILED,
    },
    EvidenceWorkflowStatus.REGISTRATION_FAILED: {EvidenceWorkflowStatus.REGISTRATION_PENDING},
    EvidenceWorkflowStatus.REGISTERED_ONCHAIN: {EvidenceWorkflowStatus.SUBMITTED_FOR_VERIFICATION},
    EvidenceWorkflowStatus.SUBMITTED_FOR_VERIFICATION: set(),
}


def transition_evidence(evidence: Evidence, target: EvidenceWorkflowStatus) -> None:
    if target not in EVIDENCE_TRANSITIONS[evidence.workflow_status]:
        raise ValueError(f"Invalid evidence transition {evidence.workflow_status} -> {target}")
    evidence.workflow_status = target


CLAIM_TRANSITIONS: dict[ClaimStatus, set[ClaimStatus]] = {
    ClaimStatus.DRAFT: {ClaimStatus.EVIDENCE_PENDING},
    ClaimStatus.EVIDENCE_PENDING: {ClaimStatus.READY_FOR_VERIFICATION, ClaimStatus.REJECTED},
    ClaimStatus.READY_FOR_VERIFICATION: {ClaimStatus.VERIFICATION_PENDING, ClaimStatus.REJECTED},
    ClaimStatus.VERIFICATION_PENDING: {ClaimStatus.VERIFIED, ClaimStatus.REJECTED},
    ClaimStatus.VERIFIED: {ClaimStatus.CHALLENGED, ClaimStatus.REVOKED, ClaimStatus.SUPERSEDED},
    ClaimStatus.CHALLENGED: {ClaimStatus.VERIFIED, ClaimStatus.REVOKED, ClaimStatus.SUPERSEDED},
    ClaimStatus.REJECTED: {ClaimStatus.SUPERSEDED},
    ClaimStatus.SUPERSEDED: set(),
    ClaimStatus.REVOKED: set(),
}


def transition_claim(claim: Claim, target: ClaimStatus) -> None:
    if target not in CLAIM_TRANSITIONS[claim.status]:
        raise ValueError(f"Invalid claim transition {claim.status} -> {target}")
    claim.status = target
