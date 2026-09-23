from fastapi.testclient import TestClient
from sqlalchemy import select

from impactgraph.config import Settings
from impactgraph.evidence import FileEvidenceStorage
from impactgraph.hashing import sha256_bytes
from impactgraph.main import app, session_factory
from impactgraph.persistence import EvidenceRecord, ProvenanceEdgeRecord

OPERATOR = "operator@globalwater.example"
VERIFIER = "verifier@impactverify.example"
ADMIN = "admin@impactgraph.example"
CLAIM_ID = "claim-water-12-200"
EVIDENCE_ID = "ev-inv-8291"


def client() -> TestClient:
    return TestClient(app)


# --- reads are public -------------------------------------------------------------

def test_transparency_reads_need_no_account():
    """Public verifiability is the product; a donor must not need to sign in."""
    anonymous = client()
    assert anonymous.get(f"/claims/{CLAIM_ID}").json()["status"] == "VERIFICATION_PENDING"
    assert anonymous.get("/programs").status_code == 200
    assert anonymous.get(f"/claims/{CLAIM_ID}/provenance").status_code == 200
    assert anonymous.get(f"/claims/{CLAIM_ID}/verification").status_code == 200
    assert anonymous.get(f"/evidence/{EVIDENCE_ID}").status_code == 200


# --- authentication ---------------------------------------------------------------

def test_login_returns_identity_and_sets_an_httponly_cookie(sign_in):
    session = client()
    payload = sign_in(session, OPERATOR)
    assert payload["role"] == "OPERATOR"
    assert payload["organization"]["id"] == "org-global-water"
    cookie = session.cookies.jar._cookies
    assert session.get("/auth/me").json()["email"] == OPERATOR
    assert cookie, "a session cookie should have been set"


def test_login_rejects_a_wrong_password_and_an_unknown_account():
    session = client()
    wrong = session.post("/auth/login", json={"email": OPERATOR, "password": "nope"})
    unknown = session.post("/auth/login", json={"email": "nobody@x.example", "password": "nope"})
    assert wrong.status_code == 401
    assert unknown.status_code == 401
    # The same message for both: otherwise an anonymous caller can enumerate accounts.
    assert wrong.json()["detail"]["message"] == unknown.json()["detail"]["message"]


def test_logout_revokes_the_session(sign_in):
    session = client()
    sign_in(session, OPERATOR)
    assert session.get("/auth/me").status_code == 200
    session.post("/auth/logout")
    assert session.get("/auth/me").status_code == 401


def test_mutations_reject_anonymous_callers_and_the_wrong_role(sign_in):
    anonymous = client()
    assert anonymous.post(f"/evidence/{EVIDENCE_ID}/analyze").status_code == 401
    assert anonymous.post(f"/demo/evidence/{EVIDENCE_ID}/tamper").status_code == 401

    verifier = client()
    sign_in(verifier, VERIFIER)
    # A real identity, but not one allowed to manage evidence.
    assert verifier.post(f"/evidence/{EVIDENCE_ID}/analyze").status_code == 403
    assert verifier.post(f"/demo/evidence/{EVIDENCE_ID}/tamper").status_code == 403


def test_a_forged_role_header_no_longer_grants_anything():
    """The previous model trusted X-Demo-Role; it must now be inert."""
    forged = client()
    response = forged.post(
        f"/demo/evidence/{EVIDENCE_ID}/tamper",
        headers={"X-Demo-Role": "ADMIN", "X-Actor-Id": "org-impactgraph"},
    )
    assert response.status_code == 401


