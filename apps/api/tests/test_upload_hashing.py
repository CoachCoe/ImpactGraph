"""The uploaded bytes must be what gets hashed, and the hash must be what gets committed.

Nothing asserted this. Three upload tests read `workflowStatus` and discarded
`contentHash`; the service tests inject a synthetic hash; the only real hash assertions
run against the seeded record, whose hash is produced by the seed rather than by the
upload route. So the route could hash the filename, a truncated read, or the stream
before `.read()`, and every commitment registered from then on would commit to nothing --
with a green suite. This is the product's central claim.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from impactgraph.auth import DEMO_PASSWORD
from impactgraph.config import Settings
from impactgraph.evidence import FileEvidenceStorage
from impactgraph.hashing import sha256_bytes
from impactgraph.main import app, session_factory
from impactgraph.persistence import EvidenceRecord

OPERATOR = "operator@globalwater.example"
PROJECT_ID = "project-water-12"


def upload(session: TestClient, evidence_id: str, payload: bytes) -> dict:
    response = session.post(
        "/evidence",
        files={"file": (f"{evidence_id}.txt", payload, "text/plain")},
        data={
            "evidence_id": evidence_id,
            "project_id": PROJECT_ID,
            "evidence_type": "INVOICE",
            "visibility": "PUBLIC",
        },
        headers={"Idempotency-Key": f"upload-{evidence_id}"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def signed_in() -> TestClient:
    session = TestClient(app)
    session.post("/auth/login", json={"email": OPERATOR, "password": DEMO_PASSWORD})
    return session


def test_the_recorded_hash_is_the_hash_of_the_bytes_that_were_sent():
    payload = b"Invoice INV-9001\nVendor: Aqua Systems Ltd.\nUSD 1,234.56\n"
    body = upload(signed_in(), "ev-hash-check", payload)
    assert body["contentHash"] == sha256_bytes(payload)


def test_the_stored_object_is_byte_identical_to_what_was_sent():
    """A hash over the right bytes is worthless if different bytes were stored."""
    payload = b"Invoice INV-9002\nTrailing whitespace and a tab:\t \nUSD 7.00\n"
    upload(signed_in(), "ev-storage-check", payload)
    assert session_factory is not None
    with session_factory() as session:
        record = session.scalar(
            select(EvidenceRecord).where(EvidenceRecord.external_id == "ev-storage-check")
        )
    settings = Settings.from_env()
    storage = FileEvidenceStorage(
        settings.evidence_storage_path, settings.evidence_encryption_key
    )
    assert storage.retrieve(record.storage_uri) == payload
    assert record.content_hash == sha256_bytes(payload)
    # And the bytes on disk are not those bytes: evidence is encrypted at rest, so the
    # object is readable only through a key that can be destroyed.
    on_disk = Path(record.storage_uri.removeprefix("file://")).read_bytes()
    assert payload not in on_disk


def test_a_one_byte_difference_produces_a_different_commitment():
    """Guards against a hash over something constant, like the id or the filename."""
    first = upload(signed_in(), "ev-sensitivity-a", b"Invoice INV-9003\nUSD 10.00\n")
    second = upload(signed_in(), "ev-sensitivity-b", b"Invoice INV-9003\nUSD 10.01\n")
    assert first["contentHash"] != second["contentHash"]


def test_identical_bytes_under_different_ids_hash_identically():
    """And guards against the opposite: a hash that mixes the identifier into the digest."""
    payload = b"Invoice INV-9004\nUSD 42.00\n"
    first = upload(signed_in(), "ev-same-bytes-a", payload)
    assert first["contentHash"] == sha256_bytes(payload)
