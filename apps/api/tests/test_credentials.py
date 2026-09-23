"""The first real secret this platform keeps for a customer.

Sealed under the key that protects evidence at rest, decrypted only at the moment of use,
and never able to move money -- ADR-014 is a statement about capability, not restraint.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest
from cryptography.exceptions import InvalidTag
from sqlalchemy import select

from impactgraph.credentials import (
    PaymentScopeRefused,
    refresh_token_for,
    revoke,
    seal,
    store,
    unseal,
)
from impactgraph.persistence import ProviderCredentialRecord
from tests.support import memory_session

KEY = os.urandom(32)
TOKEN = "refresh-abcdef0123456789"


@pytest.fixture
def session():
    return memory_session()


def test_the_token_is_not_in_the_row(session):
    with session.begin():
        store(
            session,
            key=KEY,
            organization_ref="org-global-water",
            provider="truelayer",
            refresh_token=TOKEN,
            scopes="info accounts transactions",
            expires_at=None,
        )
    record = session.scalar(select(ProviderCredentialRecord))
    assert TOKEN.encode() not in record.sealed_refresh_token
    assert refresh_token_for(
        session, key=KEY, organization_ref="org-global-water", provider="truelayer"
    ) == TOKEN


def test_a_sealed_token_cannot_be_moved_to_another_organisation():
    """Without binding, a row could be copied between organisations and still decrypt, so
    one connection would answer for somebody else's."""
    sealed = seal(KEY, TOKEN, associated="org-global-water:truelayer")
    assert unseal(KEY, sealed, associated="org-global-water:truelayer") == TOKEN
    with pytest.raises(InvalidTag):
        unseal(KEY, sealed, associated="org-other-water:truelayer")


def test_a_grant_that_could_move_money_is_refused(session):
    """What is asked for and what is granted are different things, and only the second
    matters. ADR-014 has to be about capability rather than about good intentions."""
    for scope in ("payments", "payment:create", "accounts transfer", "write:payouts"):
        with pytest.raises(PaymentScopeRefused, match="move money"), session.begin():
            store(
                session,
                key=KEY,
                organization_ref="org-global-water",
                provider="truelayer",
                refresh_token=TOKEN,
                scopes=scope,
                expires_at=None,
            )


def test_read_only_scopes_are_accepted(session):
    with session.begin():
        record = store(
            session,
            key=KEY,
            organization_ref="org-global-water",
            provider="truelayer",
            refresh_token=TOKEN,
            scopes="info accounts balance transactions offline_access",
            expires_at=datetime(2026, 12, 1, tzinfo=UTC),
        )
        assert record.scopes.startswith("info")


def test_reconnecting_replaces_the_secret_rather_than_adding_a_second(session):
    """A second row would leave the old token readable for as long as nobody noticed."""
    with session.begin():
        store(session, key=KEY, organization_ref="org-a", provider="truelayer",
              refresh_token="first-token", scopes="accounts", expires_at=None)
    with session.begin():
        store(session, key=KEY, organization_ref="org-a", provider="truelayer",
              refresh_token="second-token", scopes="accounts", expires_at=None)

    rows = list(session.scalars(select(ProviderCredentialRecord)))
    assert len(rows) == 1
    assert refresh_token_for(
        session, key=KEY, organization_ref="org-a", provider="truelayer"
    ) == "second-token"


def test_revoking_destroys_the_token_rather_than_flagging_it(session):
    with session.begin():
        store(session, key=KEY, organization_ref="org-a", provider="truelayer",
              refresh_token=TOKEN, scopes="accounts", expires_at=None)
    with session.begin():
        assert revoke(session, organization_ref="org-a", provider="truelayer") is True

    record = session.scalar(select(ProviderCredentialRecord))
    assert record.sealed_refresh_token == b""
    assert record.revoked_at is not None
    with pytest.raises(LookupError):
        refresh_token_for(session, key=KEY, organization_ref="org-a", provider="truelayer")


def test_a_rotated_key_cannot_read_what_the_old_one_sealed(session):
    """Which is the point of rotation, and the reason the procedure has to re-seal rather
    than simply swap the key."""
    with session.begin():
        store(session, key=KEY, organization_ref="org-a", provider="truelayer",
              refresh_token=TOKEN, scopes="accounts", expires_at=None)
    with pytest.raises(InvalidTag):
        refresh_token_for(
            session, key=os.urandom(32), organization_ref="org-a", provider="truelayer"
        )


def test_a_key_of_the_wrong_length_is_refused_rather_than_padded():
    with pytest.raises(ValueError, match="32 bytes"):
        seal(os.urandom(16), TOKEN, associated="org-a:truelayer")


def test_rotation_re_seals_a_credential_under_the_new_key(session):
    """The runbook says the procedure re-seals rather than swaps. This is the part of it
    that has to be true for a bank connection rather than only for evidence."""
    import os as _os

    from impactgraph.credentials import seal as _seal
    from impactgraph.credentials import unseal as _unseal

    new_key = _os.urandom(32)
    with session.begin():
        store(session, key=KEY, organization_ref="org-a", provider="truelayer",
              refresh_token=TOKEN, scopes="accounts", expires_at=None)

    with session.begin():
        record = session.scalar(select(ProviderCredentialRecord))
        token = _unseal(KEY, record.sealed_refresh_token, associated="org-a:truelayer")
        record.sealed_refresh_token = _seal(new_key, token, associated="org-a:truelayer")

    assert refresh_token_for(
        session, key=new_key, organization_ref="org-a", provider="truelayer"
    ) == TOKEN
    with pytest.raises(InvalidTag):
        refresh_token_for(session, key=KEY, organization_ref="org-a", provider="truelayer")
