from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, text

from .attribution import MixedCurrencyError, funding_attribution
from .auth import (
    SESSION_COOKIE,
    SESSION_LIFETIME,
    AuthenticatedUser,
    AuthenticationError,
    authenticate,
    challenge_message,
    hash_password,
    issue_wallet_challenge,
    resolve_session,
    revoke_session,
    verify_wallet_signature,
)
from .blockchain import EvmBlockchainService, commitment_from_receipt
from .cli import register_seeded_evidence
from .config import Settings
from .database import create_session_factory
from .demo import INVOICE_BYTES, TAMPERED_INVOICE_BYTES, store
from .domain import BlockchainStatus, Role, Visibility
from .evidence import (
    EvidenceAnalysisProvider,
    EvidenceUnrecoverable,
    FileEvidenceStorage,
    MockEvidenceAnalysisProvider,
    ReconciliationService,
    verify_integrity,
)
from .export import (
    claim_exists,
    evidence_csv,
    money_trail_csv,
    outcomes_csv,
    provenance_csv,
)
from .extraction import SCHEMA_FIELDS, TinkerEvidenceAnalysisProvider, review_required_fields
from .financial import (
    EvidenceReconciliationService,
    FinancialIngestionService,
    MockFinancialDataProvider,
    financial_summary,
    transaction_response,
)
from .hashing import sha256_bytes
from .metrics import REGISTRY, OutboxCollector, integrity_checks, render
from .notifications import (
    ConsoleNotificationTransport,
    Message,
    resolve_token,
    subscribe,
)
from .observability import configure_logging, correlation_context, logger
from .persistence import (
    AttestationRecord,
    BlockchainOperationRecord,
    ClaimRecord,
    DomainEntityRecord,
    EvidenceRecord,
    FinancialTransactionRecord,
    OutboxRecord,
    ProgramRecord,
    UserRecord,
)
from .read_model import (
    CLAIM_ID as SEEDED_CLAIM_ID,
)
from .read_model import TransparencyReadRepository, reset_read_model
from .services import (
    ApplicationActor,
    AuditService,
    AuthorizationError,
    DataProtectionApplicationService,
    DomainConflictError,
    EvidenceApplicationService,
    IdempotencyConflictError,
    OnboardingApplicationService,
    TenantApplicationService,
    VerificationApplicationService,
)
from .verification import restate_claims_for
from .worker import BlockchainOutboxWorker

settings = Settings.from_env()
session_factory = (
    create_session_factory(settings.database_url)
    if settings.persistence_mode == "postgres"
    else None
)
evidence_storage = FileEvidenceStorage(
    settings.evidence_storage_path,
    settings.evidence_encryption_key,
    settings.evidence_key_path,
)
# Accepting a provider name and then using the mock anyway would be the silent fallback
# the specification forbids for chains, so an unknown name is refused at startup by
# Settings rather than degraded here.
analysis_provider: EvidenceAnalysisProvider = (
    TinkerEvidenceAnalysisProvider(model=settings.extraction_model)
    if settings.ai_provider == "tinker"
    else MockEvidenceAnalysisProvider()
)
reconciliation_service = ReconciliationService()
evidence_reconciliation = EvidenceReconciliationService(reconciliation_service)
financial_provider = MockFinancialDataProvider()
configure_logging()
log = logger("impactgraph.api")

app = FastAPI(title="ImpactGraph Transparency API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def correlation_id(request: Request, call_next):
    value = request.headers.get("x-correlation-id", str(uuid4()))
    request.state.correlation_id = value
    with correlation_context(value):
        response = await call_next(request)
        response.headers["x-correlation-id"] = value
        return response


def _refuse_demo_store_route() -> None:
    """These routes fabricate chain state and exist only for the in-memory demo.

    They mark an operation CONFIRMED, and one transitions a claim to VERIFIED, without a
    receipt, an event or a confirmation depth. With a database configured they would also
    contradict the durable read model, so they refuse rather than produce a second,
    fictional answer. The durable path is intent -> wallet/outbox -> observed event.
    """
    if session_factory is not None:
        raise HTTPException(
            409,
            detail={
                "code": "DEMO_ROUTE_DISABLED",
                "message": (
                    "This route fabricates chain state and is disabled when a database is "
                    "configured. Use the durable verification flow."
                ),
            },
        )


def _assert_wallet_may_verify(wallet: str) -> None:
    """Refuse an intent the registry would reject anyway.

    Separation of duties is checked between organisations, which does not stop the
    operator's own wallet being bound to a verifier account. The registry catches that and
    reverts -- but a revert reaches the browser as a failed gas estimate, and the wallet
    then falls back to an enormous gas limit, so the visitor sees a gas error rather than
    the authorisation problem that actually stopped them. Fail here instead, while there
    is still something useful to say.
    """
    try:
        chain = EvmBlockchainService.from_foundry_artifact(
            rpc_url=settings.rpc_url,
            contract_address=settings.registry_address,
            artifact_path=settings.contract_artifact_path,
        )
        holds_verifier = chain.has_verifier_role(wallet)
        holds_operator = chain.has_operator_role(wallet)
    except (OSError, ConnectionError, ValueError):
        # The registry is unreachable. The chain still enforces this, so do not block the
        # flow on our own inability to pre-check it.
        return
    if holds_operator:
        raise HTTPException(
            403,
            detail={
                "code": "WALLET_IS_OPERATOR",
                "message": (
                    f"{wallet} holds OPERATOR_ROLE on this registry, so it cannot also be "
                    "the independent verifier. Connect the verifier's wallet instead."
                ),
            },
        )
    if not holds_verifier:
        raise HTTPException(
            403,
            detail={
                "code": "WALLET_LACKS_VERIFIER_ROLE",
                "message": (
                    f"{wallet} does not hold VERIFIER_ROLE on this registry, so the "
                    "attestation would revert. Connect the wallet that was granted the "
                    "verifier role."
                ),
            },
        )


def _unauthenticated() -> HTTPException:
    return HTTPException(
        401,
        detail={"code": "NOT_AUTHENTICATED", "message": "Sign in to perform this operation"},
    )


def current_user(request: Request) -> AuthenticatedUser | None:
    """Resolve the signed-in user, or None. Reads are public, so this never raises."""
    if session_factory is None:
        return None
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        header = request.headers.get("authorization", "")
        token = header[7:] if header.lower().startswith("bearer ") else None
    if not token:
        return None
    with session_factory() as session:
        return resolve_session(session, token)


CurrentUser = Annotated[AuthenticatedUser | None, Depends(current_user)]


def require_user(user: AuthenticatedUser | None, allowed: set[Role]) -> AuthenticatedUser:
    """Authorise a mutation against the signed-in identity.

    Replaces the former header-derived role. The identity now comes from a session the
    caller had to authenticate to obtain, so the separation-of-duties rules built on it
    are enforceable rather than advisory.
    """
    if session_factory is None:
        raise HTTPException(
            409, "Authentication requires PERSISTENCE_MODE=postgres"
        )
    if user is None:
        raise _unauthenticated()
    if user.role not in allowed:
        raise HTTPException(
            403,
            detail={
                "code": "ROLE_FORBIDDEN",
                "message": f"{user.role.value} cannot perform this operation",
            },
        )
    return user


def actor_for(user: AuthenticatedUser) -> ApplicationActor:
    """The domain actor for a signed-in user. Organisation identity is not client-supplied."""
    return ApplicationActor(user.organization_external_id, user.role, user.wallet_address)


class InvoiceExtraction(BaseModel):
    """The review boundary accepts the same constrained shape produced by the AI provider."""

    model_config = ConfigDict(extra="forbid")

    # Required, because reconciliation resolves a payment against these and an operator
    # confirms every one of them before registration. The rest are nullable: a real
    # document need not carry an equipment line, and a model that invents one to satisfy
    # a schema is worse than a model that says the document did not contain it.
    documentType: str
    invoiceNumber: str
    amountMinor: int = Field(ge=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    vendor: str | None = None
    date: str | None = None
    equipment: str | None = None
    quantity: int | None = Field(default=None, ge=1)
    projectReference: str | None = None
    # Self-reported by the model and per field, so the operator screen can mark the ones
    # worth looking at rather than presenting one number for the whole document.
    selfReportedConfidence: dict[str, Annotated[float, Field(ge=0, le=1)]]


class OrganizationCreateRequest(BaseModel):
    """The first user's role is not here: it follows from the organisation's kind."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=3, max_length=160, pattern=r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["OPERATOR", "VERIFIER"]
    userEmail: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    userName: str = Field(min_length=1, max_length=200)
    userPassword: str = Field(min_length=12, max_length=200)


class ProgramCreateRequest(BaseModel):
    """The owning organisation is deliberately absent: it comes from the session."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=3, max_length=120, pattern=r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")
    name: str = Field(min_length=1, max_length=240)
    region: str = Field(min_length=1, max_length=240)
    verificationThreshold: int = Field(default=1, ge=1, le=10)


class VerifierRoleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    userEmail: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ProjectCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=3, max_length=160, pattern=r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")
    name: str = Field(min_length=1, max_length=240)


class ClaimCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=3, max_length=160, pattern=r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")
    programId: str = Field(min_length=1, max_length=160)
    projectId: str = Field(min_length=1, max_length=160)
    statement: str = Field(min_length=1, max_length=1000)
    outcomeId: str = Field(min_length=1, max_length=160)


class DataProtectionRequest(BaseModel):
    """What makes holding this object lawful. Not a consent form.

    An organisation that delivers the aid it photographs cannot obtain freely given
    consent from the person receiving it, so the basis is stated rather than assumed.
    """

    model_config = ConfigDict(extra="forbid")

    lawfulBasis: Literal[
        "CONSENT",
        "CONTRACT",
        "LEGAL_OBLIGATION",
        "VITAL_INTERESTS",
        "PUBLIC_TASK",
        "LEGITIMATE_INTEREST",
    ]
    specialCategory: bool = False
    controllerOrgRef: str = Field(min_length=1, max_length=160)
    jointControllerOrgRef: str | None = Field(default=None, max_length=160)
    subjectReference: str = Field(min_length=1, max_length=160)
    purpose: str = Field(min_length=1, max_length=1000)
    retainUntil: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class ErasureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=1000)


