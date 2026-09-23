"""Erasing evidence whose commitment cannot be erased.

ADR-011: a commitment on an immutable ledger cannot be withdrawn, so the erasable thing
has to be the content rather than the record that something was committed. Each object is
encrypted under its own data key; destroying that key leaves the ciphertext meaningless and
the commitment still true about what was once committed.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from impactgraph.evidence import EvidenceUnrecoverable, FileEvidenceStorage
from impactgraph.hashing import sha256_bytes

DOCUMENT = b"Household register: Amina Otieno, 4 people, Kisumu\n"


@pytest.fixture
def storage(tmp_path: Path) -> FileEvidenceStorage:
    return FileEvidenceStorage(tmp_path, os.urandom(32))


def test_what_is_written_to_disk_is_not_the_document(storage, tmp_path):
    uri = storage.store("ev-household-1", DOCUMENT)
    written = Path(uri.removeprefix("file://")).read_bytes()
    assert DOCUMENT not in written
    assert b"Amina" not in written
    assert storage.retrieve(uri) == DOCUMENT


def test_destroying_the_key_makes_the_document_unrecoverable(storage):
    uri = storage.store("ev-household-2", DOCUMENT)
    assert storage.retrieve(uri) == DOCUMENT

    assert storage.destroy_key(uri) is True
    with pytest.raises(EvidenceUnrecoverable):
        storage.retrieve(uri)
    # Destroying it twice is not an error, and says nothing was there to destroy.
    assert storage.destroy_key(uri) is False


def test_erasure_is_a_different_answer_from_never_having_existed(storage):
    """A data subject who asked for erasure is owed the first. An object that was never
    here is a 404; one that was erased is a record we can still say something true about."""
    uri = storage.store("ev-household-3", DOCUMENT)
    storage.destroy_key(uri)
    assert storage.exists(uri) is True
    with pytest.raises(EvidenceUnrecoverable):
        storage.retrieve(uri)


def test_the_ciphertext_is_worthless_without_the_configured_key(tmp_path):
    """The wrapped data key sits beside the ciphertext, so the pair has to be useless to
    anyone who takes the disk without the key-encryption key."""
    original = FileEvidenceStorage(tmp_path, os.urandom(32))
    uri = original.store("ev-household-4", DOCUMENT)

    thief = FileEvidenceStorage(tmp_path, os.urandom(32))
    with pytest.raises(Exception) as refused:
        thief.retrieve(uri)
    assert not isinstance(refused.value, EvidenceUnrecoverable), (
        "a wrong key must fail as a decryption failure, not as an erasure"
    )


def test_a_key_that_is_not_thirty_two_bytes_is_refused(tmp_path):
    with pytest.raises(ValueError, match="32 bytes"):
        FileEvidenceStorage(tmp_path, os.urandom(16))


def test_evidence_is_still_immutable_through_the_encryption(storage):
    """Ciphertext differs for identical bytes, so immutability has to be checked against
    what was stored rather than against the encrypted form."""
    uri = storage.store("ev-household-5", DOCUMENT)
    assert storage.store("ev-household-5", DOCUMENT) == uri

    with pytest.raises(FileExistsError):
        storage.store("ev-household-5", b"Household register: someone else\n")


def test_the_commitment_still_describes_the_document_and_not_its_ciphertext(storage):
    """The hash registered on chain is over the publishable bytes. Hashing the ciphertext
    would make integrity unverifiable by anyone holding the original document."""
    uri = storage.store("ev-household-6", DOCUMENT)
    assert sha256_bytes(storage.retrieve(uri)) == sha256_bytes(DOCUMENT)


# --- The lawful basis a registration is gated on, through the API ---

from fastapi.testclient import TestClient

from impactgraph.auth import DEMO_PASSWORD
from impactgraph.main import app, session_factory

OPERATOR = "operator@globalwater.example"
BASIS = {
    "lawfulBasis": "LEGITIMATE_INTEREST",
    "controllerOrgRef": "org-global-water",
    "subjectReference": "subject-household-14",
    "purpose": "Showing a funder what their money delivered.",
}


def operator_client() -> TestClient:
    client = TestClient(app)
    assert client.post(
        "/auth/login", json={"email": OPERATOR, "password": DEMO_PASSWORD}
    ).status_code == 200
    return client


def upload(client: TestClient, evidence_id: str, *, personal: bool) -> None:
    # Distinct bytes per object: identical content is the same evidence, and the schema
    # says so with a unique constraint on the commitment.
    response = client.post(
        "/evidence",
        headers={"Idempotency-Key": f"upload-{evidence_id}"},
        data={
            "evidence_id": evidence_id,
            "project_id": "project-water-12",
            "evidence_type": "INVOICE",
            "visibility": "RESTRICTED",
            "personal_data": str(personal).lower(),
        },
        files={"file": ("INV-8291.txt", f"Invoice INV-8291 for {evidence_id}".encode(), "text/plain")},
    )
    assert response.status_code == 201, response.text


def review(client: TestClient, evidence_id: str) -> None:
    analyzed = client.post(f"/evidence/{evidence_id}/analyze").json()
    assert client.post(
        f"/evidence/{evidence_id}/review",
        json={"extraction": analyzed["extraction"], "confirmed": analyzed["reviewRequired"]},
    ).status_code == 200


def test_personal_data_cannot_be_committed_without_a_lawful_basis():
    """After registration the hash cannot be withdrawn, so there is no later point at which
    "we had no basis for holding this" can be acted on cheaply."""
    client = operator_client()
    upload(client, "ev-personal-nobasis", personal=True)
    review(client, "ev-personal-nobasis")

    refused = client.post(
        "/evidence/ev-personal-nobasis/register", headers={"Idempotency-Key": "reg-nobasis"}
    )
    assert refused.status_code == 409
    assert "lawful basis" in refused.json()["detail"]

    declared = client.post("/evidence/ev-personal-nobasis/data-protection", json=BASIS)
    assert declared.status_code == 201, declared.text
    assert client.post(
        "/evidence/ev-personal-nobasis/register", headers={"Idempotency-Key": "reg-withbasis"}
    ).status_code == 202


def test_evidence_with_no_personal_data_in_it_is_not_asked_for_one():
    """An invoice from a supplier is not about a person, and demanding a basis for it would
    make the declaration meaningless everywhere it does matter."""
    client = operator_client()
    upload(client, "ev-impersonal", personal=False)
    review(client, "ev-impersonal")
    assert client.post(
        "/evidence/ev-impersonal/register", headers={"Idempotency-Key": "reg-impersonal"}
    ).status_code == 202


def test_special_category_data_cannot_rest_on_legitimate_interest():
    """Article 9 permits no Article 6 basis on its own. Explicit consent is the only
    condition this system accepts; anything else is not an engineering decision."""
    client = operator_client()
    upload(client, "ev-health", personal=True)
    refused = client.post(
        "/evidence/ev-health/data-protection", json={**BASIS, "specialCategory": True}
    )
    assert refused.status_code == 409
    assert "Article 9" in refused.json()["detail"]

    accepted = client.post(
        "/evidence/ev-health/data-protection",
        json={**BASIS, "specialCategory": True, "lawfulBasis": "CONSENT"},
    )
    assert accepted.status_code == 201, accepted.text


def test_erasing_evidence_makes_it_unrecoverable_and_restates_what_rested_on_it():
    """A claim keeps whatever badge it had unless something re-evaluates it, so erasing the
    evidence beneath one without restating it leaves a donor reading a verdict whose
    support no longer exists."""
    from sqlalchemy import select

    from impactgraph.persistence import DataProtectionRecord, EvidenceRecord

    client = operator_client()
    erased = client.post(
        "/evidence/ev-inv-8291/data-protection", json=BASIS
    )
    assert erased.status_code == 201, erased.text

    response = client.post(
        "/evidence/ev-inv-8291/erase", json={"reason": "The subject objected."}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["keyDestroyed"] is True
    # The commitment is not withdrawn, and the response says so rather than implying it.
    assert "remains" in body["commitment"]

    assert session_factory is not None
    with session_factory() as session:
        record = session.scalar(
            select(DataProtectionRecord).where(
                DataProtectionRecord.evidence_ref == "ev-inv-8291"
            )
        )
        assert record.erased_at is not None and record.withdrawn_at is not None
        evidence = session.scalar(
            select(EvidenceRecord).where(EvidenceRecord.external_id == "ev-inv-8291")
        )
        assert evidence.integrity_status == "UNRECOVERABLE"

    # And the bytes really are gone, not merely marked.
    integrity = client.post("/evidence/ev-inv-8291/verify-integrity")
    assert integrity.status_code == 410, integrity.text
    assert integrity.json()["detail"]["code"] == "EVIDENCE_ERASED"


def test_an_erasure_record_does_not_quote_what_was_erased():
    """An audit entry naming the subject or the contents is not an erasure."""
    from sqlalchemy import select

    from impactgraph.persistence import AuditLogRecord

    client = operator_client()
    client.post("/evidence/ev-inv-8291/data-protection", json=BASIS)
    client.post("/evidence/ev-inv-8291/erase", json={"reason": "Retention schedule reached."})

    assert session_factory is not None
    with session_factory() as session:
        entry = session.scalar(
            select(AuditLogRecord)
            .where(AuditLogRecord.action == "EVIDENCE_ERASED")
            .order_by(AuditLogRecord.created_at.desc())
        )
        assert entry is not None
        recorded = str(entry.metadata_json)
        assert "subject-household-14" not in recorded
        assert "INV-8291" not in recorded


# --- Answering a subject access request ---


def declare_for(client: TestClient, evidence_id: str, subject: str, **overrides) -> None:
    body = {**BASIS, "subjectReference": subject, **overrides}
    assert client.post(f"/evidence/{evidence_id}/data-protection", json=body).status_code == 201


def test_a_subject_access_request_is_one_query_not_a_tour_of_the_codebase():
    """A DSAR has a deadline and no allowance for a search that misses something."""
    client = operator_client()
    upload(client, "ev-subject-a", personal=True)
    upload(client, "ev-subject-b", personal=True)
    upload(client, "ev-someone-else", personal=True)
    declare_for(client, "ev-subject-a", "subject-amina")
    declare_for(client, "ev-subject-b", "subject-amina")
    declare_for(client, "ev-someone-else", "subject-other")

    answer = client.get("/data-subjects/subject-amina")
    assert answer.status_code == 200, answer.text
    body = answer.json()
    held = {entry["evidenceId"] for entry in body["held"]}
    assert held == {"ev-subject-a", "ev-subject-b"}
    assert set(body["erasable"]) == held
    # What is held, with the basis and the controller, because those are what a subject is
    # entitled to be told.
    assert all(entry["lawfulBasis"] == "LEGITIMATE_INTEREST" for entry in body["held"])
    assert all(entry["controller"] == "org-global-water" for entry in body["held"])
    # And the honest note about what objecting can and cannot undo.
    assert "cannot be withdrawn" in body["note"]


def test_the_subject_record_reports_what_is_held_and_not_its_contents():
    """Otherwise this is a way to read every restricted object by guessing a reference."""
    client = operator_client()
    upload(client, "ev-subject-contents", personal=True)
    declare_for(client, "ev-subject-contents", "subject-contents")

    body = client.get("/data-subjects/subject-contents").json()
    serialised = str(body)
    assert "INV-8291" not in serialised
    assert "extraction" not in serialised


def test_an_operator_sees_only_the_subjects_it_is_answerable_for():
    """Controllership decides this. An operator is not entitled to another organisation's
    subjects merely because it can name one."""
    from tests.test_ownership_scoping import make_outsider

    client = operator_client()
    upload(client, "ev-subject-scoped", personal=True)
    declare_for(client, "ev-subject-scoped", "subject-scoped")

    make_outsider()
    outsider = TestClient(app)
    assert outsider.post(
        "/auth/login",
        json={"email": "operator@otherwater.example", "password": DEMO_PASSWORD},
    ).status_code == 200
    assert outsider.get("/data-subjects/subject-scoped").json()["held"] == []

    admin = TestClient(app)
    admin.post("/auth/login", json={"email": "admin@impactgraph.example", "password": DEMO_PASSWORD})
    assert len(admin.get("/data-subjects/subject-scoped").json()["held"]) == 1


def test_an_erased_object_still_appears_with_what_became_of_it():
    """A subject is owed an account of what was held, including what has gone, or an
    erasure looks indistinguishable from never having been told about it."""
    client = operator_client()
    upload(client, "ev-subject-erased", personal=True)
    declare_for(client, "ev-subject-erased", "subject-erased")
    client.post("/evidence/ev-subject-erased/erase", json={"reason": "They objected."})

    entry = client.get("/data-subjects/subject-erased").json()["held"][0]
    assert entry["erasedAt"] is not None
    assert entry["objectedAt"] is not None
    assert entry["evidenceId"] not in client.get("/data-subjects/subject-erased").json()["erasable"]


def test_erasure_removes_what_the_model_read_out_of_the_document():
    """Destroying the storage key is not erasure on its own.

    The extraction is the document restated as fields -- for a household register that is
    the person's name, their household and where they live -- and it sat in a column,
    served over the API to anyone who could read the record, after the file was gone.
    """
    from sqlalchemy import select

    from impactgraph.persistence import EvidenceRecord

    client = operator_client()
    upload(client, "ev-derivatives", personal=True)
    analyzed = client.post("/evidence/ev-derivatives/analyze").json()
    assert analyzed["extraction"] is not None
    client.post(
        "/evidence/ev-derivatives/review",
        json={"extraction": analyzed["extraction"], "confirmed": analyzed["reviewRequired"]},
    )
    declare_for(client, "ev-derivatives", "subject-derivatives")

    erased = client.post("/evidence/ev-derivatives/erase", json={"reason": "They objected."})
    assert erased.status_code == 200, erased.text
    assert "extraction" in erased.json()["derivativesCleared"]

    served = client.get("/evidence/ev-derivatives").json()
    assert served["extraction"] is None
    assert served.get("reconciliation") is None
    assert served.get("providerMetadata") is None

    assert session_factory is not None
    with session_factory() as session:
        record = session.scalar(
            select(EvidenceRecord).where(EvidenceRecord.external_id == "ev-derivatives")
        )
        assert record.extraction is None
        assert "providerMetadata" not in (record.metadata_json or {})
        # The commitment stays: it cannot be withdrawn from the ledger anyway, and it
        # reveals nothing about the document it commits to.
        assert record.content_hash.startswith("sha256:")


def test_the_key_is_not_stored_beside_the_object_it_unlocks(tmp_path):
    """One backup containing both undoes every erasure the moment it is restored."""
    import os as _os

    store = FileEvidenceStorage(tmp_path / "objects", _os.urandom(32))
    uri = store.store("ev-separate", DOCUMENT)

    objects = {path.name for path in (tmp_path / "objects").iterdir()}
    assert objects == {"ev-separate.bin"}, "a key was written beside the ciphertext"
    assert store.key_root != store.root
    assert {path.name for path in store.key_root.iterdir()} == {"ev-separate.key"}
    assert store.retrieve(uri) == DOCUMENT


def test_an_object_cannot_be_made_to_decrypt_as_a_different_one(tmp_path):
    """The identifier is authenticated with the ciphertext, so moving files around cannot
    substitute one document for another."""
    import os as _os

    store = FileEvidenceStorage(tmp_path / "objects", _os.urandom(32))
    first = store.store("ev-first", b"the first document\n")
    store.store("ev-second", b"the second document\n")

    # Swap both halves of the pair, which without associated data would decrypt cleanly.
    (store.root / "ev-first.bin").write_bytes((store.root / "ev-second.bin").read_bytes())
    (store.key_root / "ev-first.key").write_bytes(
        (store.key_root / "ev-second.key").read_bytes()
    )
    with pytest.raises(Exception) as refused:
        store.retrieve(first)
    assert not isinstance(refused.value, EvidenceUnrecoverable)


def test_rotating_the_key_keeps_the_document_readable(tmp_path):
    """A swap without a re-seal makes every object unrecoverable in one step, so the
    procedure re-seals -- and the binding has to survive it."""
    import os as _os

    old_key, new_key = _os.urandom(32), _os.urandom(32)
    store = FileEvidenceStorage(tmp_path / "objects", old_key)
    uri = store.store("ev-rotate", DOCUMENT)

    assert store.rewrap(uri, new_key) is True
    rotated = FileEvidenceStorage(tmp_path / "objects", new_key)
    assert rotated.retrieve(uri) == DOCUMENT
    # And the old key no longer opens it, which is the point of rotating.
    with pytest.raises(Exception):
        FileEvidenceStorage(tmp_path / "objects", old_key).retrieve(uri)


def test_rotation_leaves_an_erased_object_erased(tmp_path):
    import os as _os

    old_key, new_key = _os.urandom(32), _os.urandom(32)
    store = FileEvidenceStorage(tmp_path / "objects", old_key)
    uri = store.store("ev-erased-rotate", DOCUMENT)
    store.destroy_key(uri)

    assert store.rewrap(uri, new_key) is False
    with pytest.raises(EvidenceUnrecoverable):
        FileEvidenceStorage(tmp_path / "objects", new_key).retrieve(uri)
