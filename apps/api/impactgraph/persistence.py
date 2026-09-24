from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
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
    operator_org_ref: Mapped[str] = mapped_column(String(160), default="", index=True)
    region: Mapped[str] = mapped_column(String(240))
    status: Mapped[str] = mapped_column(String(40))
    # Whether the registry entity exists. `registerEvidence` reverts with UnknownProgram
    # without it, so evidence filed against a program still waiting for its receipt would
    # fail in the worker rather than at the point the operator could do something about it.
    chain_status: Mapped[str] = mapped_column(
        String(40), default="NOT_STARTED", server_default="NOT_STARTED"
    )
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
    #: Visually similar, not byte-identical. The content hash proves bytes are unchanged;
    #: this finds the same photograph re-encoded or re-cropped for another delivery. They
    #: answer different questions and both are needed.
    perceptual_hash: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    #: Declared by the operator uploading it, because nothing else can know. Registration
    #: is refused until such an object has a data protection record naming a basis.
    personal_data: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
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
    #: When this claim was published as a public proof. Opt in, and once set it stays
    #: set: a page that can be withdrawn when the verdict turns inconvenient is not a
    #: record of anything.
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
    # The worker polls `processed_at IS NULL ORDER BY created_at` on every tick, and the
    # metrics scrape asks the same question. Partial rather than a plain index on
    # processed_at: only unsubmitted rows are ever looked for, so the index stays the size
    # of the backlog rather than the size of everything the system has ever sent.
    __table_args__ = (
        Index(
            "ix_outbox_pending",
            "created_at",
            postgresql_where=text("processed_at IS NULL"),
            sqlite_where=text("processed_at IS NULL"),
        ),
    )


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


class ProviderCredentialRecord(EntityMixin, Base):
    """A bank connection held on an organisation's behalf.

    The first real secret this platform keeps for a customer. Encrypted with the same
    key-encryption key that protects evidence at rest, so there is one key to rotate and
    one place to lose rather than two.

    Read-only scopes only. ADR-014 says this system observes money and does not move it,
    and a credential that could move it would make that promise a matter of restraint
    rather than of capability.
    """

    __tablename__ = "provider_credentials"
    organization_ref: Mapped[str] = mapped_column(String(160), index=True)
    provider: Mapped[str] = mapped_column(String(80), index=True)
    #: Nonce-prefixed ciphertext of the refresh token. Never the token.
    sealed_refresh_token: Mapped[bytes] = mapped_column(LargeBinary)
    #: What the provider granted, recorded so a credential that should not be able to move
    #: money can be shown not to be able to, rather than assumed.
    scopes: Mapped[str] = mapped_column(String(400), default="")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: When it was last exchanged, so a stale connection is visible rather than silent.
    refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "organization_ref", "provider", name="uq_provider_credential_org_provider"
        ),
    )