class EvidenceReviewRequest(BaseModel):
    extraction: InvoiceExtraction
    #: The fields the operator confirmed against the document in front of them. Reviewing
    #: is the act this records, so the endpoint refuses a review that does not cover every
    #: field the analysis flagged -- otherwise the confirmation is a checkbox in a browser
    #: and the API registers whatever a model proposed.
    confirmed: list[str] = Field(default_factory=list)


class WalletSubmissionRequest(BaseModel):
    transactionHash: str
    wallet: str


class VerificationDecisionRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


async def monitor_wallet_operation(operation_id: UUID) -> None:
    if session_factory is None or not settings.registry_address:
        return
    blockchain = EvmBlockchainService.from_foundry_artifact(
        rpc_url=settings.rpc_url,
        contract_address=settings.registry_address,
        artifact_path=settings.contract_artifact_path,
        sender=settings.evm_sender_address or None,
    )
    worker = BlockchainOutboxWorker(
        session_factory=session_factory,
        blockchain=blockchain,
        confirmations_required=settings.confirmations_required,
    )
    for _ in range(120):
        result = await asyncio.to_thread(worker.observe_operation, operation_id)
        if result in {BlockchainStatus.CONFIRMED, BlockchainStatus.FAILED}:
            return
        await asyncio.sleep(1)


async def process_backend_operation(operation_id: UUID) -> None:
    """Submit a durable outbox intent, then independently confirm its receipt and event."""
    if session_factory is None or not settings.registry_address:
        return
    blockchain = EvmBlockchainService.from_foundry_artifact(
        rpc_url=settings.rpc_url,
        contract_address=settings.registry_address,
        artifact_path=settings.contract_artifact_path,
        sender=settings.evm_sender_address or None,
    )
    worker = BlockchainOutboxWorker(
        session_factory=session_factory,
        blockchain=blockchain,
        confirmations_required=settings.confirmations_required,
    )
    for _ in range(120):
        result = await asyncio.to_thread(worker.run_once)
        with session_factory() as session:
            operation = session.get(BlockchainOperationRecord, operation_id)
            if operation and operation.status in {BlockchainStatus.CONFIRMED, BlockchainStatus.FAILED}:
                return
        if result.failed:
            return
        await asyncio.sleep(1)


def evidence_record_response(record: EvidenceRecord) -> dict[str, Any]:
    metadata = record.metadata_json or {}
    return {
        "id": record.external_id,
        "projectId": record.project_ref,
        "type": record.evidence_type,
        "filename": metadata.get("filename"),
        "contentHash": record.content_hash,
        "storageUri": record.storage_uri,
        "mimeType": record.mime_type,
        "visibility": record.visibility,
        "workflowStatus": record.workflow_status,
        "analysisStatus": record.analysis_status,
        # Which fields a person has to confirm. Always includes the ones reconciliation
        # resolves a payment against, whatever the model reported about its own certainty:
        # a misread digit in an invoice number turns a matched payment into an unmatched
        # one, and the model has been observed misreading exactly that field.
        "reviewRequired": (
            review_required_fields(record.extraction) if record.extraction else []
        ),
        "integrityStatus": record.integrity_status,
        "blockchainStatus": record.blockchain_status,
        "extraction": record.extraction,
        "reconciliation": record.reconciliation,
        "providerMetadata": metadata.get("providerMetadata"),
        "blockchainReference": metadata.get("blockchainReference"),
    }


def require_idempotency(value: str | None) -> str:
    if not value:
        raise HTTPException(
            400,
            detail={"code": "IDEMPOTENCY_KEY_REQUIRED", "message": "Idempotency-Key is required"},
        )
    return value


def database_read(method: str, *args):
    if session_factory is None:
        return None
    with session_factory() as session:
        repository = TransparencyReadRepository(session)
        try:
            return getattr(repository, method)(*args)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc


class LoginRequest(BaseModel):
    email: str
    password: str


class WalletProofRequest(BaseModel):
    nonce: str
    signature: str


def _session_payload(user: AuthenticatedUser) -> dict[str, Any]:
    return {
        "email": user.email,
        "displayName": user.display_name,
        "role": user.role.value,
        "organization": {
            "id": user.organization_external_id,
            "name": user.organization_name,
        },
        "walletAddress": user.wallet_address,
    }


@app.post("/auth/login")
def login(body: LoginRequest, response: Response):
    if session_factory is None:
        raise HTTPException(409, "Authentication requires PERSISTENCE_MODE=postgres")
    with session_factory.begin() as session:
        try:
            token, user = authenticate(session, body.email, body.password)
        except AuthenticationError as exc:
            # One message for both unknown account and wrong password: distinguishing
            # them would let an unauthenticated caller enumerate valid accounts. The
            # operational log carries no identifier for the same reason -- it would
            # reconstruct the enumeration the response refuses to give.
            log.info("auth.login_failed")
            raise HTTPException(
                401, detail={"code": "INVALID_CREDENTIALS", "message": str(exc)}
            ) from exc
        log.info("auth.login_succeeded", user_id=str(user.user_id), role=user.role)
        payload = _session_payload(user)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=settings.app_env == "production",
        max_age=int(SESSION_LIFETIME.total_seconds()),
        path="/",
    )
    return payload


@app.post("/auth/logout")
def logout(request: Request, response: Response):
    if session_factory is not None:
        with session_factory.begin() as session:
            revoke_session(session, request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"signedOut": True}


@app.get("/auth/me")
def me(user: CurrentUser = None):
    if user is None:
        raise _unauthenticated()
    return _session_payload(user)


@app.post("/auth/wallet/challenge")
def wallet_challenge(user: CurrentUser = None):
    """Issue a nonce the wallet must sign to prove control of its address."""
    verifier = require_user(user, {Role.VERIFIER, Role.OPERATOR, Role.ADMIN})
    with session_factory.begin() as session:  # type: ignore[union-attr]
        nonce = issue_wallet_challenge(session, verifier.user_id)
    return {"nonce": nonce, "message": challenge_message(nonce)}


@app.post("/auth/wallet/verify")
def wallet_verify(body: WalletProofRequest, user: CurrentUser = None):
    """Bind a wallet address to the account by recovering it from a signature.

    The address is recovered from the signature rather than supplied by the caller, so an
    account cannot claim a wallet it does not control -- which the previous
    X-Wallet-Address header allowed.
    """
    account = require_user(user, {Role.VERIFIER, Role.OPERATOR, Role.ADMIN})
    with session_factory.begin() as session:  # type: ignore[union-attr]
        try:
            address = verify_wallet_signature(
                session, account.user_id, body.nonce, body.signature
            )
        except AuthenticationError as exc:
            raise HTTPException(
                400, detail={"code": "WALLET_PROOF_INVALID", "message": str(exc)}
            ) from exc
        record = session.get(UserRecord, account.user_id)
        if record is None:
            raise _unauthenticated()
        record.wallet_address = address
    return {"walletAddress": address}


@app.get("/financial/programs/{program_id}")
def program_financials(program_id: str):
    """Where the money came from, what it was committed to, and where it went.

    Public: "where did the money go" is one of the questions the product exists to answer,
    and an answer only its operator can see is not transparency.
    """
    if session_factory is None:
        raise HTTPException(409, "Financial records require PERSISTENCE_MODE=postgres")
    with session_factory() as session:
        return financial_summary(session, program_id)


