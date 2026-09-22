from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class EntityMixin:
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProgramRecord(EntityMixin, Base):
    __tablename__ = "programs"
    slug: Mapped[str] = mapped_column(String(120), unique=True)
    name: Mapped[str] = mapped_column(String(240))
    operator_name: Mapped[str] = mapped_column(String(240))
    # The organisation that operates this program. Separation of duties is decided
    # against this, not against a hardcoded identifier.
    operator_org_ref: Mapped[str] = mapped_column(String(160), default="")
    region: Mapped[str] = mapped_column(String(240))
    status: Mapped[str] = mapped_column(String(40))
    # How many distinct independent verifiers this program's claims require. One is what
    # every program did implicitly before the column existed.
    verification_threshold: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


class DomainEntityRecord(EntityMixin, Base):
    __tablename__ = "domain_entities"
    external_id: Mapped[str] = mapped_column(String(160), unique=True)
    entity_type: Mapped[str] = mapped_column(String(48), index=True)
    program_id: Mapped[UUID | None] = mapped_column(ForeignKey("programs.id"))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    blockchain_status: Mapped[str] = mapped_column(String(40), default="NOT_STARTED")


class EvidenceRecord(EntityMixin, Base):
    __tablename__ = "evidence"
    external_id: Mapped[str] = mapped_column(String(160), unique=True)
    project_ref: Mapped[str] = mapped_column(String(160))
    evidence_type: Mapped[str] = mapped_column(String(40))
    storage_uri: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(71), unique=True)
    mime_type: Mapped[str] = mapped_column(String(120))
    visibility: Mapped[str] = mapped_column(String(24))
    workflow_status: Mapped[str] = mapped_column(String(48))
    analysis_status: Mapped[str] = mapped_column(String(40))
    integrity_status: Mapped[str] = mapped_column(String(40))
    blockchain_status: Mapped[str] = mapped_column(String(40))
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    extraction: Mapped[dict | None] = mapped_column(JSON)
    reconciliation: Mapped[dict | None] = mapped_column(JSON)


class ClaimRecord(EntityMixin, Base):
    __tablename__ = "claims"
    external_id: Mapped[str] = mapped_column(String(160), unique=True)
    program_ref: Mapped[str] = mapped_column(String(160))
    project_ref: Mapped[str] = mapped_column(String(160))
    statement: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(71))
    status: Mapped[str] = mapped_column(String(48))
    verification_policy_version: Mapped[str] = mapped_column(String(20))
    verification_bundle_hash: Mapped[str | None] = mapped_column(String(71))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProvenanceEdgeRecord(EntityMixin, Base):
    __tablename__ = "provenance_edges"
    source_type: Mapped[str] = mapped_column(String(48))
    source_id: Mapped[str] = mapped_column(String(160))
    relationship: Mapped[str] = mapped_column(String(40))
    target_type: Mapped[str] = mapped_column(String(48))
    target_id: Mapped[str] = mapped_column(String(160))
    confirmed_onchain: Mapped[bool] = mapped_column(Boolean, default=False)
    superseded_by: Mapped[UUID | None] = mapped_column(ForeignKey("provenance_edges.id"))
    __table_args__ = (
        UniqueConstraint("source_id", "relationship", "target_id", name="uq_provenance_assertion"),
    )


class AttestationRecord(EntityMixin, Base):
    __tablename__ = "attestations"
    external_id: Mapped[str] = mapped_column(String(160), unique=True)
    attestation_type: Mapped[str] = mapped_column(String(48))
    subject_type: Mapped[str] = mapped_column(String(48))
    subject_id: Mapped[str] = mapped_column(String(160))
    issuer_id: Mapped[str] = mapped_column(String(160))
    # None when the attestation was recorded in the system rather than signed on chain.
    issuer_wallet: Mapped[str | None] = mapped_column(String(42))
    statement_hash: Mapped[str] = mapped_column(String(71))
    verification_bundle_hash: Mapped[str] = mapped_column(String(71))
    transaction_hash: Mapped[str | None] = mapped_column(String(66))
    status: Mapped[str] = mapped_column(String(32))
    revoked_by_attestation_id: Mapped[UUID | None] = mapped_column(ForeignKey("attestations.id"))


class BlockchainOperationRecord(EntityMixin, Base):
    __tablename__ = "blockchain_operations"
    entity_id: Mapped[str] = mapped_column(String(160), index=True)
    operation_type: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(40))
    expected_event: Mapped[str] = mapped_column(String(80))
    transaction_hash: Mapped[str | None] = mapped_column(String(66), index=True)
    chain_id: Mapped[int] = mapped_column(BigInteger)
    confirmations: Mapped[int] = mapped_column(Integer, default=0)
    correlation_id: Mapped[str] = mapped_column(String(80), index=True)
    error: Mapped[str | None] = mapped_column(Text)


