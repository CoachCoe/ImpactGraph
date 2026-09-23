"""Bringing a second organisation onto the platform.

Organisations and users existed only in the seed, so onboarding one meant a code change
and a database session. These cover who may do it, what the organisation's kind decides,
and the chain grant without which a newly onboarded verifier can sign nothing.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from impactgraph.auth import DEMO_PASSWORD
from impactgraph.main import app, session_factory
from impactgraph.persistence import UserRecord

ADMIN = "admin@impactgraph.example"
OPERATOR = "operator@globalwater.example"
PASSWORD = "a-long-enough-password"


def sign_in(client: TestClient, email: str) -> None:
    assert client.post(
        "/auth/login", json={"email": email, "password": DEMO_PASSWORD}
    ).status_code == 200


def onboard(client: TestClient, body: dict, key: str):
    return client.post("/organizations", headers={"Idempotency-Key": key}, json=body)


def operator_body(**overrides) -> dict:
    return {
        "id": "org-riverbank",
        "name": "Riverbank Trust",
        "kind": "OPERATOR",
        "userEmail": "lead@riverbank.example",
        "userName": "Riverbank Lead",
        "userPassword": PASSWORD,
        **overrides,
    }


def test_an_onboarded_operator_can_sign_in_and_run_their_own_program():
    """The whole point: a second organisation without a code change or a CLI operator."""
    admin = TestClient(app)
    sign_in(admin, ADMIN)
    created = onboard(admin, operator_body(), "onboard-riverbank")
    assert created.status_code == 201, created.text
    assert created.json()["firstUser"] == {"email": "lead@riverbank.example", "role": "OPERATOR"}
    assert created.json()["chainRoleRequired"] is False

    theirs = TestClient(app)
    assert theirs.post(
        "/auth/login", json={"email": "lead@riverbank.example", "password": PASSWORD}
    ).status_code == 200
    program = theirs.post(
        "/programs",
        headers={"Idempotency-Key": "riverbank-program"},
        json={"id": "program-riverbank-1", "name": "Riverbank One", "region": "Devon"},
    )
    assert program.status_code == 201, program.text
    assert program.json()["operatorOrgRef"] == "org-riverbank"


def test_only_an_administrator_may_onboard_an_organisation():
    """There is no public signup, and who may act as an operator is not left open."""
    operator = TestClient(app)
    sign_in(operator, OPERATOR)
    assert onboard(operator, operator_body(id="org-sneaky"), "sneaky").status_code == 403
    assert onboard(TestClient(app), operator_body(id="org-anon"), "anon").status_code == 401


def test_the_organisations_kind_decides_the_role_and_a_client_cannot_name_it():
    """A client that could name the role could invite itself an administrator."""
    admin = TestClient(app)
    sign_in(admin, ADMIN)
    refused = onboard(admin, operator_body(id="org-x", role="ADMIN"), "named-role")
    assert refused.status_code == 422

    assert onboard(admin, operator_body(id="org-y", kind="ADMIN"), "admin-kind").status_code == 422


def test_an_email_already_in_use_is_refused_rather_than_reassigned():
    admin = TestClient(app)
    sign_in(admin, ADMIN)
    clash = onboard(admin, operator_body(id="org-clash", userEmail=OPERATOR), "clash")
    assert clash.status_code == 409
    assert "already exists" in clash.json()["detail"]


def test_a_verifier_wallet_is_not_granted_a_role_before_it_has_been_proven():
    """The address comes from the user record, where it arrives only after the verifier has
    signed a server-issued nonce. Granting an asserted address would defeat that."""
    admin = TestClient(app)
    sign_in(admin, ADMIN)
    created = onboard(
        admin,
        {
            "id": "org-northaudit",
            "name": "North Audit",
            "kind": "VERIFIER",
            "userEmail": "check@northaudit.example",
            "userName": "North Auditor",
            "userPassword": PASSWORD,
        },
        "onboard-northaudit",
    )
    assert created.status_code == 201
    # The response says so rather than leaving the administrator to discover it.
    assert created.json()["chainRoleRequired"] is True

    refused = admin.post(
        "/organizations/verifier-role",
        headers={"Idempotency-Key": "grant-unproven"},
        json={"userEmail": "check@northaudit.example"},
    )
    assert refused.status_code == 409
    assert "proven a wallet" in refused.json()["detail"]


def test_a_proven_verifier_wallet_is_queued_for_the_grant_it_needs_to_attest():
    admin = TestClient(app)
    sign_in(admin, ADMIN)
    onboard(
        admin,
        {
            "id": "org-southaudit",
            "name": "South Audit",
            "kind": "VERIFIER",
            "userEmail": "check@southaudit.example",
            "userName": "South Auditor",
            "userPassword": PASSWORD,
        },
        "onboard-southaudit",
    )
    wallet = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"
    assert session_factory is not None
    with session_factory.begin() as session:
        user = session.scalar(
            select(UserRecord).where(UserRecord.email == "check@southaudit.example")
        )
        user.wallet_address = wallet

    granted = admin.post(
        "/organizations/verifier-role",
        headers={"Idempotency-Key": "grant-proven"},
        json={"userEmail": "check@southaudit.example"},
    )
    assert granted.status_code == 202, granted.text
    assert granted.json()["wallet"] == wallet


def test_an_operators_wallet_is_never_granted_the_verifier_role():
    """The registry refuses a wallet holding both, and the separation is the trust model."""
    admin = TestClient(app)
    sign_in(admin, ADMIN)
    refused = admin.post(
        "/organizations/verifier-role",
        headers={"Idempotency-Key": "grant-operator"},
        json={"userEmail": OPERATOR},
    )
    assert refused.status_code == 409
    assert "verifier" in refused.json()["detail"].lower()