class BeneficiaryContactRecord(EntityMixin, Base):
    """Someone a delivery is meant to have reached, held as little as possible.

    A list of aid recipients tied to deliveries is a targeting aid in a conflict or a
    hostile administrative setting -- displacement, household composition and receipt of
    assistance are all inferable from it. So the number is sealed, the lookup is by salted
    hash, and no route returns either.

    Enrolment is separate from delivery in time on purpose. An operator who controls both
    at the same moment controls who gets asked.
    """

    __tablename__ = "beneficiary_contacts"
    program_ref: Mapped[str] = mapped_column(String(160), index=True)
    #: Salted hash of the phone number. Deduplicates and rate-limits without a directory.
    contact_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    #: Sealed under the same key as evidence, readable only by the dispatch process.
    sealed_contact: Mapped[bytes] = mapped_column(LargeBinary)
    #: Not a name. A reference the operating organisation can resolve if it must, which
    #: this system deliberately cannot.
    household_ref: Mapped[str] = mapped_column(String(160), default="")
    language: Mapped[str] = mapped_column(String(8), default="en")
    #: Asked to stop. Honoured before anything else is considered.
    opted_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConfirmationRoundRecord(EntityMixin, Base):
    """One attempt to ask a sample of people whether a delivery reached them.

    The seed and the method are stored so a third party can re-derive exactly who was
    selected. Without that, "we sampled randomly" is an assertion by the organisation
    being checked.
    """

    __tablename__ = "confirmation_rounds"
    external_id: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    delivery_ref: Mapped[str] = mapped_column(String(160), index=True)
    program_ref: Mapped[str] = mapped_column(String(160), index=True)
    seed: Mapped[str] = mapped_column(String(160))
    method: Mapped[str] = mapped_column(Text)
    population: Mapped[int] = mapped_column(Integer)
    sample_size: Mapped[int] = mapped_column(Integer)
    #: Set when dispatch actually happened, which requires the safeguarding review.
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConfirmationResponseRecord(EntityMixin, Base):
    """One answer, stored so that it cannot be attributed back to a person by anyone
    using this system.

    The contact hash links a response to a round for deduplication and nothing else. No
    API returns a response row; only counts, and only above a sample size at which a
    dissenter cannot be identified by elimination.
    """

    __tablename__ = "confirmation_responses"
    __table_args__ = (
        UniqueConstraint("round_ref", "contact_hash", name="uq_confirmation_one_per_person"),
    )
    round_ref: Mapped[str] = mapped_column(String(160), index=True)
    contact_hash: Mapped[str] = mapped_column(String(64), index=True)
    #: CONFIRMED / DISPUTED / NO_ANSWER. Not free text: a structured answer can be counted
    #: without being read, and a free-text one cannot be published without exposing
    #: whoever wrote it.
    answer: Mapped[str] = mapped_column(String(16), index=True)
    responded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RiskFindingRecord(EntityMixin, Base):
    """Something that looks wrong, for a person to decide about.

    A finding never changes a claim's status. A false fraud accusation against an NGO is
    a serious harm, and an automated one is a harm the system caused on its own.

    Every finding carries its explanation and the records behind it, because an
    unexplained score is not actionable and gets ignored -- which is worse than no queue,
    since it trains reviewers that the queue is noise.
    """

    __tablename__ = "risk_findings"
    external_id: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    #: Scoped so detection never reaches across tenants.
    organization_ref: Mapped[str] = mapped_column(String(160), index=True)
    #: DUPLICATE_INVOICE / REUSED_IMAGE / VENDOR_CONCENTRATION.
    kind: Mapped[str] = mapped_column(String(48), index=True)
    #: Plain language, written for the person who has to act on it.
    explanation: Mapped[str] = mapped_column(Text)
    #: The identifiers the finding rests on, so a reviewer can go and look.
    subjects: Mapped[dict] = mapped_column(JSON, default=dict)
    #: OPEN / INVESTIGATING / CONFIRMED / DISMISSED. The last two are the dispositions
    #: that make a false positive rate measurable.
    state: Mapped[str] = mapped_column(String(24), default="OPEN", index=True)
    assigned_to: Mapped[str | None] = mapped_column(String(160), nullable=True)
    #: Why a reviewer decided what they decided. Required to close one, because a queue
    #: that can be emptied without saying why measures nothing.
    disposition_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DataProtectionRecord(EntityMixin, Base):
    """What makes holding one evidence object lawful, and who is answerable for it.

    Deliberately not a consent record. Dynamic Aid operates its own programmes and
    delivers the aid, so consent obtained from someone receiving that aid is not freely
    given and is therefore not valid consent -- and the basis for ordinary programme
    imagery is more likely to be legitimate interest with an unconditional objection
    route. Special-category data is the exception: legitimate interest is not available
    for it under Article 9, which is why `special_category` constrains the basis.

    The controller is stored per object rather than assumed, because it is not constant.
    Dynamic Aid is the controller for programmes it runs, a joint controller for ones it
    co-builds with a partner, and would become a processor for organisations it handed
    the platform to.
    """

    __tablename__ = "data_protection_records"
    evidence_ref: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    #: Article 6 basis. Named rather than assumed so the answer can differ per object and
    #: can be changed without a migration when counsel settles it.
    lawful_basis: Mapped[str] = mapped_column(String(40))
    #: Article 9 data -- health, vulnerability. Legitimate interest cannot carry it.
    special_category: Mapped[bool] = mapped_column(Boolean, default=False)
    controller_org_ref: Mapped[str] = mapped_column(String(160), index=True)
    #: Set where a programme is co-built, which is joint controllership rather than one
    #: organisation acting for another.
    joint_controller_org_ref: Mapped[str | None] = mapped_column(String(160), nullable=True)
    #: Who the data is about, by a reference this system can resolve rather than a name.
    subject_reference: Mapped[str] = mapped_column(String(160), index=True)
    purpose: Mapped[str] = mapped_column(Text)
    captured_by: Mapped[str] = mapped_column(String(160))
    #: Retention schedule. Past this the object is erased whether or not anyone asked.
    retain_until: Mapped[str | None] = mapped_column(String(10), nullable=True)
    #: Consent withdrawn, or legitimate interest objected to. One field: what the person
    #: did differs by basis, what this system must then do does not.
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: When the key was destroyed. Separate from withdrawn_at because a withdrawal that
    #: was recorded and never acted on is the failure this column exists to expose.
    erased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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