@app.get("/financial/funding/{funding_id}/attribution")
def funding_attribution_view(funding_id: str):
    # Public, like the rest of the money trail: following a contribution to what it
    # reached is the question this product exists to answer.
    if session_factory is None:
        raise HTTPException(409, "Attribution requires PERSISTENCE_MODE=postgres")
    with session_factory() as session:
        try:
            return funding_attribution(session, funding_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except MixedCurrencyError as exc:
            # Refusing beats presenting a total that silently added two currencies.
            # Conversion with a traceable rate belongs to the financial adapter.
            raise HTTPException(
                409,
                detail={"code": "MIXED_CURRENCY", "message": str(exc)},
            ) from exc


@app.get("/financial/transactions/{transaction_id}")
def financial_transaction(transaction_id: str):
    if session_factory is None:
        raise HTTPException(409, "Financial records require PERSISTENCE_MODE=postgres")
    with session_factory() as session:
        record = session.scalar(
            select(FinancialTransactionRecord).where(
                FinancialTransactionRecord.external_id == transaction_id
            )
        )
        if record is None:
            raise HTTPException(404, "Financial transaction not found")
        return transaction_response(record)


@app.post("/financial/programs/{program_id}/import")
def import_financial_statement(
    request: Request,
    program_id: str,
    user: CurrentUser = None,
    idempotency_key: str | None = Header(default=None),
):
    """Pull the provider statement and record any payments not already observed.

    Importing the same statement twice is a no-op: payments are unique per provider
    source reference. Spend beyond the allocation is rejected rather than recorded.
    """
    actor = actor_for(require_user(user, {Role.OPERATOR, Role.ADMIN}))
    _require_program_management(program_id, user)
    require_idempotency(idempotency_key)
    if session_factory is None:
        raise HTTPException(409, "Financial records require PERSISTENCE_MODE=postgres")
    service = FinancialIngestionService(financial_provider)
    with session_factory.begin() as session:
        result = service.import_statement(session, program_ref=program_id)
        AuditService().record(
            session,
            actor=actor,
            action="FINANCIAL_STATEMENT_IMPORTED",
            entity_type="PROGRAM",
            entity_id=program_id,
            correlation_id=request.state.correlation_id,
            metadata=result.as_dict(),
        )
        return {"provider": financial_provider.name, **result.as_dict()}


#: Readiness probes must not outlast the interval an orchestrator polls them on.
READINESS_TIMEOUT_SECONDS = 3


def _probe_database() -> None:
    if session_factory is None:
        raise RuntimeError("no database is configured")
    with session_factory() as session:
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            # Bounds the query server side, so a database that accepts the connection and
            # then stops answering releases the thread instead of holding it forever.
            session.execute(
                text(f"SET LOCAL statement_timeout = {READINESS_TIMEOUT_SECONDS * 1000}")
            )
        session.execute(text("SELECT 1"))


@lru_cache(maxsize=1)
def _readiness_web3():
    from web3 import Web3

    return Web3(
        Web3.HTTPProvider(
            settings.rpc_url, request_kwargs={"timeout": READINESS_TIMEOUT_SECONDS}
        )
    )


def _probe_chain() -> None:
    if not settings.registry_address:
        raise RuntimeError("no registry address is configured")
    # The request timeout does not bound name resolution, so a stalled DNS lookup can
    # still hold a worker. One reused provider keeps that bounded to one socket rather
    # than one per probe.
    reported = _readiness_web3().eth.chain_id
    if reported != settings.chain_id:
        # Reachable is not the same as correct. A wallet or a worker pointed at the wrong
        # chain is the failure this catches, and it looks healthy by every other measure.
        raise RuntimeError(f"chain reports {reported}, expected {settings.chain_id}")


def _probe_evidence_storage() -> None:
    root = settings.evidence_storage_path
    # Accessibility only. A write probe would prove more and would also mutate the store
    # this application promises never to alter outside an upload.
    if not root.is_dir() or not os.access(root, os.R_OK | os.W_OK):
        raise RuntimeError("evidence storage is not readable and writable")


READINESS_PROBES = {
    "database": _probe_database,
    "chain": _probe_chain,
    "evidenceStorage": _probe_evidence_storage,
}

#: Components whose probe has not come back yet. Python cannot interrupt a blocked call,
#: so a probe that overruns leaves its thread working; without this a dependency that
#: stopped answering would strand one more thread on every poll, for ever.
_probes_in_flight: set[str] = set()


async def _run_probe(name: str, probe) -> None:
    if name in _probes_in_flight:
        raise TimeoutError(f"the previous {name} probe has not returned")

    def release_when_done() -> None:
        try:
            probe()
        finally:
            _probes_in_flight.discard(name)

    _probes_in_flight.add(name)
    await asyncio.wait_for(
        asyncio.to_thread(release_when_done), timeout=READINESS_TIMEOUT_SECONDS
    )


def _outbox_backlog() -> tuple[int, float]:
    """Rows waiting, and how long the oldest has waited. Empty is zero for both."""
    if session_factory is None:
        return 0, 0.0
    with session_factory() as session:
        pending, oldest = session.execute(
            select(func.count(OutboxRecord.id), func.min(OutboxRecord.created_at)).where(
                OutboxRecord.processed_at.is_(None)
            )
        ).one()
    if not pending or oldest is None:
        return 0, 0.0
    if oldest.tzinfo is None:
        oldest = oldest.replace(tzinfo=UTC)
    return pending, max(0.0, (datetime.now(UTC) - oldest).total_seconds())


REGISTRY.register(OutboxCollector(_outbox_backlog))


@app.get("/metrics")
def metrics():
    return Response(content=render(), media_type=CONTENT_TYPE_LATEST)


class FollowRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)


notification_transport = ConsoleNotificationTransport()


@app.post("/claims/{claim_id}/follow", status_code=202)
def follow_claim(claim_id: str, body: FollowRequest):
    """Ask to be told when this claim changes. No account, by design."""
    if session_factory is None:
        raise HTTPException(409, "Following a claim requires PERSISTENCE_MODE=postgres")
    with session_factory.begin() as session:
        claim = session.scalar(
            select(ClaimRecord).where(ClaimRecord.external_id == claim_id)
        )
        if claim is None:
            raise HTTPException(404, "Claim not found")
        token = subscribe(session, email=body.email, claim_id=claim_id)

    if token is not None:
        # Nothing further is ever sent to an address that has not answered this, so the
        # endpoint cannot be used to mail someone who did not ask.
        notification_transport.send(
            Message(
                to=body.email.strip().lower(),
                subject="Confirm that you want updates on this claim",
                body=(
                    f"Someone asked for updates when claim {claim_id} changes. If that "
                    "was you, follow the link. If it was not, ignore this and nothing "
                    "further will be sent."
                ),
                manage_url_path=f"/notifications/confirm?token={token}",
            )
        )
    # One answer whether or not this address already follows the claim. Two would let a
    # caller ask who is watching what.
    return {"status": "check_your_email"}


@app.post("/notifications/confirm")
def confirm_following(token: str):
    if session_factory is None:
        raise HTTPException(409, "Notifications require PERSISTENCE_MODE=postgres")
    with session_factory.begin() as session:
        subscription = resolve_token(session, token)
        if subscription is None:
            raise HTTPException(404, "This link is not valid")
        if subscription.confirmed_at is None:
            subscription.confirmed_at = datetime.now(UTC)
        subscription.unsubscribed_at = None
        return {"status": "following", "claimId": subscription.claim_id}


@app.post("/notifications/unsubscribe")
def unsubscribe_from_claim(token: str):
    """One click, no account, and honoured immediately."""
    if session_factory is None:
        raise HTTPException(409, "Notifications require PERSISTENCE_MODE=postgres")
    with session_factory.begin() as session:
        subscription = resolve_token(session, token)
        if subscription is None:
            raise HTTPException(404, "This link is not valid")
        subscription.unsubscribed_at = datetime.now(UTC)
        return {"status": "unsubscribed", "claimId": subscription.claim_id}


def _csv_response(body: str, filename: str) -> Response:
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/export/programs/{program_id}/money-trail.csv")
def export_money_trail(program_id: str):
    """Public, like the money trail it serialises."""
    if session_factory is None:
        raise HTTPException(409, "Export requires PERSISTENCE_MODE=postgres")
    with session_factory() as session:
        body = money_trail_csv(session, program_id)
    return _csv_response(body, f"{program_id}-money-trail.csv")


@app.get("/export/programs/{program_id}/outcomes.csv")
def export_outcomes(program_id: str):
    if session_factory is None:
        raise HTTPException(409, "Export requires PERSISTENCE_MODE=postgres")
    with session_factory() as session:
        body = outcomes_csv(session, program_id)
    return _csv_response(body, f"{program_id}-outcomes.csv")


@app.get("/export/claims/{claim_id}/provenance.csv")
def export_provenance(claim_id: str):
    if session_factory is None:
        raise HTTPException(409, "Export requires PERSISTENCE_MODE=postgres")
    with session_factory() as session:
        if not claim_exists(session, claim_id):
            raise HTTPException(404, "Claim not found")
        body = provenance_csv(session, claim_id)
    return _csv_response(body, f"{claim_id}-provenance.csv")


@app.get("/export/claims/{claim_id}/evidence.csv")
def export_evidence(claim_id: str, user: CurrentUser = None):
    """Exactly the rows this reader could already open, one at a time.

    Decided here against the session rather than inside the serialiser, so an export
    cannot become the one route that answers what every other route refuses -- nor the one
    that refuses what every other route permits. The per-row check is the same function
    the evidence endpoint uses, so the two cannot drift apart.
    """
    if session_factory is None:
        raise HTTPException(409, "Export requires PERSISTENCE_MODE=postgres")
    with session_factory() as session:
        if not claim_exists(session, claim_id):
            raise HTTPException(404, "Claim not found")
        body = evidence_csv(session, claim_id, readable=lambda item: _may_read(item, user))
    return _csv_response(body, f"{claim_id}-evidence.csv")


