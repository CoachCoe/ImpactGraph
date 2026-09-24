"""Authentication: passwords, sessions, and proof of wallet control.

Replaces the previous model, in which the acting persona, organisation and wallet were
read from `X-Demo-Role`, `X-Actor-Id` and `X-Wallet-Address` request headers and believed.
Those were forgeable, so every role check was decorative and the separation-of-duties rule
that the operator may not verify its own claim compared two strings the caller chose.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from eth_account import Account
from eth_account.messages import encode_defunct
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import PUBLIC_CHAIN_IDS
from .domain import Role
from .persistence import OrganizationRecord, SessionRecord, UserRecord, WalletChallengeRecord

SESSION_COOKIE = "impactgraph_session"
SESSION_LIFETIME = timedelta(hours=12)
CHALLENGE_LIFETIME = timedelta(minutes=10)

_hasher = PasswordHasher()


class AuthenticationError(Exception):
    """The caller is not who they claim to be, or is not signed in at all."""


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:  # noqa: BLE001 -- a malformed stored hash must not authenticate
        return False
    return True


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: UUID
    email: str
    display_name: str
    organization_external_id: str
    organization_name: str
    role: Role
    wallet_address: str | None


def _to_authenticated(user: UserRecord, organization: OrganizationRecord) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        organization_external_id=organization.external_id,
        organization_name=organization.name,
        role=Role(user.role),
        wallet_address=user.wallet_address,
    )


def authenticate(session: Session, email: str, password: str) -> tuple[str, AuthenticatedUser]:
    """Verify credentials and open a session. Returns the raw token, which is never stored."""
    user = session.scalar(select(UserRecord).where(UserRecord.email == email.strip().lower()))
    # Hash even when the user is unknown, so a missing account and a wrong password take
    # comparable time and the response does not disclose which it was.
    stored = user.password_hash if user else _hasher.hash("no-such-user")
    if not verify_password(stored, password) or user is None or user.disabled:
        raise AuthenticationError("Email or password is incorrect")

    organization = session.get(OrganizationRecord, user.organization_id)
    if organization is None:
        raise AuthenticationError("The account is not attached to an organisation")

    token = secrets.token_urlsafe(48)
    session.add(
        SessionRecord(
            token_hash=_token_hash(token),
            user_id=user.id,
            expires_at=datetime.now(UTC) + SESSION_LIFETIME,
        )
    )
    return token, _to_authenticated(user, organization)


def resolve_session(session: Session, token: str | None) -> AuthenticatedUser | None:
    """Return the signed-in user for a token, or None. Never raises for a bad token."""
    if not token:
        return None
    record = session.scalar(
        select(SessionRecord).where(SessionRecord.token_hash == _token_hash(token))
    )
    if record is None or record.revoked_at is not None:
        return None
    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= datetime.now(UTC):
        return None
    user = session.get(UserRecord, record.user_id)
    if user is None or user.disabled:
        return None
    organization = session.get(OrganizationRecord, user.organization_id)
    if organization is None:
        return None
    return _to_authenticated(user, organization)


def revoke_session(session: Session, token: str | None) -> None:
    if not token:
        return
    record = session.scalar(
        select(SessionRecord).where(SessionRecord.token_hash == _token_hash(token))
    )
    if record is not None and record.revoked_at is None:
        record.revoked_at = datetime.now(UTC)


def issue_wallet_challenge(session: Session, user_id: UUID) -> str:
    """Mint a nonce the wallet must sign. Proof of control, not an assertion of it."""
    nonce = secrets.token_hex(16)
    session.add(
        WalletChallengeRecord(
            user_id=user_id,
            nonce=nonce,
            expires_at=datetime.now(UTC) + CHALLENGE_LIFETIME,
        )
    )
    return nonce


def challenge_message(nonce: str) -> str:
    return (
        "ImpactGraph wallet verification\n"
        f"nonce: {nonce}\n"
        "Signing proves you control this address. It authorises no transaction."
    )


def verify_wallet_signature(
    session: Session, user_id: UUID, nonce: str, signature: str
) -> str:
    """Recover the signing address from an EIP-191 signature over an unconsumed nonce.

    Returns the recovered address. The caller never supplies it, so it cannot be forged.
    """
    record = session.scalar(
        select(WalletChallengeRecord).where(WalletChallengeRecord.nonce == nonce)
    )
    if record is None or record.user_id != user_id:
        raise AuthenticationError("Unknown wallet challenge")
    if record.consumed_at is not None:
        raise AuthenticationError("This wallet challenge has already been used")
    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= datetime.now(UTC):
        raise AuthenticationError("This wallet challenge has expired")

    try:
        recovered = Account.recover_message(
            encode_defunct(text=challenge_message(nonce)), signature=signature
        )
    except Exception as exc:
        raise AuthenticationError("The wallet signature could not be verified") from exc

    record.consumed_at = datetime.now(UTC)
    return str(recovered)


# Development accounts created by `make seed`. The password is intentionally well known
# and printed by the seed command: these are demo personas on a local database, in the
# same spirit as Anvil's published keys. Never create them against a real deployment.
DEMO_PASSWORD = "impactgraph-demo"
DEMO_ORGANIZATIONS = (
    ("org-global-water", "Global Water Initiative", "OPERATOR"),
    ("org-impactverify", "ImpactVerify", "VERIFIER"),
    ("org-impactgraph", "ImpactGraph", "ADMIN"),
)
DEMO_USERS = (
    ("operator@globalwater.example", "Amina Otieno", "org-global-water", "OPERATOR", None),
    (
        "verifier@impactverify.example",
        "Lars Jensen",
        "org-impactverify",
        "VERIFIER",
        None,
    ),
    ("admin@impactgraph.example", "ImpactGraph Admin", "org-impactgraph", "ADMIN", None),
)


def provision_hosted_demo_accounts(session: Session, password: str) -> list[str]:
    """Create the three showcase personas with an operator-supplied secret.

    Hosted demos need the same roles as the local walkthrough, but must never inherit its
    published password. Existing users are deliberately left alone: rerunning deployment
    tooling must not silently rotate credentials or take ownership of a real account.
    """
    if len(password) < 12:
        raise ValueError("The hosted demo password must contain at least 12 characters")
    if password == DEMO_PASSWORD:
        raise ValueError("The published local demo password is forbidden on a hosted demo")

    created: list[str] = []
    organizations: dict[str, OrganizationRecord] = {}
    for external_id, name, kind in DEMO_ORGANIZATIONS:
        organization = session.scalar(
            select(OrganizationRecord).where(OrganizationRecord.external_id == external_id)
        )
        if organization is None:
            organization = OrganizationRecord(external_id=external_id, name=name, kind=kind)
            session.add(organization)
            session.flush()
            created.append(external_id)
        organizations[external_id] = organization

    password_hash = hash_password(password)
    for email, display_name, organization_ref, role, wallet in DEMO_USERS:
        if session.scalar(select(UserRecord).where(UserRecord.email == email)) is not None:
            continue
        session.add(
            UserRecord(
                email=email,
                display_name=display_name,
                password_hash=password_hash,
                organization_id=organizations[organization_ref].id,
                role=role,
                wallet_address=wallet,
            )
        )
        created.append(email)
    return created


def demo_accounts_permitted(settings: Any) -> str | None:
    """Why demo accounts must not be created here, or None when they may be.

    These accounts share a password that is a constant in this file, which is published.
    Anyone who can read the source can sign in as ADMIN to any deployment that has them,
    so the decision cannot rest on DEMO_MODE -- a string the caller chooses -- and it
    cannot live only in a deploy script that not every operator will run.
    """
    if settings.chain_id in PUBLIC_CHAIN_IDS:
        return f"chain {settings.chain_id} is a public network"
    if settings.demo_mode != "local":
        return f"demo mode is {settings.demo_mode!r} rather than 'local'"
    if settings.app_env == "production":
        return "APP_ENV is 'production'"
    return None


def seed_demo_accounts(session: Session) -> list[str]:
    """Create the demo organisations and users if they are absent. Idempotent."""
    created: list[str] = []
    organizations: dict[str, OrganizationRecord] = {}
    for external_id, name, kind in DEMO_ORGANIZATIONS:
        organization = session.scalar(
            select(OrganizationRecord).where(OrganizationRecord.external_id == external_id)
        )
        if organization is None:
            organization = OrganizationRecord(external_id=external_id, name=name, kind=kind)
            session.add(organization)
            session.flush()
            created.append(external_id)
        organizations[external_id] = organization

    for email, display_name, organization_ref, role, wallet in DEMO_USERS:
        if session.scalar(select(UserRecord).where(UserRecord.email == email)) is not None:
            continue
        session.add(
            UserRecord(
                email=email,
                display_name=display_name,
                password_hash=hash_password(DEMO_PASSWORD),
                organization_id=organizations[organization_ref].id,
                role=role,
                wallet_address=wallet,
            )
        )
        created.append(email)
    return created
