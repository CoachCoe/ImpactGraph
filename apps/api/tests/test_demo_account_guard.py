"""The demo accounts share a password that is a published constant.

`scripts/deploy.sh release` runs `seed`, and `seed` used to create these accounts
unconditionally. Anyone who could read the source could therefore sign in as ADMIN to any
deployment made the documented way -- and ADMIN bypasses project ownership, can overwrite
any stored evidence, and can delete the audit log.

The decision must not rest on DEMO_MODE, which is a string the caller chooses, nor live
only in a deploy script that not every operator runs.
"""

from __future__ import annotations

import pytest
from sqlalchemy import delete, select

from impactgraph.auth import (
    DEMO_PASSWORD,
    DEMO_USERS,
    demo_accounts_permitted,
    hash_password,
    provision_hosted_demo_accounts,
    verify_password,
)
from impactgraph.config import PUBLIC_CHAIN_IDS, Settings
from impactgraph.database import create_session_factory
from impactgraph.persistence import OrganizationRecord, UserRecord


def settings(**overrides) -> Settings:
    value = Settings.from_env()
    for key, item in overrides.items():
        object.__setattr__(value, key, item)
    return value


def test_a_local_development_stack_may_have_demo_accounts():
    assert demo_accounts_permitted(settings(chain_id=31337, demo_mode="local", app_env="development")) is None


@pytest.mark.parametrize("chain_id", sorted(PUBLIC_CHAIN_IDS))
def test_no_demo_accounts_on_a_public_chain_whatever_the_demo_mode_says(chain_id):
    # The misconfiguration that motivated this: DEMO_MODE left at 'local' while the chain
    # id points at a real network. The old guard keyed on DEMO_MODE and let this through.
    refusal = demo_accounts_permitted(
        settings(chain_id=chain_id, demo_mode="local", app_env="development")
    )
    assert refusal is not None
    assert str(chain_id) in refusal


def test_no_demo_accounts_in_production():
    refusal = demo_accounts_permitted(
        settings(chain_id=31337, demo_mode="local", app_env="production")
    )
    assert refusal is not None and "production" in refusal


def test_no_demo_accounts_outside_local_demo_mode():
    refusal = demo_accounts_permitted(
        settings(chain_id=31337, demo_mode="sepolia", app_env="development")
    )
    assert refusal is not None and "local" in refusal


def test_the_shared_password_is_still_a_published_constant():
    """If this ever becomes a secret, the guard above can be reconsidered. Until then the
    guard is the only thing standing between a published password and an ADMIN session."""
    assert DEMO_PASSWORD == "impactgraph-demo"


@pytest.mark.parametrize("password", ["short", DEMO_PASSWORD])
def test_hosted_provisioning_rejects_unsafe_passwords(password):
    factory = create_session_factory()
    with factory.begin() as session, pytest.raises(ValueError):
        provision_hosted_demo_accounts(session, password)


def test_hosted_provisioning_creates_private_idempotent_personas():
    password = "private-hosted-demo-password"
    factory = create_session_factory()
    with factory.begin() as session:
        session.execute(delete(UserRecord))
        session.execute(delete(OrganizationRecord))
        created = provision_hosted_demo_accounts(session, password)

    assert {email for email, *_ in DEMO_USERS}.issubset(created)
    with factory.begin() as session:
        users = session.scalars(select(UserRecord)).all()
        assert len(users) == len(DEMO_USERS)
        assert all(verify_password(user.password_hash, password) for user in users)
        assert provision_hosted_demo_accounts(session, "a-different-private-password") == []
        assert all(verify_password(user.password_hash, password) for user in users)
        # This module shares the suite database. Restore the local fixture credentials so
        # the behavior under test cannot leak into later authentication tests.
        for user in users:
            user.password_hash = hash_password(DEMO_PASSWORD)