def _may_read(evidence_id: str, user: AuthenticatedUser | None) -> bool:
    try:
        _require_evidence_visibility(evidence_id, user)
    except HTTPException:
        return False
    return True


@app.get("/health/live")
def health_live():
    """Liveness: this process is serving. Deliberately touches nothing else.

    The container HEALTHCHECK polls this. A dependency-aware probe there would let a
    transient database or RPC fault mark the process for replacement, which fixes nothing
    and loses whatever it was doing.
    """
    return {"status": "ok", "mode": settings.demo_mode, "chainId": settings.chain_id}


@app.get("/health", include_in_schema=False)
def health_alias():
    """Containers and scripts deployed before the split still poll this."""
    return health_live()


@app.get("/health/ready")
async def health_ready(response: Response):
    """Readiness: the dependencies this API cannot serve without are answering.

    Async, and every probe is bounded: a health check that blocks is a health check that
    becomes the outage it exists to report. At most one probe per component is ever
    outstanding, so a dependency that stops answering costs one stranded thread rather
    than one on every poll.
    """
    components: dict[str, Any] = {}
    for name, probe in READINESS_PROBES.items():
        try:
            await _run_probe(name, probe)
            components[name] = {"status": "ok"}
        except Exception as exc:  # noqa: BLE001 -- a probe reports, it does not raise
            # The class, never the text: an RPC or database URL may embed credentials.
            components[name] = {"status": "unavailable", "error": exc.__class__.__name__}
    ready = all(item["status"] == "ok" for item in components.values())
    if not ready:
        response.status_code = 503
    return {"status": "ready" if ready else "unavailable", "components": components}


@app.get("/programs")
def programs():
    return database_read("programs") if session_factory else store.programs()


@app.get("/programs/{program_id}")
def program(program_id: str):
    if session_factory:
        return database_read("program", program_id)
    if program_id != store.program()["id"]:
        raise HTTPException(404, "Program not found")
    return store.program()


@app.get("/projects/{project_id}")
def project(project_id: str):
    if session_factory:
        return database_read("project", project_id)
    if project_id != store.project()["id"]:
        raise HTTPException(404, "Project not found")
    return store.project()


@app.get("/claims")
def claims(status: str | None = None, program: str | None = None, user: CurrentUser = None):
    """Claims, optionally by status and program.

    The verifier workspace named one claim in its source, so a second organisation's work
    was unreachable and the queue its own comment described did not exist.

    Drafts are left out for anyone but the organisation that wrote them. Any claim can
    still be read by its identifier -- that is the transparency this product is for -- but
    enumerating them would publish an organisation's unfinished statements the moment they
    were written, which is a different thing from making the finished ones inspectable.
    """
    if session_factory is None:
        raise HTTPException(503, "Listing claims requires PERSISTENCE_MODE=postgres")
    own_drafts = (
        user.organization_external_id
        if user and user.role in {Role.OPERATOR, Role.ADMIN}
        else None
    )
    return database_read("claims", status, program, own_drafts)


@app.get("/claims/{claim_id}")
def claim(claim_id: str):
    if session_factory:
        return database_read("claim", claim_id)
    if claim_id != store.claim["id"]:
        raise HTTPException(404, "Claim not found")
    return store.claim


@app.get("/claims/{claim_id}/provenance")
def provenance(claim_id: str):
    if session_factory:
        return database_read("provenance", claim_id)
    if claim_id != store.claim["id"]:
        raise HTTPException(404, "Claim not found")
    return store.graph()


@app.get("/claims/{claim_id}/verification")
def verification(claim_id: str):
    if session_factory:
        return database_read("verification", claim_id)
    if claim_id != store.claim["id"]:
        raise HTTPException(404, "Claim not found")
    return store.verification()


@app.get("/evidence/{evidence_id}")
def evidence(evidence_id: str, user: CurrentUser = None):
    _require_evidence_visibility(evidence_id, user)
    if session_factory:
        return database_read("evidence", evidence_id)
    if evidence_id not in store.evidence:
        raise HTTPException(404, "Evidence not found")
    return store.evidence[evidence_id]


def _tenant_write(operation):
    """Run a tenant creation and map its domain errors onto the HTTP contract."""
    try:
        with session_factory.begin() as session:
            return operation(session)
    except AuthorizationError as exc:
        raise HTTPException(403, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except IdempotencyConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, DomainConflictError) as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/organizations", status_code=201)
def create_organization(
    request: Request,
    body: OrganizationCreateRequest,
    user: CurrentUser = None,
    idempotency_key: str | None = Header(default=None),
):
    """Onboard an organisation and its first person. Administrators only.

    There is no public signup: who may act as an operator or a verifier is not something
    to leave open. A verifier still cannot attest until its wallet has been proven and
    granted VERIFIER_ROLE, which the response says.
    """
    actor = actor_for(require_user(user, {Role.ADMIN}))
    key = require_idempotency(idempotency_key)
    if session_factory is None:
        raise HTTPException(503, "Onboarding requires PERSISTENCE_MODE=postgres")
    service = OnboardingApplicationService(settings.chain_id)
    return _tenant_write(
        lambda session: service.create_organization(
            session,
            actor=actor,
            organization_id=body.id,
            name=body.name,
            kind=body.kind,
            user_email=body.userEmail,
            user_name=body.userName,
            password_hash=hash_password(body.userPassword),
            correlation_id=request.state.correlation_id,
            idempotency_key=key,
        )
    )


@app.post("/organizations/verifier-role", status_code=202)
def grant_verifier_role(
    request: Request,
    body: VerifierRoleRequest,
    background_tasks: BackgroundTasks,
    user: CurrentUser = None,
    idempotency_key: str | None = Header(default=None),
):
    """Grant a verifier's proven wallet VERIFIER_ROLE on the registry.

    `createAttestation` checks the role of the address that signed it, and a verifier signs
    with their own wallet, so without this a newly onboarded verifier can sign nothing.
    """
    actor = actor_for(require_user(user, {Role.ADMIN}))
    key = require_idempotency(idempotency_key)
    if session_factory is None:
        raise HTTPException(503, "Granting a role requires PERSISTENCE_MODE=postgres")
    service = OnboardingApplicationService(settings.chain_id)
    response = _tenant_write(
        lambda session: service.grant_verifier_role(
            session,
            actor=actor,
            user_email=body.userEmail,
            correlation_id=request.state.correlation_id,
            idempotency_key=key,
        )
    )
    if response.get("operationId"):
        background_tasks.add_task(process_backend_operation, UUID(response["operationId"]))
    return response


@app.get("/operator/programs")
def operator_programs(user: CurrentUser = None):
    """The programs this operator may file evidence under, with their projects.

    Scoped to the caller's organisation rather than returning everything: the operator
    workspace had one project named in its source, and listing every tenant's projects to
    replace that would be a worse answer than the constant was.
    """
    authenticated = require_user(user, {Role.OPERATOR, Role.ADMIN})
    if session_factory is None:
        raise HTTPException(503, "Listing programs requires PERSISTENCE_MODE=postgres")
    with session_factory() as session:
        query = select(ProgramRecord).order_by(ProgramRecord.name)
        if authenticated.role != Role.ADMIN:
            query = query.where(
                ProgramRecord.operator_org_ref == authenticated.organization_external_id
            )
        programs = list(session.scalars(query))
        projects = {
            program.id: [
                {"id": entity.external_id, "name": (entity.data or {}).get("name", entity.external_id)}
                for entity in session.scalars(
                    select(DomainEntityRecord).where(
                        DomainEntityRecord.entity_type == "PROJECT",
                        DomainEntityRecord.program_id == program.id,
                    )
                )
            ]
            for program in programs
        }
        return [
            {
                "id": program.slug,
                "name": program.name,
                "region": program.region,
                "operator": program.operator_name,
                "chainStatus": program.chain_status,
                "projects": projects.get(program.id, []),
            }
            for program in programs
        ]


@app.post("/programs", status_code=201)
def create_program(
    request: Request,
    body: ProgramCreateRequest,
    background_tasks: BackgroundTasks,
    user: CurrentUser = None,
    idempotency_key: str | None = Header(default=None),
):
    """Create a program and queue the registry entity it needs to be usable.

    The program is returned PENDING. It cannot accept evidence until the worker has
    observed the ProgramCreated event, because `registerEvidence` reverts with
    UnknownProgram against a registry that has never heard of it.
    """
    actor = actor_for(require_user(user, {Role.OPERATOR, Role.ADMIN}))
    key = require_idempotency(idempotency_key)
    if session_factory is None:
        raise HTTPException(503, "Creating a program requires PERSISTENCE_MODE=postgres")
    service = TenantApplicationService(settings.chain_id)
    response = _tenant_write(
        lambda session: service.create_program(
            session,
            actor=actor,
            program_id=body.id,
            name=body.name,
            region=body.region,
            verification_threshold=body.verificationThreshold,
            correlation_id=request.state.correlation_id,
            idempotency_key=key,
        )
    )
    if response.get("operationId"):
        background_tasks.add_task(process_backend_operation, UUID(response["operationId"]))
    return response