def test_a_verifier_cannot_request_verification_before_proving_its_wallet(sign_in):
    verifier = client()
    sign_in(verifier, VERIFIER)
    response = verifier.post(
        f"/verification-requests/{CLAIM_ID}/intent",
        headers={"Idempotency-Key": "intent-without-wallet"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "WALLET_NOT_VERIFIED"


def test_verifier_can_reject_with_an_audited_idempotent_reason(sign_in):
    verifier = client()
    sign_in(verifier, VERIFIER)
    headers = {"Idempotency-Key": "reject-showcase-claim"}
    payload = {"reason": "Delivery evidence does not establish the stated outcome."}
    first = verifier.post(
        f"/verification-requests/{CLAIM_ID}/reject", headers=headers, json=payload
    )
    replay = verifier.post(
        f"/verification-requests/{CLAIM_ID}/reject", headers=headers, json=payload
    )
    assert first.status_code == 200
    assert replay.json() == first.json()
    assert client().get(f"/claims/{CLAIM_ID}").json()["status"] == "REJECTED"


def test_wallet_binding_requires_a_signature_over_the_issued_nonce(sign_in):
    from eth_account import Account
    from eth_account.messages import encode_defunct

    verifier = client()
    sign_in(verifier, VERIFIER)
    challenge = verifier.post("/auth/wallet/challenge").json()

    account = Account.from_key("0x" + "11" * 32)
    signature = Account.sign_message(
        encode_defunct(text=challenge["message"]), private_key=account.key
    ).signature.hex()

    # A signature over a different nonce must not bind the wallet.
    forged = verifier.post(
        "/auth/wallet/verify",
        json={"nonce": "0" * 32, "signature": "0x" + signature.removeprefix("0x")},
    )
    assert forged.status_code == 400

    bound = verifier.post(
        "/auth/wallet/verify",
        json={"nonce": challenge["nonce"], "signature": "0x" + signature.removeprefix("0x")},
    )
    assert bound.status_code == 200
    # The address is recovered from the signature, never supplied by the caller.
    assert bound.json()["walletAddress"].lower() == account.address.lower()
    assert verifier.get("/auth/me").json()["walletAddress"].lower() == account.address.lower()

    # A nonce is single use.
    replay = verifier.post(
        "/auth/wallet/verify",
        json={"nonce": challenge["nonce"], "signature": "0x" + signature.removeprefix("0x")},
    )
    assert replay.status_code == 400


# --- evidence visibility ----------------------------------------------------------

def test_restricted_evidence_is_not_readable_without_a_session(sign_in):
    operator = client()
    sign_in(operator, OPERATOR)
    evidence_id = "ev-restricted-probe"
    operator.post(
        "/evidence",
        headers={"Idempotency-Key": "restricted-upload"},
        data={
            "evidence_id": evidence_id,
            "project_id": "project-water-12",
            "evidence_type": "INVOICE",
            "visibility": "RESTRICTED",
        },
        files={"file": ("INV-8291.txt", b"Invoice INV-8291", "text/plain")},
    )
    assert client().get(f"/evidence/{evidence_id}").status_code == 401
    assert operator.get(f"/evidence/{evidence_id}").status_code == 200


def test_upload_rejects_an_unknown_visibility_before_storing(sign_in):
    operator = client()
    sign_in(operator, OPERATOR)
    response = operator.post(
        "/evidence",
        headers={"Idempotency-Key": "bad-visibility"},
        data={
            "evidence_id": "ev-bad-visibility",
            "project_id": "project-water-12",
            "evidence_type": "INVOICE",
            "visibility": "EVERYONE",
        },
        files={"file": ("invoice.txt", b"invoice", "text/plain")},
    )
    assert response.status_code == 422
    assert client().get("/evidence/ev-bad-visibility").status_code == 404


# --- integrity and tampering ------------------------------------------------------

def test_tamper_demo_alters_stored_bytes_and_integrity_detects_it(sign_in):
    admin = client()
    sign_in(admin, ADMIN)
    storage = FileEvidenceStorage(Settings.from_env().evidence_storage_path)
    uri = storage.uri_for(EVIDENCE_ID)
    before = storage.retrieve(uri)

    assert client().post(f"/evidence/{EVIDENCE_ID}/verify-integrity").json()["status"] == "MATCH"
    assert client().post(f"/demo/evidence/{EVIDENCE_ID}/tamper").status_code == 401
    result = admin.post(f"/demo/evidence/{EVIDENCE_ID}/tamper").json()

    # The demo must alter the stored object itself, not a flag the check is told about.
    after = storage.retrieve(uri)
    assert after != before
    assert result["status"] == "MISMATCH"
    assert result["current"] == sha256_bytes(after)
    assert result["expected"] != result["current"]

    # Re-running the check without re-tampering still reports the divergence, and the
    # registered commitment is unchanged -- history cannot be rewritten to match.
    recheck = client().post(f"/evidence/{EVIDENCE_ID}/verify-integrity").json()
    assert recheck["status"] == "MISMATCH"
    assert recheck["expected"] == result["expected"]

    assert admin.post("/demo/reset").status_code == 200
    assert client().post(f"/evidence/{EVIDENCE_ID}/verify-integrity").json()["status"] == "MATCH"


def test_integrity_does_not_trust_a_mutated_evidence_row_hash():
    assert session_factory is not None
    with session_factory.begin() as session:
        record = session.scalar(
            select(EvidenceRecord).where(EvidenceRecord.external_id == EVIDENCE_ID)
        )
        assert record is not None
        record.content_hash = "sha256:" + "0" * 64
    result = client().post(f"/evidence/{EVIDENCE_ID}/verify-integrity").json()
    assert result["status"] == "MATCH"
    assert result["expected"] != "sha256:" + "0" * 64


# --- operator vertical slice ------------------------------------------------------

def test_operator_evidence_slice_is_submitted_but_not_registered(sign_in):
    operator = client()
    sign_in(operator, OPERATOR)
    evidence_id = "ev-upload-flow"
    upload = {
        "data": {
            "evidence_id": evidence_id,
            "project_id": "project-water-12",
            "evidence_type": "INVOICE",
            "visibility": "RESTRICTED",
        },
        "files": {"file": ("INV-8291.txt", b"Invoice INV-8291", "text/plain")},
    }
    uploaded = operator.post(
        "/evidence", headers={"Idempotency-Key": "upload-flow-key"}, **upload
    )
    assert uploaded.status_code == 201
    assert uploaded.json()["workflowStatus"] == "UPLOADED"

    replay = operator.post(
        "/evidence", headers={"Idempotency-Key": "upload-flow-key"}, **upload
    )
    assert replay.json() == uploaded.json()

    analyzed = operator.post(f"/evidence/{evidence_id}/analyze")
    assert analyzed.json()["workflowStatus"] == "ANALYZED"
    reviewed = operator.post(
        f"/evidence/{evidence_id}/review",
        json={
            "extraction": analyzed.json()["extraction"],
            "confirmed": analyzed.json()["reviewRequired"],
        },
    )
    assert reviewed.json()["workflowStatus"] == "REVIEWED"

    submitted = operator.post(
        f"/evidence/{evidence_id}/register", headers={"Idempotency-Key": "register-flow-key"}
    )
    assert submitted.json()["blockchainStatus"] in {"CREATED", "SUBMITTED"}
    # Submitted is not registered. Only an observed receipt, the expected event and the
    # configured depth may advance it -- see tests/test_worker.py.
    assert (
        operator.get(f"/evidence/{evidence_id}").json()["workflowStatus"]
        == "REGISTRATION_PENDING"
    )


def test_operator_correction_is_validated_and_reconciled_again(sign_in):
    operator = client()
    sign_in(operator, OPERATOR)
    evidence_id = "ev-corrected-flow"
    operator.post(
        "/evidence",
        headers={"Idempotency-Key": "corrected-upload"},
        data={
            "evidence_id": evidence_id,
            "project_id": "project-water-12",
            "evidence_type": "INVOICE",
            "visibility": "RESTRICTED",
        },
        files={"file": ("INV-8291.txt", b"Invoice INV-8291", "text/plain")},
    )
    analyzed = operator.post(f"/evidence/{evidence_id}/analyze").json()
    extraction, confirmed = analyzed["extraction"], analyzed["reviewRequired"]
    extraction["amountMinor"] = 1
    reviewed = operator.post(
        f"/evidence/{evidence_id}/review",
        json={"extraction": extraction, "confirmed": confirmed},
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["reconciliation"]["status"] == "CONFLICT"

    extraction["amountMinor"] = -1
    rejected = operator.post(
        f"/evidence/{evidence_id}/review",
        json={"extraction": extraction, "confirmed": confirmed},
    )
    assert rejected.status_code == 422


def test_verification_requires_a_connected_path_to_this_claim():
    assert session_factory is not None
    with session_factory.begin() as session:
        outcome_edge = session.scalar(
            select(ProvenanceEdgeRecord).where(
                ProvenanceEdgeRecord.source_type == "OUTCOME",
                ProvenanceEdgeRecord.target_id == CLAIM_ID,
            )
        )
        assert outcome_edge is not None
        outcome_edge.target_id = "claim-unrelated"
    requirements = {
        item["requirement"]: item["status"]
        for item in client().get(f"/claims/{CLAIM_ID}/verification").json()["requirements"]
    }
    assert requirements["PROVENANCE_COMPLETE"] == "FAIL"


def test_the_routes_that_fabricate_chain_state_refuse_against_a_database(sign_in):
    admin = client()
    sign_in(admin, ADMIN)
    response = admin.post("/blockchain/operations/whatever/confirm-evidence-demo")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "DEMO_ROUTE_DISABLED"


# --- financial data API -----------------------------------------------------------

PROGRAM_ID = "program-clean-water-kenya-2026"


def test_the_money_trail_is_public():
    """"Where did the money go" is one of the questions the product exists to answer."""
    summary = client().get(f"/financial/programs/{PROGRAM_ID}")
    assert summary.status_code == 200
    body = summary.json()
    assert body["received"]["currency"] == "USD"
    # Integer minor units, never a float.
    assert isinstance(body["received"]["amountMinor"], int)
    assert body["committed"]["amountMinor"] + body["uncommitted"]["amountMinor"] == (
        body["received"]["amountMinor"]
    )
    assert any(t["id"] == "ftx-9182" for t in body["transactions"])
    assert client().get("/financial/transactions/ftx-9182").json()["payee"] == "Aqua Systems Ltd."
    assert body["received"]["amountMinor"] == 10_000_000
    assert body["spent"]["amountMinor"] == 8_742_000
    project = client().get("/projects/project-water-12").json()
    assert project["spent"]["amountMinor"] == 420_000


def test_unevidenced_spend_is_visible_rather_than_hidden():
    body = client().get(f"/financial/programs/{PROGRAM_ID}").json()
    statuses = {t["id"]: t["matchStatus"] for t in body["transactions"]}
    # The haulage payment has no invoice; the ledger says so instead of omitting it.
    assert statuses["ftx-9183"] == "UNMATCHED"
    assert body["matchCounts"]["UNMATCHED"] >= 1


def test_importing_a_statement_requires_an_operator_and_an_idempotency_key(sign_in):
    assert client().post(f"/financial/programs/{PROGRAM_ID}/import").status_code == 401

    verifier = client()
    sign_in(verifier, VERIFIER)
    assert verifier.post(f"/financial/programs/{PROGRAM_ID}/import").status_code == 403

    operator = client()
    sign_in(operator, OPERATOR)
    assert operator.post(f"/financial/programs/{PROGRAM_ID}/import").status_code == 400


def test_reimporting_a_statement_records_no_new_payments(sign_in):
    operator = client()
    sign_in(operator, OPERATOR)
    before = client().get(f"/financial/programs/{PROGRAM_ID}").json()["spent"]["amountMinor"]
    result = operator.post(
        f"/financial/programs/{PROGRAM_ID}/import", headers={"Idempotency-Key": "import-1"}
    )
    assert result.status_code == 200
    assert result.json()["imported"] == []
    assert len(result.json()["skipped"]) == 3
    after = client().get(f"/financial/programs/{PROGRAM_ID}").json()["spent"]["amountMinor"]
    assert after == before


def test_operation_status_reports_the_claim_it_actually_attests(sign_in):
    """The showcase claim id was hardcoded here.

    Polling any verifier operation therefore returned the showcase claim's status, so a
    second claim's attestation would have reported the wrong verification state -- the
    exact signal the verifier UI gates on.
    """
    from uuid import uuid4

    from sqlalchemy import select

    from impactgraph.main import session_factory
    from impactgraph.persistence import (
        AttestationRecord,
        BlockchainOperationRecord,
        ClaimRecord,
    )

    other_claim = "claim-other-programme"
    attestation_id = "att-other-programme"
    operation_id = uuid4()

    assert session_factory is not None
    with session_factory.begin() as session:
        showcase = session.scalar(
            select(ClaimRecord).where(ClaimRecord.external_id == CLAIM_ID)
        )
        assert showcase.status == "VERIFICATION_PENDING"
        session.add(
            ClaimRecord(
                external_id=other_claim,
                program_ref="program-other",
                project_ref="project-other",
                statement="A different claim entirely.",
                payload_hash="sha256:" + "c" * 64,
                # Deliberately different from the showcase claim's status.
                status="REJECTED",
                verification_policy_version="1.0",
                verification_bundle_hash="sha256:" + "d" * 64,
            )
        )
        session.add(
            AttestationRecord(
                external_id=attestation_id,
                attestation_type="INDEPENDENT_VERIFIER",
                subject_type="CLAIM",
                subject_id=other_claim,
                issuer_id="org-impactverify",
                issuer_wallet="0x" + "2" * 40,
                statement_hash="sha256:" + "e" * 64,
                verification_bundle_hash="sha256:" + "d" * 64,
                status="SUBMITTED",
            )
        )
        session.add(
            BlockchainOperationRecord(
                id=operation_id,
                chain_id=31337,
                entity_id=attestation_id,
                operation_type="CREATE_VERIFIER_ATTESTATION",
                expected_event="AttestationCreated",
                status="SUBMITTED",
                transaction_hash="0x" + "f" * 64,
                confirmations=0,
                correlation_id="corr-other",
            )
        )

    body = client().get(f"/blockchain/operations/{operation_id}").json()
    assert body["claimStatus"] == "REJECTED", "must report its own claim, not the showcase one"


def test_provenance_is_scoped_to_its_claim_and_reports_real_integrity(sign_in):
    """The graph must contain this claim's lineage and nothing else.

    The previous version of this test only planted an unrelated *evidence* edge, which
    was the one case the earlier fix happened to cover -- it was written to the
    implementation rather than to the invariant. It now plants unrelated funding, an
    unrelated outcome and the edge between them, which the earlier fix let through
    because the financial nodes were selected unscoped.
    """

    from impactgraph.main import session_factory
    from impactgraph.persistence import (
        ClaimRecord,
        FundingRecord,
        OutcomeRecord,
        ProvenanceEdgeRecord,
    )

    baseline = client().get(f"/claims/{CLAIM_ID}/provenance").json()
    assert baseline["edges"], "the showcase claim should have provenance"
    assert client().get(f"/claims/{CLAIM_ID}").json()["evidenceIds"] == ["ev-inv-8291"]

    assert session_factory is not None
    with session_factory.begin() as session:
        session.add(
            ClaimRecord(
                external_id="claim-unrelated",
                program_ref="program-other",
                project_ref="project-other",
                statement="An unrelated claim.",
                payload_hash="sha256:" + "9" * 64,
                status="DRAFT",
                verification_policy_version="1.0",
                verification_bundle_hash="sha256:" + "8" * 64,
            )
        )
        session.add(
            FundingRecord(
                external_id="funding-unrelated",
                program_ref="program-other",
                funder_name="Unrelated Donor",
                amount_minor=500000,
                currency="USD",
                received_on="2026-01-01",
                source_ref="other-1",
            )
        )
        session.add(
            OutcomeRecord(
                external_id="outcome-unrelated",
                program_ref="program-other",
                delivery_ref=None,
                metric="Other",
                value=9,
                unit="things",
                region="Elsewhere",
            )
        )
        for source, source_type, relationship, target, target_type in [
            ("ev-unrelated", "EVIDENCE", "SUPPORTS", "claim-unrelated", "CLAIM"),
            ("funding-unrelated", "FUNDING", "FUNDS", "outcome-unrelated", "OUTCOME"),
        ]:
            session.add(
                ProvenanceEdgeRecord(
                    source_id=source,
                    source_type=source_type,
                    relationship=relationship,
                    target_id=target,
                    target_type=target_type,
                    confirmed_onchain=True,
                )
            )

    after = client().get(f"/claims/{CLAIM_ID}/provenance").json()
    assert after["edges"] == baseline["edges"], "another claim's edges must not leak in"
    node_ids = {node["id"] for node in after["nodes"]}
    assert not node_ids & {"ev-unrelated", "funding-unrelated", "outcome-unrelated"}
    # Its own evidence, not the showcase's. The identifier itself is redacted for anything
    # that is not PUBLIC, and an id with no evidence record behind it fails closed.
    unrelated = client().get("/claims/claim-unrelated").json()["evidenceIds"]
    showcase = client().get(f"/claims/{CLAIM_ID}").json()["evidenceIds"]
    assert len(unrelated) == 1
    assert not set(unrelated) & set(showcase)

    # The seed no longer asserts a passed integrity check, so earn it: the graph must
    # report what the check actually found, before and after it has been run.
    before = next(node for node in after["nodes"] if node["type"] == "EVIDENCE")
    assert before["detail"] == "Integrity not yet checked"
    assert client().post("/evidence/ev-inv-8291/verify-integrity").json()["status"] == "MATCH"
    checked = client().get(f"/claims/{CLAIM_ID}/provenance").json()
    evidence_node = next(node for node in checked["nodes"] if node["type"] == "EVIDENCE")
    assert evidence_node["detail"] == "Integrity confirmed"

    # Once the bytes diverge, the graph must say so rather than repeat the label.
    admin = client()
    sign_in(admin, ADMIN)
    admin.post("/demo/evidence/ev-inv-8291/tamper")
    tampered = client().get(f"/claims/{CLAIM_ID}/provenance").json()
    node = next(n for n in tampered["nodes"] if n["type"] == "EVIDENCE")
    assert node["detail"] == "Integrity check failed"
    admin.post("/demo/reset")


def test_the_graph_follows_provenance_edges_rather_than_a_fixed_payment():
    """ftx-9182 was selected by id, so the graph showed one payment whatever the edges said."""
    from impactgraph.main import session_factory
    from impactgraph.persistence import ProvenanceEdgeRecord

    def payments():
        graph = client().get(f"/claims/{CLAIM_ID}/provenance").json()
        return sorted(n["id"] for n in graph["nodes"] if n["type"] == "FINANCIAL_TRANSACTION")

    # ftx-9183 and ftx-9184 are real unevidenced spend with no link to this claim, so
    # they belong in the money trail and not in this lineage.
    assert payments() == ["ftx-9182"]

    assert session_factory is not None
    with session_factory.begin() as session:
        session.add(
            ProvenanceEdgeRecord(
                source_id="ftx-9184",
                source_type="FINANCIAL_TRANSACTION",
                relationship="SUPPORTS",
                target_id="delivery-water-12",
                target_type="DELIVERY",
                confirmed_onchain=True,
            )
        )
    assert payments() == ["ftx-9182", "ftx-9184"]


def test_evidence_cannot_be_registered_on_a_model_nobody_checked(sign_in):
    """The operator screen blocks this, and the screen is not where a guarantee lives.

    Two posts with an operator session were enough to put a misread invoice number on
    chain: /review took whatever extraction it was handed and moved straight to REVIEWED.
    """
    operator = client()
    sign_in(operator, OPERATOR)
    evidence_id = "ev-unconfirmed-flow"
    operator.post(
        "/evidence",
        headers={"Idempotency-Key": "unconfirmed-upload"},
        data={
            "evidence_id": evidence_id,
            "project_id": "project-water-12",
            "evidence_type": "INVOICE",
            "visibility": "RESTRICTED",
        },
        files={"file": ("INV-8291.txt", b"Invoice INV-8291", "text/plain")},
    )
    analyzed = operator.post(f"/evidence/{evidence_id}/analyze").json()
    flagged = analyzed["reviewRequired"]
    assert {"invoiceNumber", "amountMinor", "currency"} <= set(flagged)

    refused = operator.post(
        f"/evidence/{evidence_id}/review", json={"extraction": analyzed["extraction"]}
    )
    assert refused.status_code == 422
    assert refused.json()["detail"]["code"] == "CONFIRMATION_REQUIRED"
    assert set(refused.json()["detail"]["fields"]) == set(flagged)

    # Confirming all but one is still not a review.
    partial = operator.post(
        f"/evidence/{evidence_id}/review",
        json={"extraction": analyzed["extraction"], "confirmed": flagged[1:]},
    )
    assert partial.status_code == 422
    assert partial.json()["detail"]["fields"] == [flagged[0]]

    # Nothing moved: the evidence is still waiting on a person.
    assert operator.get(f"/evidence/{evidence_id}").json()["workflowStatus"] == "ANALYZED"

    accepted = operator.post(
        f"/evidence/{evidence_id}/review",
        json={"extraction": analyzed["extraction"], "confirmed": flagged},
    )
    assert accepted.status_code == 200
    assert accepted.json()["workflowStatus"] == "REVIEWED"