class OutboxRecord(EntityMixin, Base):
    __tablename__ = "outbox"
    topic: Mapped[str] = mapped_column(String(80), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    correlation_id: Mapped[str] = mapped_column(String(80), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProcessedChainEventRecord(EntityMixin, Base):
    __tablename__ = "processed_chain_events"
    chain_id: Mapped[int] = mapped_column(BigInteger)
    transaction_hash: Mapped[str] = mapped_column(String(66))
    log_index: Mapped[int] = mapped_column(Integer)
    event_name: Mapped[str] = mapped_column(String(80))
    __table_args__ = (
        UniqueConstraint("chain_id", "transaction_hash", "log_index", name="uq_chain_event"),
    )


class IdempotencyRecord(EntityMixin, Base):
    __tablename__ = "idempotency_records"
    key: Mapped[str] = mapped_column(String(160), unique=True)
    operation: Mapped[str] = mapped_column(String(80))
    request_hash: Mapped[str] = mapped_column(String(71))
    response_status: Mapped[int] = mapped_column(Integer)
    response_body: Mapped[dict] = mapped_column(JSON)


class AuditLogRecord(EntityMixin, Base):
    __tablename__ = "audit_log"
    actor_id: Mapped[str] = mapped_column(String(160))
    action: Mapped[str] = mapped_column(String(100), index=True)
    entity_type: Mapped[str] = mapped_column(String(48))
    entity_id: Mapped[str] = mapped_column(String(160), index=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    correlation_id: Mapped[str] = mapped_column(String(80), index=True)


class OrganizationRecord(EntityMixin, Base):
    """An acting organisation. Application identity, distinct from blockchain identity."""

    __tablename__ = "organizations"
    external_id: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(40))  # OPERATOR | VERIFIER | ADMIN


class UserRecord(EntityMixin, Base):
    """A person who signs in. Their role is derived from their organisation membership.

    `wallet_address` is only trusted once proven: a verifier must sign a server-issued
    nonce before it is stored, so the address cannot simply be asserted by a client.
    """

    __tablename__ = "users"
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(Text)
    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id"), index=True
    )
    role: Mapped[str] = mapped_column(String(40))  # DONOR | OPERATOR | VERIFIER | ADMIN
    wallet_address: Mapped[str | None] = mapped_column(String(42), nullable=True)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)


class SessionRecord(EntityMixin, Base):
    """A signed-in session.

    Only the SHA-256 of the token is stored, so a database disclosure does not hand over
    usable sessions. Sessions are revocable, which a stateless token would not be -- and
    revocability matters for a system whose claim is auditability.
    """

    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class WalletChallengeRecord(EntityMixin, Base):
    """A one-time nonce a wallet must sign to prove it controls an address."""

    __tablename__ = "wallet_challenges"
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), index=True
    )
    nonce: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class MoneyMixin:
    """Integer minor units and an ISO currency. Never a float, never a single column."""

    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))


class FundingRecord(MoneyMixin, EntityMixin, Base):
    """Money received into a program from a funder."""

    __tablename__ = "funding"
    external_id: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    program_ref: Mapped[str] = mapped_column(String(160), index=True)
    funder_name: Mapped[str] = mapped_column(String(240))
    received_on: Mapped[str] = mapped_column(String(10))
    source_ref: Mapped[str] = mapped_column(String(160))


class AllocationRecord(MoneyMixin, EntityMixin, Base):
    """A commitment of funding to a project. The ceiling that spend is checked against."""

    __tablename__ = "allocations"
    external_id: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    program_ref: Mapped[str] = mapped_column(String(160), index=True)
    project_ref: Mapped[str] = mapped_column(String(160), index=True)
    funding_ref: Mapped[str] = mapped_column(String(160), index=True)
    purpose: Mapped[str] = mapped_column(String(240))


class FinancialTransactionRecord(MoneyMixin, EntityMixin, Base):
    """An observed payment, imported from a financial data provider.

    ImpactGraph does not move this money; it records that the provider reported it. The
    provider's own identifier is unique per provider so re-importing a statement is a
    no-op rather than a duplicate payment.
    """

    __tablename__ = "financial_transactions"
    __table_args__ = (
        UniqueConstraint("provider", "source_ref", name="uq_financial_transaction_source"),
    )
    external_id: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    program_ref: Mapped[str] = mapped_column(String(160), index=True)
    allocation_ref: Mapped[str | None] = mapped_column(String(160), index=True, nullable=True)
    payer_ref: Mapped[str] = mapped_column(String(160))
    payee_ref: Mapped[str] = mapped_column(String(160))
    payee_name: Mapped[str] = mapped_column(String(240))
    occurred_on: Mapped[str] = mapped_column(String(10), index=True)
    memo: Mapped[str] = mapped_column(String(400), default="")
    provider: Mapped[str] = mapped_column(String(80), index=True)
    source_ref: Mapped[str] = mapped_column(String(160))
    # MATCHED / PARTIAL_MATCH / UNMATCHED / CONFLICT, set by reconciliation against evidence.
    match_status: Mapped[str] = mapped_column(String(24), default="UNMATCHED", index=True)


class DeliveryRecord(EntityMixin, Base):
    """Goods or services recorded as delivered, supported by a financial transaction."""

    __tablename__ = "deliveries"
    external_id: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    program_ref: Mapped[str] = mapped_column(String(160), index=True)
    project_ref: Mapped[str] = mapped_column(String(160), index=True)
    financial_transaction_ref: Mapped[str | None] = mapped_column(
        String(160), index=True, nullable=True
    )
    item: Mapped[str] = mapped_column(String(240))
    quantity: Mapped[int] = mapped_column(Integer)
    delivered_on: Mapped[str] = mapped_column(String(10))


class OutcomeRecord(EntityMixin, Base):
    """A measured result attributed to a delivery."""

    __tablename__ = "outcomes"
    external_id: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    program_ref: Mapped[str] = mapped_column(String(160), index=True)
    delivery_ref: Mapped[str | None] = mapped_column(String(160), index=True, nullable=True)
    metric: Mapped[str] = mapped_column(String(160))
    value: Mapped[int] = mapped_column(Integer)
    unit: Mapped[str] = mapped_column(String(80))
    region: Mapped[str] = mapped_column(String(240), default="")