@app.post("/programs/{program_id}/projects", status_code=201)
def create_project(
    request: Request,
    program_id: str,
    body: ProjectCreateRequest,
    user: CurrentUser = None,
    idempotency_key: str | None = Header(default=None),
):
    """A project is not a registry entity, so this is a database write and nothing else."""
    actor = actor_for(require_user(user, {Role.OPERATOR, Role.ADMIN}))
    key = require_idempotency(idempotency_key)
    if session_factory is None:
        raise HTTPException(503, "Creating a project requires PERSISTENCE_MODE=postgres")
    service = TenantApplicationService(settings.chain_id)
    return _tenant_write(
        lambda session: service.create_project(
            session,
            actor=actor,
            program_id=program_id,
            project_id=body.id,
            name=body.name,
            correlation_id=request.state.correlation_id,
            idempotency_key=key,
        )
    )


@app.post("/programs/{program_id}/registry-entity", status_code=202)
def retry_program_entity(
    request: Request,
    program_id: str,
    background_tasks: BackgroundTasks,
    user: CurrentUser = None,
    idempotency_key: str | None = Header(default=None),
):
    """Queue the registry entity again after a failed submission.

    Without this a program whose chain call failed for a transient reason is a tombstone:
    claims refuse it, evidence refuses it, and its identifier is taken so it cannot be
    created again. Evidence has always been able to retry; a program could not.
    """
    actor = actor_for(require_user(user, {Role.OPERATOR, Role.ADMIN}))
    key = require_idempotency(idempotency_key)
    if session_factory is None:
        raise HTTPException(503, "Retrying a registry entity requires PERSISTENCE_MODE=postgres")
    service = TenantApplicationService(settings.chain_id)
    response = _tenant_write(
        lambda session: service.retry_registry_entity(
            session,
            actor=actor,
            program_id=program_id,
            correlation_id=request.state.correlation_id,
            idempotency_key=key,
        )
    )
    if response.get("operationId"):
        background_tasks.add_task(process_backend_operation, UUID(response["operationId"]))
    return response


@app.post("/claims", status_code=201)
def create_claim(
    request: Request,
    body: ClaimCreateRequest,
    background_tasks: BackgroundTasks,
    user: CurrentUser = None,
    idempotency_key: str | None = Header(default=None),
):
    actor = actor_for(require_user(user, {Role.OPERATOR, Role.ADMIN}))
    key = require_idempotency(idempotency_key)
    if session_factory is None:
        raise HTTPException(503, "Creating a claim requires PERSISTENCE_MODE=postgres")
    service = TenantApplicationService(settings.chain_id)
    response = _tenant_write(
        lambda session: service.create_claim(
            session,
            actor=actor,
            claim_id=body.id,
            program_id=body.programId,
            project_id=body.projectId,
            statement=body.statement,
            outcome_id=body.outcomeId,
            correlation_id=request.state.correlation_id,
            idempotency_key=key,
        )
    )
    if response.get("operationId"):
        background_tasks.add_task(process_backend_operation, UUID(response["operationId"]))
    return response


@app.post("/evidence", status_code=201)
async def upload_evidence(
    request: Request,
    evidence_id: Annotated[str, Form()],
    project_id: Annotated[str, Form()],
    evidence_type: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
    visibility: Annotated[str, Form()] = "RESTRICTED",
    # Declared by the operator, because nothing else can know whether a photograph has a
    # person in it. Registration is refused for such an object until a lawful basis and a
    # controller have been recorded against it.
    personal_data: Annotated[bool, Form()] = False,
    user: CurrentUser = None,
    idempotency_key: str | None = Header(default=None),
):
    actor = actor_for(require_user(user, {Role.OPERATOR, Role.ADMIN}))
    normalized_visibility = visibility.upper()
    try:
        Visibility(normalized_visibility)
    except ValueError as exc:
        raise HTTPException(422, "visibility must be PUBLIC, RESTRICTED, or INTERNAL") from exc
    _require_project_management(project_id, user)
    key = require_idempotency(idempotency_key)
    if file.content_type not in settings.allowed_upload_types:
        raise HTTPException(415, f"Upload type {file.content_type!r} is not allowed")
    content = await file.read(settings.max_upload_bytes + 1)
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(413, "Evidence upload exceeds configured size limit")
    if not content:
        raise HTTPException(422, "Evidence upload cannot be empty")
    content_hash = sha256_bytes(content)
    if session_factory is not None:
        try:
            storage_uri = evidence_storage.store(evidence_id, content)
            service = EvidenceApplicationService(settings.chain_id)
            with session_factory.begin() as session:
                response = service.create_uploaded(
                    session,
                    actor=actor,
                    evidence_id=evidence_id,
                    project_ref=project_id,
                    evidence_type=evidence_type.upper(),
                    storage_uri=storage_uri,
                    content_hash=content_hash,
                    mime_type=file.content_type or "application/octet-stream",
                    visibility=normalized_visibility,
                    personal_data=personal_data,
                    correlation_id=request.state.correlation_id,
                    idempotency_key=key,
                )
                record = session.scalar(
                    select(EvidenceRecord).where(EvidenceRecord.external_id == evidence_id)
                )
                if record:
                    # Resolve the program from the project the evidence was filed under.
                    # Defaulting to the showcase program would file evidence against it
                    # whatever project the operator named.
                    project = session.scalar(
                        select(DomainEntityRecord).where(
                            DomainEntityRecord.external_id == project_id,
                            DomainEntityRecord.entity_type == "PROJECT",
                        )
                    )
                    program = (
                        session.get(ProgramRecord, project.program_id)
                        if project and project.program_id
                        else None
                    )
                    record.metadata_json = {
                        "filename": file.filename,
                        "programId": program.slug if program else None,
                    }
            return response
        except (ValueError, FileExistsError, DomainConflictError) as exc:
            raise HTTPException(409, str(exc)) from exc
    fingerprint = f"UPLOAD:{evidence_id}:{project_id}:{content_hash}"
    existing = store.idempotency.get(key)
    if existing:
        try:
            return store.remember_idempotent(key, fingerprint, {})
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
    if evidence_id in store.evidence:
        raise HTTPException(409, "Evidence identifier already exists")
    try:
        storage_uri = evidence_storage.store(evidence_id, content)
    except (ValueError, FileExistsError) as exc:
        raise HTTPException(409, str(exc)) from exc
    item = {
        "id": evidence_id,
        "projectId": project_id,
        "type": evidence_type.upper(),
        "filename": file.filename,
        "contentHash": content_hash,
        "storageUri": storage_uri,
        "mimeType": file.content_type,
        "visibility": normalized_visibility,
        "workflowStatus": "UPLOADED",
        "analysisStatus": "NOT_STARTED",
        "integrityStatus": "NOT_CHECKED",
        "blockchainStatus": "NOT_STARTED",
        "uploadedBy": "Global Water Initiative",
        "source": "Operator upload",
        "tampered": False,
        "correlationId": request.state.correlation_id,
    }
    store.evidence[evidence_id] = item
    return store.remember_idempotent(key, fingerprint, item)


@app.post("/evidence/{evidence_id}/analyze")
def analyze_evidence(evidence_id: str, user: CurrentUser = None):
    require_user(user, {Role.OPERATOR, Role.ADMIN})
    _require_evidence_management(evidence_id, user)
    if session_factory is not None:
        with session_factory.begin() as session:
            item = session.scalar(select(EvidenceRecord).where(EvidenceRecord.external_id == evidence_id))
            if not item:
                raise HTTPException(404, "Evidence not found")
            if item.workflow_status not in {"UPLOADED", "ANALYSIS_FAILED"}:
                raise HTTPException(409, f"Cannot analyze evidence in {item.workflow_status} state")
            item.workflow_status, item.analysis_status = "ANALYZING", "PROCESSING"
            try:
                result = analysis_provider.analyze(evidence_storage.retrieve(item.storage_uri), item.mime_type)
            except Exception as exc:
                item.workflow_status, item.analysis_status = "ANALYSIS_FAILED", "FAILED"
                raise HTTPException(422, f"Evidence analysis failed: {exc}") from exc
            item.extraction = result.extraction
            # Resolved from the ledger. These expectations used to be six literals written
            # here that happened to equal what the mock extractor returned, so every
            # document reconciled against the same values and the comparison proved nothing.
            item.reconciliation = evidence_reconciliation.reconcile(
                session,
                project_ref=item.project_ref,
                extraction=result.extraction,
            )
            metadata = dict(item.metadata_json or {})
            metadata["providerMetadata"] = {"provider": result.provider, "model": result.model, "processedAt": result.processed_at, "rawResponse": result.raw_response}
            item.metadata_json = metadata
            item.workflow_status, item.analysis_status = "ANALYZED", "COMPLETED"
            session.flush()
            return evidence_record_response(item)
    item = store.evidence.get(evidence_id)
    if not item:
        raise HTTPException(404, "Evidence not found")
    if item["workflowStatus"] not in {"UPLOADED", "ANALYSIS_FAILED"}:
        raise HTTPException(409, f"Cannot analyze evidence in {item['workflowStatus']} state")
    item["workflowStatus"] = "ANALYZING"
    item["analysisStatus"] = "PROCESSING"
    try:
        content = evidence_storage.retrieve(item["storageUri"])
        result = analysis_provider.analyze(content, item["mimeType"])
    except Exception as exc:
        item["workflowStatus"] = "ANALYSIS_FAILED"
        item["analysisStatus"] = "FAILED"
        raise HTTPException(422, f"Evidence analysis failed: {exc}") from exc
    item["extraction"] = result.extraction
    item["providerMetadata"] = {
        "provider": result.provider,
        "model": result.model,
        "processedAt": result.processed_at,
        "rawResponse": result.raw_response,
    }
    item["workflowStatus"] = "ANALYZED"
    item["analysisStatus"] = "COMPLETED"
    # The in-memory demo store has no ledger to resolve against.
    item["reconciliation"] = {
        "status": "UNMATCHED",
        "checks": [
            {
                "check": "TRANSACTION_REFERENCE",
                "result": "FAIL",
                "message": "Reconciliation needs the financial ledger; run with a database",
            }
        ],
        "reasons": ["Reconciliation requires PERSISTENCE_MODE=postgres"],
    }
    return item


