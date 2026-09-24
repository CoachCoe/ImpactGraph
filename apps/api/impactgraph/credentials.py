"""Bank connections held on an organisation's behalf.

The first real secret this platform keeps for a customer, so the handling is deliberate:
sealed with AES-GCM under the configured key-encryption key, decrypted only at the moment
of use, and never returned by any route.

ADR-014 says this system observes money and does not move it. A credential that could move
it would make that a promise about restraint rather than about capability, so a grant
carrying a payment scope is refused at the point it is stored.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.orm import Session

from .persistence import ProviderCredentialRecord

#: Anything that could initiate a payment. Refused rather than merely not requested: what
#: is asked for and what is granted are different things, and only the second matters.
FORBIDDEN_SCOPE_MARKERS = ("payment", "payout", "transfer", "mandate", "write")


class PaymentScopeRefused(ValueError):
    """A provider granted more than observation. Storing it would make ADR-014 a habit."""


def _cipher(key: bytes) -> AESGCM:
    if len(key) != 32:
        raise ValueError("The credential encryption key must be 32 bytes")
    return AESGCM(key)


def seal(key: bytes, token: str, *, associated: str) -> bytes:
    """Encrypt a refresh token, bound to the organisation and provider it belongs to.

    The binding matters: without it a sealed token could be moved between rows and would
    still decrypt, so one organisation's connection could be made to answer for another's.
    """
    nonce = secrets.token_bytes(12)
    return nonce + _cipher(key).encrypt(nonce, token.encode("utf-8"), associated.encode("utf-8"))


def unseal(key: bytes, sealed: bytes, *, associated: str) -> str:
    return _cipher(key).decrypt(sealed[:12], sealed[12:], associated.encode("utf-8")).decode("utf-8")


def _binding(organization_ref: str, provider: str) -> str:
    return f"{organization_ref}:{provider}"


def store(
    session: Session,
    *,
    key: bytes,
    organization_ref: str,
    provider: str,
    refresh_token: str,
    scopes: str,
    expires_at: datetime | None,
) -> ProviderCredentialRecord:
    granted = [item.strip().lower() for item in scopes.split() if item.strip()]
    offending = [
        scope for scope in granted if any(marker in scope for marker in FORBIDDEN_SCOPE_MARKERS)
    ]
    if offending:
        raise PaymentScopeRefused(
            f"The provider granted {', '.join(offending)}, which could move money. This "
            "system observes financial activity and does not initiate it (ADR-014); "
            "reconnect requesting read-only access."
        )

    existing = session.scalar(
        select(ProviderCredentialRecord).where(
            ProviderCredentialRecord.organization_ref == organization_ref,
            ProviderCredentialRecord.provider == provider,
        )
    )
    sealed = seal(key, refresh_token, associated=_binding(organization_ref, provider))
    now = datetime.now(UTC)
    if existing is not None:
        # Reconnecting replaces the secret in place: a second row would leave the old
        # token readable for as long as nobody noticed it.
        existing.sealed_refresh_token = sealed
        existing.scopes = scopes
        existing.expires_at = expires_at
        existing.refreshed_at = now
        existing.revoked_at = None
        return existing

    record = ProviderCredentialRecord(
        organization_ref=organization_ref,
        provider=provider,
        sealed_refresh_token=sealed,
        scopes=scopes,
        expires_at=expires_at,
        refreshed_at=now,
    )
    session.add(record)
    return record


def refresh_token_for(
    session: Session, *, key: bytes, organization_ref: str, provider: str
) -> str:
    """The token, decrypted at the moment of use and never before."""
    record = session.scalar(
        select(ProviderCredentialRecord).where(
            ProviderCredentialRecord.organization_ref == organization_ref,
            ProviderCredentialRecord.provider == provider,
        )
    )
    if record is None or record.revoked_at is not None:
        raise LookupError("No active connection for that organisation and provider")
    return unseal(
        key, record.sealed_refresh_token, associated=_binding(organization_ref, provider)
    )


def revoke(session: Session, *, organization_ref: str, provider: str) -> bool:
    """Stop using a connection. The sealed token is destroyed, not merely flagged."""
    record = session.scalar(
        select(ProviderCredentialRecord).where(
            ProviderCredentialRecord.organization_ref == organization_ref,
            ProviderCredentialRecord.provider == provider,
        )
    )
    if record is None or record.revoked_at is not None:
        return False
    record.sealed_refresh_token = b""
    record.revoked_at = datetime.now(UTC)
    return True