class NotificationSubscriptionRecord(EntityMixin, Base):
    """Someone who asked to be told when a claim stops being true.

    Deliberately not a user. Following a claim must not require an account, so this holds
    an email address and a token whose hash is all that is stored -- the same shape as a
    session, for the same reason: the link in the message is the credential.
    """

    __tablename__ = "notification_subscriptions"
    __table_args__ = (
        UniqueConstraint("email", "claim_id", name="uq_subscription_email_claim"),
    )
    email: Mapped[str] = mapped_column(String(320), index=True)
    claim_id: Mapped[str] = mapped_column(String(160), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # Nothing is sent to an address that has not answered the first message. Otherwise
    # this endpoint is a way to mail anyone, repeatedly, from someone else's domain.
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unsubscribed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NotificationDeliveryRecord(EntityMixin, Base):
    """What was sent, to whom, about which change.

    Recorded because "we told you on this date" is the sort of assertion this system
    makes about everything else, and because the uniqueness constraint is what stops a
    retry sending the same news twice.
    """

    __tablename__ = "notification_deliveries"
    __table_args__ = (
        UniqueConstraint("subscription_id", "event_key", name="uq_delivery_once"),
    )
    subscription_id: Mapped[UUID] = mapped_column(
        ForeignKey("notification_subscriptions.id"), index=True
    )
    event_key: Mapped[str] = mapped_column(String(200), index=True)
    event_type: Mapped[str] = mapped_column(String(48))
    claim_id: Mapped[str] = mapped_column(String(160), index=True)
    transport: Mapped[str] = mapped_column(String(48))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(String(200))


class MoneyMixin:
    """Integer minor units and an ISO currency. Never a float, never a single column."""

    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))


class FundingRecord(MoneyMixin, EntityMixin, Base):
    """Money received by an organisation, and the programme it was assigned to if any.

    Receipt and assignment are separate events because they are separate in the operating
    model this serves: contributions arrive unrestricted and the organisation decides
    later what they fund. A record that could not hold money between those two moments
    forced every contribution to name a programme at the instant it was received, which
    is right for a grant and wrong for a standing order.
    """

    __tablename__ = "funding"
    external_id: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    #: Who received it. Always known, which is what makes "held" answerable.
    organization_ref: Mapped[str] = mapped_column(String(160), index=True, default="")
    #: NULL until it is assigned. Held money is money with no programme, not a programme
    #: whose identifier happens to be missing.
    program_ref: Mapped[str | None] = mapped_column(String(160), index=True, nullable=True)
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: How many people this row stands for. One for a grant or a donor who asked to be
    #: named; more for a roll-up, which is how a daily total of small contributions is
    #: held without a row per contribution. See ADR-018.
    contributor_count: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    funder_name: Mapped[str] = mapped_column(String(240))
    #: An organisation that funded a programme is a public fact. A private individual is
    #: a person whose giving is their own business, so the default is the careful one.
    funder_is_organisation: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    #: Set only by someone holding the funder's own consent link. An operator knows the
    #: name and cannot publish it; that choice belongs to the person it names.
    publish_funder_name: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    name_consent_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
    # A bank reports a payment before it settles and can reverse it afterwards. Only a
    # settled payment is a fact about the world; a pending one has not happened yet and a
    # reversed one did not happen, and neither may support a claim.
    settlement: Mapped[str] = mapped_column(
        String(16), default="SETTLED", server_default="SETTLED", index=True
    )
    #: When the provider reported the reversal, so a claim that fell can be traced to it.
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: What a converted figure came from, so it can always be traced to the rate that
    #: produced it rather than being a number nobody can reproduce.
    rate_source: Mapped[str | None] = mapped_column(String(80), nullable=True)
    rate_numerator: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rate_denominator: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rate_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
    # How the figure was arrived at, where it came from, and how sure anyone is. Without
    # these an outcome is the one number on the page a reader simply has to believe, which
    # is what the rest of this system exists to avoid.
    method: Mapped[str] = mapped_column(String(400), default="")
    source: Mapped[str] = mapped_column(String(240), default="")
    # Percent, or NULL where the method does not produce one. Nought and unknown are
    # different answers and a column that cannot hold the difference invents one.
    confidence_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)


#: What a private individual is called on a surface they did not ask to appear on.
REDACTED_FUNDER = "An individual donor"


def public_funder_name(funding: FundingRecord, *, privileged: bool = False) -> str:
    """The name to show for whoever gave the money.

    An organisation that funded a programme is a public fact and stays named. A private
    individual is a person whose giving is their own business, and they can choose to be
    named -- nobody else can choose for them, which is why an operator has no way to set
    this and a consent link does.

    A roll-up stands for many people and names none of them, so it is described by its
    size. Privilege does not unlock a name there because the row never held one.

    The amount, the date and the hashes are unchanged either way, so the money is still
    followable; what is withheld is which person it came from.
    """
    if funding.contributor_count > 1:
        return f"{funding.contributor_count:,} individual donors"
    if privileged or funding.funder_is_organisation or funding.publish_funder_name:
        return funding.funder_name
    return REDACTED_FUNDER


def as_utc_iso(value: datetime | None) -> str | None:
    """Serialise a stored timestamp the same way whether it came from memory or a database.

    SQLite returns a naive datetime for a timezone-aware column, so the same field came
    back with an offset when it had just been written and without one when it was read
    again. Every timestamp this system stores is UTC, so a naive one is read as UTC rather
    than as local time.
    """
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()