def _require_confirmation(analyzed: dict[str, Any] | None, confirmed: list[str]) -> list[str]:
    """The fields a person confirmed, refusing the review when any flagged one is missing.

    Checked against the extraction as the model produced it, not the one being submitted:
    an operator who edits amountMinor to something they did not read off the document must
    still say so, and a client could otherwise clear a flag by changing the value.
    """
    outstanding = sorted(set(review_required_fields(analyzed or {})) - set(confirmed))
    if outstanding:
        raise HTTPException(
            422,
            detail={
                "code": "CONFIRMATION_REQUIRED",
                "message": "These fields must be confirmed by a person before registration",
                "fields": outstanding,
            },
        )
    return sorted(set(confirmed) & set(SCHEMA_FIELDS))


@app.get("/data-subjects/{subject_reference}")
def data_subject_record(subject_reference: str, user: CurrentUser = None):
    """Everything held about one person, for answering a subject access request.

    Scoped to the controller: an operator sees the subjects of programmes its own
    organisation is answerable for, and an administrator sees all of them. It reports what
    is held rather than the contents, so this cannot become a way to read every restricted
    object in the system by guessing a reference.
    """
    actor = actor_for(require_user(user, {Role.OPERATOR, Role.ADMIN}))
    if session_factory is None:
        raise HTTPException(503, "This requires PERSISTENCE_MODE=postgres")
    service = DataProtectionApplicationService(evidence_storage)
    with session_factory() as session:
        return service.subject_record(
            session, actor=actor, subject_reference=subject_reference
        )


@app.post("/evidence/{evidence_id}/data-protection", status_code=201)
def declare_data_protection(
    request: Request,
    evidence_id: str,
    body: DataProtectionRequest,
    user: CurrentUser = None,
):
    """Record what makes holding this evidence lawful, and who is answerable for it."""
    actor = actor_for(require_user(user, {Role.OPERATOR, Role.ADMIN}))
    _require_evidence_management(evidence_id, user)
    if session_factory is None:
        raise HTTPException(503, "Recording this requires PERSISTENCE_MODE=postgres")
    service = DataProtectionApplicationService(evidence_storage)
    return _tenant_write(
        lambda session: service.declare(
            session,
            actor=actor,
            evidence_id=evidence_id,
            lawful_basis=body.lawfulBasis,
            special_category=body.specialCategory,
            controller_org_ref=body.controllerOrgRef,
            joint_controller_org_ref=body.jointControllerOrgRef,
            subject_reference=body.subjectReference,
            purpose=body.purpose,
            retain_until=body.retainUntil,
            correlation_id=request.state.correlation_id,
        )
    )


@app.post("/evidence/{evidence_id}/erase")
def erase_evidence(
    request: Request,
    evidence_id: str,
    body: ErasureRequest,
    user: CurrentUser = None,
):
    """Destroy the key, making the object unrecoverable, and restate what rested on it.

    The onchain commitment is not withdrawn and the response says so. It records that
    these bytes were once committed, which remains true after the bytes are gone.
    """
    actor = actor_for(require_user(user, {Role.OPERATOR, Role.ADMIN}))
    _require_evidence_management(evidence_id, user)
    if session_factory is None:
        raise HTTPException(503, "Erasure requires PERSISTENCE_MODE=postgres")
    service = DataProtectionApplicationService(evidence_storage)
    return _tenant_write(
        lambda session: service.erase(
            session,
            actor=actor,
            evidence_id=evidence_id,
            reason=body.reason,
            correlation_id=request.state.correlation_id,
        )
    )


@app.post("/evidence/{evidence_id}/review")
def review_evidence(
    evidence_id: str,
    body: EvidenceReviewRequest,
    user: CurrentUser = None,
):
    require_user(user, {Role.OPERATOR, Role.ADMIN})
    _require_evidence_management(evidence_id, user)
    if session_factory is not None:
        with session_factory.begin() as session:
            item = session.scalar(select(EvidenceRecord).where(EvidenceRecord.external_id == evidence_id))
            if not item:
                raise HTTPException(404, "Evidence not found")
            if item.workflow_status != "ANALYZED":
                raise HTTPException(409, "Only ANALYZED evidence can be reviewed")
            confirmed = _require_confirmation(item.extraction, body.confirmed)
            reviewed_extraction = body.extraction.model_dump(mode="json")
            item.extraction = reviewed_extraction
            item.reconciliation = evidence_reconciliation.reconcile(
                session,
                project_ref=item.project_ref,
                extraction=reviewed_extraction,
            )
            item.workflow_status = "REVIEWED"
            metadata = dict(item.metadata_json or {})
            metadata["reviewedBy"] = "Global Water Initiative"
            metadata["confirmedFields"] = confirmed
            item.metadata_json = metadata
            session.flush()
            return evidence_record_response(item)
    item = store.evidence.get(evidence_id)
    if not item:
        raise HTTPException(404, "Evidence not found")
    if item["workflowStatus"] != "ANALYZED":
        raise HTTPException(409, "Only ANALYZED evidence can be reviewed")
    item["confirmedFields"] = _require_confirmation(item.get("extraction"), body.confirmed)
    item["extraction"] = body.extraction.model_dump(mode="json")
    item["workflowStatus"] = "REVIEWED"
    item["reviewedBy"] = "Global Water Initiative"
    return item


@app.post("/evidence/{evidence_id}/register", status_code=202)
def register_evidence(
    request: Request,
    evidence_id: str,
    background_tasks: BackgroundTasks,
    user: CurrentUser = None,
    idempotency_key: str | None = Header(default=None),
):
    actor = actor_for(require_user(user, {Role.OPERATOR, Role.ADMIN}))
    _require_evidence_management(evidence_id, user)
    key = require_idempotency(idempotency_key)
    if session_factory is not None:
        service = EvidenceApplicationService(settings.chain_id)
        try:
            with session_factory.begin() as session:
                response = service.request_registration(
                    session,
                    actor=actor,
                    evidence_id=evidence_id,
                    correlation_id=request.state.correlation_id,
                    idempotency_key=key,
                )
            background_tasks.add_task(process_backend_operation, UUID(response["operationId"]))
            return response
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except (ValueError, DomainConflictError) as exc:
            raise HTTPException(409, str(exc)) from exc
    item = store.evidence.get(evidence_id)
    if not item:
        raise HTTPException(404, "Evidence not found")
    fingerprint = f"REGISTER:{evidence_id}:{item['contentHash']}"
    existing = store.idempotency.get(key)
    if existing:
        try:
            return store.remember_idempotent(key, fingerprint, {})
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
    if item["workflowStatus"] != "REVIEWED":
        raise HTTPException(409, "Evidence must be REVIEWED before registration")
    operation_id = f"bcop-register-{evidence_id}"
    operation = {
        "operationId": operation_id,
        "entityId": evidence_id,
        "status": "SUBMITTED",
        "transactionHash": "0x" + sha256_bytes(evidence_id.encode()).split(":", 1)[1],
        "expectedEvent": "EvidenceRegistered",
        "correlationId": request.state.correlation_id,
    }
    store.evidence_operations[operation_id] = operation
    item["workflowStatus"] = "REGISTRATION_PENDING"
    item["blockchainStatus"] = "SUBMITTED"
    return store.remember_idempotent(key, fingerprint, operation)


