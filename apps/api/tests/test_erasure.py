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