@app.post("/blockchain/operations/{operation_id}/confirm-evidence-demo")
def confirm_evidence_registration(operation_id: str, user: CurrentUser = None):
    require_user(user, {Role.ADMIN, Role.OPERATOR})
    _refuse_demo_store_route()
    operation = store.evidence_operations.get(operation_id)
    if not operation:
        raise HTTPException(404, "Blockchain operation not found")
    item = store.evidence[operation["entityId"]]
    operation.update(
        {"status": "CONFIRMED", "confirmations": 1, "eventValidated": True, "blockNumber": 1001}
    )
    item["workflowStatus"] = "REGISTERED_ONCHAIN"
    item["blockchainStatus"] = "CONFIRMED"
    item["blockchainReference"] = {
        "transactionHash": operation["transactionHash"],
        "blockNumber": operation["blockNumber"],
    }
    return {"evidence": item, "blockchainOperation": operation}


def _evidence_operating_org(evidence_id: str) -> str | None:
    """The organisation that operates the program this evidence belongs to."""
    if not session_factory:
        return None
    with session_factory() as session:
        record = session.scalar(
            select(EvidenceRecord).where(EvidenceRecord.external_id == evidence_id)
        )
        if record is None:
            return None
        project = session.scalar(
            select(DomainEntityRecord).where(
                DomainEntityRecord.external_id == record.project_ref,
                DomainEntityRecord.entity_type == "PROJECT",
            )
        )
        program = session.get(ProgramRecord, project.program_id) if project else None
        return program.operator_org_ref if program else None


def _evidence_visibility(evidence_id: str) -> str | None:
    if session_factory:
        with session_factory() as session:
            record = session.scalar(
                select(EvidenceRecord).where(EvidenceRecord.external_id == evidence_id)
            )
            return record.visibility if record else None
    item = store.evidence.get(evidence_id)
    return item["visibility"] if item else None


def _operating_org_for_program(program_id: str) -> str:
    """The organisation that operates a program, or 404 if there is no such program."""
    if not session_factory:
        raise HTTPException(503, "Database is not configured")
    with session_factory() as session:
        program = session.scalar(
            select(ProgramRecord).where(ProgramRecord.slug == program_id)
        )
        if program is None:
            raise HTTPException(404, "Program not found")
        return program.operator_org_ref


def _require_program_management(program_id: str, user: AuthenticatedUser | None) -> None:
    """An operator may write to the ledger of its own program only.

    Import was role-checked but never ownership-checked, while every other operator
    mutation resolves ownership. The rows it writes are the counterparts evidence
    reconciles against, and reconciliation is a required verification requirement -- so an
    outside operator could push another organisation's claim across a policy gate, or
    block it by exhausting the allocation.
    """
    if user is None:
        raise _unauthenticated()
    if user.role == Role.ADMIN:
        return
    if _operating_org_for_program(program_id) != user.organization_external_id:
        raise HTTPException(403, "Program belongs to another operating organisation")


def _require_project_management(
    project_id: str, user: AuthenticatedUser | None
) -> None:
    """An operator may mutate only projects belonging to its organisation."""
    if user is None:
        raise _unauthenticated()
    if user.role == Role.ADMIN:
        return
    if session_factory:
        with session_factory() as session:
            project = session.scalar(
                select(DomainEntityRecord).where(
                    DomainEntityRecord.external_id == project_id,
                    DomainEntityRecord.entity_type == "PROJECT",
                )
            )
            program = session.get(ProgramRecord, project.program_id) if project else None
            if program is None:
                raise HTTPException(404, "Project not found")
            if program.operator_org_ref != user.organization_external_id:
                raise HTTPException(403, "Project belongs to another operating organisation")
            return
    if project_id != "project-water-12" or user.organization_external_id != "org-global-water":
        raise HTTPException(403, "Project belongs to another operating organisation")


def _require_evidence_management(
    evidence_id: str, user: AuthenticatedUser | None
) -> None:
    if session_factory:
        with session_factory() as session:
            evidence = session.scalar(
                select(EvidenceRecord).where(EvidenceRecord.external_id == evidence_id)
            )
            if evidence is None:
                raise HTTPException(404, "Evidence not found")
            project_ref = evidence.project_ref
    else:
        evidence = store.evidence.get(evidence_id)
        if evidence is None:
            raise HTTPException(404, "Evidence not found")
        project_ref = evidence["projectId"]
    _require_project_management(project_ref, user)


def _require_evidence_visibility(
    evidence_id: str, user: AuthenticatedUser | None
) -> None:
    """Enforce the visibility recorded on the evidence.

    The field was previously stored and reported but never checked, so RESTRICTED and
    INTERNAL evidence was readable by anyone. PUBLIC stays open without an account,
    because public verifiability is the point of the product.
    """
    visibility = _evidence_visibility(evidence_id)
    if visibility is None:
        raise HTTPException(404, "Evidence not found")
    if visibility == "PUBLIC":
        return
    if user is None:
        raise _unauthenticated()
    if visibility == "INTERNAL":
        # Role alone is not enough: any operator would otherwise read every other
        # organisation's internal evidence, including the extracted document fields.
        # docs/security.md has always said this is limited to the operating organisation.
        operating_org = _evidence_operating_org(evidence_id)
        permitted = user.role == Role.ADMIN or (
            operating_org is not None
            and operating_org == user.organization_external_id
        )
        if not permitted:
            raise HTTPException(
                403,
                detail={
                    "code": "EVIDENCE_FORBIDDEN",
                    "message": "This evidence is internal to the operating organisation",
                },
            )


def _seeded_evidence_uri(evidence_id: str) -> str:
    """Resolve the seeded showcase object, creating it if storage is empty.

    Integrity verification reads the stored bytes back, so the object has to exist. It is
    created only when absent: after the tampering demo the altered bytes must survive until
    an explicit demo reset, or the mismatch would silently repair itself.
    """
    uri = evidence_storage.uri_for(evidence_id)
    if not evidence_storage.exists(uri):
        evidence_storage.store(evidence_id, INVOICE_BYTES)
    return uri


def _integrity_response(
    evidence_id: str,
    expected: str,
    current: str,
    byte_count: int,
    registered_at: str | None = None,
) -> dict[str, Any]:
    matched = current == expected
    return {
        "registeredAt": registered_at,
        "evidenceId": evidence_id,
        "status": "MATCH" if matched else "MISMATCH",
        "expected": expected,
        "current": current,
        # How many bytes were actually read back and hashed. The interface shows the work
        # the check did, and a count it invented would be the one thing it must not do.
        "byteCount": byte_count,
        "explanation": "The stored evidence is byte-for-byte consistent with its registered "
        "commitment. This proves the bytes have not changed since registration; it does not "
        "prove the document states the truth."
        if matched
        else "The stored evidence no longer hashes to its registered commitment. The commitment "
        "cannot be changed retroactively, so the divergence is detectable.",
    }


def _registered_evidence_hash(record: EvidenceRecord) -> str:
    """Resolve the immutable commitment from its confirmed registry receipt when configured."""
    metadata = record.metadata_json or {}
    reference = metadata.get("blockchainReference") or {}
    transaction_hash = reference.get("transactionHash")
    if settings.registry_address:
        if not transaction_hash:
            raise HTTPException(409, "Confirmed evidence has no registry transaction reference")
        blockchain = EvmBlockchainService.from_foundry_artifact(
            rpc_url=settings.rpc_url,
            contract_address=settings.registry_address,
            artifact_path=settings.contract_artifact_path,
        )
        observation = blockchain.get_transaction(transaction_hash)
        if observation is None:
            raise HTTPException(409, "Registry transaction is not available")
        commitment = commitment_from_receipt(observation, record.external_id)
        if not commitment:
            raise HTTPException(409, "Registry receipt has no matching evidence commitment")
        return commitment
    commitment = metadata.get("registeredContentHash")
    if not commitment:
        raise HTTPException(409, "Evidence has no independently indexed registered commitment")
    return str(commitment)


@app.post("/evidence/{evidence_id}/verify-integrity")
def integrity(request: Request, evidence_id: str, user: CurrentUser = None):
    # Deliberately public: verifying that evidence still matches its published commitment
    # is the product's central claim, and a donor must be able to check it without an
    # account. Visibility is enforced instead -- non-public evidence requires a session.
    _require_evidence_visibility(evidence_id, user)
    if session_factory:
        with session_factory.begin() as session:
            record = session.scalar(
                select(EvidenceRecord).where(EvidenceRecord.external_id == evidence_id)
            )
            if record is None:
                raise HTTPException(404, "Evidence not found")
            try:
                content = evidence_storage.retrieve(record.storage_uri)
            except EvidenceUnrecoverable as exc:
                # Erased, not broken and not absent. Saying "could not be read back" here
                # would describe a fault, when what happened is that someone exercised a
                # right and this system did what it said it would.
                record.integrity_status = "UNRECOVERABLE"
                raise HTTPException(
                    410,
                    detail={
                        "code": "EVIDENCE_ERASED",
                        "message": "This evidence was erased at the request of its subject "
                        "or by its retention schedule. The onchain commitment remains and "
                        "still records that these bytes were once committed.",
                    },
                ) from exc
            except (OSError, ValueError) as exc:
                raise HTTPException(
                    409,
                    detail={
                        "code": "EVIDENCE_UNREADABLE",
                        "message": "The stored evidence object could not be read back",
                    },
                ) from exc
            registered_hash = _registered_evidence_hash(record)
            matched, current = verify_integrity(content, registered_hash)
            record.integrity_status = "MATCH" if matched else "MISMATCH"
            integrity_checks.labels(result=record.integrity_status).inc()
            # The mismatch is detected on the evidence, but it is the claim that carries
            # the trust signal a donor reads. Restate it in the same transaction, or the
            # claim keeps its verified badge above a failing requirement list.
            restate_claims_for(session, evidence_id, request.state.correlation_id)
            return _integrity_response(
                evidence_id,
                registered_hash,
                current,
                len(content),
                ((record.metadata_json or {}).get("blockchainReference") or {}).get(
                    "registeredAt"
                ),
            )
    item = store.evidence.get(evidence_id)
    if not item:
        raise HTTPException(404, "Evidence not found")
    item["storageUri"] = item.get("storageUri") or _seeded_evidence_uri(evidence_id)
    try:
        content = evidence_storage.retrieve(item["storageUri"])
    except (OSError, ValueError, KeyError) as exc:
        raise HTTPException(
            409,
            detail={
                "code": "EVIDENCE_UNREADABLE",
                "message": "The stored evidence object could not be read back",
            },
        ) from exc
    _, current = verify_integrity(content, item["contentHash"])
    response = _integrity_response(
        evidence_id, item["contentHash"], current, len(content)
    )
    item["integrityStatus"] = response["status"]
    return response


@app.post("/demo/evidence/{evidence_id}/tamper")
def tamper(request: Request, evidence_id: str, user: CurrentUser = None):
    require_user(user, {Role.ADMIN})
    if settings.demo_mode == "sepolia":
        raise HTTPException(409, "Refusing to alter evidence in a public demo run")
    storage_uri = _evidence_storage_uri(evidence_id)
    evidence_storage.overwrite(storage_uri, TAMPERED_INVOICE_BYTES)
    # The same correlation identifier covers the tamper and the check that detects it.
    return integrity(request, evidence_id, user=user)


def _evidence_storage_uri(evidence_id: str) -> str:
    if session_factory:
        with session_factory() as session:
            record = session.scalar(
                select(EvidenceRecord).where(EvidenceRecord.external_id == evidence_id)
            )
            if record is None:
                raise HTTPException(404, "Evidence not found")
            return record.storage_uri
    item = store.evidence.get(evidence_id)
    if item is None:
        raise HTTPException(404, "Evidence not found")
    item["storageUri"] = item.get("storageUri") or _seeded_evidence_uri(evidence_id)
    return item["storageUri"]


@app.post("/verification-requests/{claim_id}/submit-attestation")
def submit_attestation(
    claim_id: str,
    user: CurrentUser = None,
    idempotency_key: str | None = Header(default=None),
):
    require_user(user, {Role.VERIFIER})
    _refuse_demo_store_route()
    if claim_id != store.claim["id"]:
        raise HTTPException(404, "Claim not found")
    if not idempotency_key:
        raise HTTPException(
            400,
            detail={"code": "IDEMPOTENCY_KEY_REQUIRED", "message": "Idempotency-Key is required"},
        )
    return store.submit_verification()


@app.post("/verification-requests/{claim_id}/intent", status_code=201)
def create_verifier_intent(
    request: Request,
    claim_id: str,
    user: CurrentUser = None,
    idempotency_key: str | None = Header(default=None),
):
    verifier = require_user(user, {Role.VERIFIER})
    if not verifier.wallet_address:
        raise HTTPException(
            403,
            detail={
                "code": "WALLET_NOT_VERIFIED",
                "message": "Prove control of your wallet before requesting verification",
            },
        )
    key = require_idempotency(idempotency_key)
    if session_factory is None:
        raise HTTPException(409, "Durable verifier intents require PERSISTENCE_MODE=postgres")
    if not settings.registry_address:
        raise HTTPException(503, "IMPACT_REGISTRY_ADDRESS is required")
    _assert_wallet_may_verify(verifier.wallet_address)
    service = VerificationApplicationService(settings.chain_id, settings.registry_address)
    try:
        with session_factory.begin() as session:
            return service.create_verifier_intent(
                session,
                actor=actor_for(verifier),
                claim_id=claim_id,
                correlation_id=request.state.correlation_id,
                idempotency_key=key,
            )
    except AuthorizationError as exc:
        raise HTTPException(403, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except DomainConflictError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/verification-requests/{claim_id}/reject")
def reject_verification_request(
    request: Request,
    claim_id: str,
    body: VerificationDecisionRequest,
    user: CurrentUser = None,
    idempotency_key: str | None = Header(default=None),
):
    verifier = require_user(user, {Role.VERIFIER})
    key = require_idempotency(idempotency_key)
    if session_factory is None:
        raise HTTPException(409, "Durable verification decisions require PERSISTENCE_MODE=postgres")
    service = VerificationApplicationService(settings.chain_id, settings.registry_address)
    try:
        with session_factory.begin() as session:
            return service.reject_claim(
                session,
                actor=actor_for(verifier),
                claim_id=claim_id,
                reason=body.reason,
                correlation_id=request.state.correlation_id,
                idempotency_key=key,
            )
    except AuthorizationError as exc:
        raise HTTPException(403, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except DomainConflictError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/blockchain/operations/{operation_id}/submitted")
def record_wallet_submission(
    request: Request,
    operation_id: UUID,
    body: WalletSubmissionRequest,
    background_tasks: BackgroundTasks,
    user: CurrentUser = None,
):
    verifier = require_user(user, {Role.VERIFIER})
    if session_factory is None:
        raise HTTPException(409, "Durable wallet submissions require PERSISTENCE_MODE=postgres")
    service = VerificationApplicationService(settings.chain_id, settings.registry_address)
    try:
        with session_factory.begin() as session:
            response = service.record_wallet_submission(
                session,
                actor=actor_for(verifier),
                operation_id=operation_id,
                transaction_hash=body.transactionHash,
                correlation_id=request.state.correlation_id,
            )
        background_tasks.add_task(monitor_wallet_operation, operation_id)
        return response
    except AuthorizationError as exc:
        raise HTTPException(403, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (ValueError, DomainConflictError) as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/blockchain/operations/{operation_id}")
def blockchain_operation(operation_id: UUID):
    if session_factory is None:
        raise HTTPException(409, "Durable operations require PERSISTENCE_MODE=postgres")
    with session_factory() as session:
        operation = session.get(BlockchainOperationRecord, operation_id)
        if operation is None:
            raise HTTPException(404, "Blockchain operation not found")
        claim_status = None
        if operation.operation_type == "CREATE_VERIFIER_ATTESTATION":
            # Report the status of the claim this attestation is actually about. The
            # showcase claim id was hardcoded here, so polling any verifier operation
            # returned the showcase claim's status regardless of what was attested.
            attestation = session.scalar(
                select(AttestationRecord).where(
                    AttestationRecord.external_id == operation.entity_id
                )
            )
            if attestation:
                claim = TransparencyReadRepository(session).claim(attestation.subject_id)
                claim_status = claim["status"]
        return {
            "operationId": str(operation.id),
            "entityId": operation.entity_id,
            "operationType": operation.operation_type,
            "status": operation.status,
            "transactionHash": operation.transaction_hash,
            "confirmations": operation.confirmations,
            "expectedEvent": operation.expected_event,
            "error": operation.error,
            "claimStatus": claim_status,
        }


@app.post("/blockchain/operations/{operation_id}/confirm-demo")
def confirm_attestation(operation_id: str, user: CurrentUser = None):
    require_user(user, {Role.ADMIN, Role.VERIFIER})
    _refuse_demo_store_route()
    if not store.pending_tx or operation_id != store.pending_tx["operationId"]:
        raise HTTPException(404, "Blockchain operation not found")
    try:
        return store.confirm_verification()
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/blockchain/transactions/{transaction_hash}")
def blockchain_transaction(transaction_hash: str):
    if (
        not store.pending_tx
        or transaction_hash.lower() != store.pending_tx["transactionHash"].lower()
    ):
        raise HTTPException(404, "Blockchain transaction not found")
    return store.pending_tx


@app.post("/demo/reset")
def reset(user: CurrentUser = None):
    require_user(user, {Role.ADMIN})
    if settings.demo_mode == "sepolia":
        raise HTTPException(409, "Sepolia is immutable; create a new demo run")
    store.reset()
    # Restore the pristine showcase bytes so the tampering demo can be run again.
    evidence_storage.overwrite(evidence_storage.uri_for("ev-inv-8291"), INVOICE_BYTES)
    claim_status = store.claim["status"]
    if session_factory:
        with session_factory.begin() as session:
            reset_read_model(session, evidence_storage)
        # Reseeding drops the registry reference, which left the evidence marked CONFIRMED
        # with nothing pointing at the registry -- so the donor-facing integrity check, the
        # one thing this product exists to demonstrate, answered 409 for the rest of the
        # run. The commitment is still on chain, and this recovers the reference.
        register_seeded_evidence(settings)
        with session_factory() as session:
            claim_status = TransparencyReadRepository(session).claim(SEEDED_CLAIM_ID)["status"]
    return {"reset": True, "claimStatus": claim_status}
