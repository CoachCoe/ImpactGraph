"""Retention enforced without anyone asking.

A retention period nobody enforces is a promise rather than a policy. The subject who
never comes back, and the organisation that forgets, still get what the schedule said.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from impactgraph.evidence import EvidenceUnrecoverable, FileEvidenceStorage
from impactgraph.main import session_factory
from impactgraph.persistence import AuditLogRecord, DataProtectionRecord, EvidenceRecord
from impactgraph.retention import RetentionWorker
from tests.conftest import TEST_ENCRYPTION_KEY

EVIDENCE = "ev-inv-8291"


@pytest.fixture
def worker():
    from impactgraph.config import Settings

    assert session_factory is not None
    return RetentionWorker(
        session_factory=session_factory,
        storage=FileEvidenceStorage(
            Settings.from_env().evidence_storage_path, TEST_ENCRYPTION_KEY
        ),
    )


def schedule(evidence_id: str, retain_until: str | None) -> None:
    assert session_factory is not None
    with session_factory.begin() as session:
        session.add(
            DataProtectionRecord(
                evidence_ref=evidence_id,
                lawful_basis="LEGITIMATE_INTEREST",
                special_category=False,
                controller_org_ref="org-global-water",
                subject_reference="subject-household-14",
                purpose="Showing a funder what their money delivered.",
                captured_by="org-global-water",
                retain_until=retain_until,
            )
        )
        session.scalar(
            select(EvidenceRecord).where(EvidenceRecord.external_id == evidence_id)
        ).personal_data = True


def test_an_object_past_its_schedule_is_erased_without_anyone_asking(worker):
    yesterday = (datetime.now(UTC).date() - timedelta(days=1)).isoformat()
    schedule(EVIDENCE, yesterday)

    result = worker.run_once()
    assert result.erased == 1 and result.failed == 0

    assert session_factory is not None
    with session_factory() as session:
        record = session.scalar(
            select(DataProtectionRecord).where(DataProtectionRecord.evidence_ref == EVIDENCE)
        )
        assert record.erased_at is not None
        evidence = session.scalar(
            select(EvidenceRecord).where(EvidenceRecord.external_id == EVIDENCE)
        )
        assert evidence.integrity_status == "UNRECOVERABLE"
        with pytest.raises(EvidenceUnrecoverable):
            worker.storage.retrieve(evidence.storage_uri)


def test_an_object_still_within_its_schedule_is_left_alone(worker):
    tomorrow = (datetime.now(UTC).date() + timedelta(days=1)).isoformat()
    schedule(EVIDENCE, tomorrow)
    assert worker.run_once().erased == 0


def test_a_record_with_no_schedule_is_never_erased_by_this(worker):
    """An absent retention period is not a retention period of zero. Reading it that way
    would erase everything the first time this ran."""
    schedule(EVIDENCE, None)
    assert worker.run_once().erased == 0


def test_erasing_twice_does_not_happen(worker):
    yesterday = (datetime.now(UTC).date() - timedelta(days=1)).isoformat()
    schedule(EVIDENCE, yesterday)
    assert worker.run_once().erased == 1
    # Already erased, so no longer due -- and not counted as a failure either.
    second = worker.run_once()
    assert second.erased == 0 and second.failed == 0


def test_the_erasure_is_recorded_without_quoting_what_was_erased(worker):
    yesterday = (datetime.now(UTC).date() - timedelta(days=1)).isoformat()
    schedule(EVIDENCE, yesterday)
    worker.run_once()

    assert session_factory is not None
    with session_factory() as session:
        entry = session.scalar(
            select(AuditLogRecord)
            .where(AuditLogRecord.action == "EVIDENCE_ERASED")
            .order_by(AuditLogRecord.created_at.desc())
        )
        assert entry is not None and entry.actor_id == "retention"
        recorded = str(entry.metadata_json)
        assert "Retention schedule reached" in recorded
        assert "subject-household-14" not in recorded


def test_one_unerasable_object_does_not_stop_the_rest(worker, monkeypatch):
    """A retention deadline that passed is not undone by a later failure, so each object
    is erased in its own transaction."""
    yesterday = (datetime.now(UTC).date() - timedelta(days=1)).isoformat()
    schedule(EVIDENCE, yesterday)

    calls = {"n": 0}
    original = worker.storage.destroy_key

    def flaky(uri: str) -> bool:
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("the volume went away")
        return original(uri)

    monkeypatch.setattr(worker.storage, "destroy_key", flaky)
    first = worker.run_once()
    assert (first.erased, first.failed) == (0, 1)

    # The record is untouched by the failure, so the next run still finds it due.
    second = worker.run_once()
    assert (second.erased, second.failed) == (1, 0)


def test_the_cutoff_is_inclusive_of_the_day_it_names(worker):
    """"Retain until 2026-09-23" means the object is not kept beyond that day."""
    today = datetime.now(UTC).date()
    schedule(EVIDENCE, today.isoformat())
    assert worker.run_once(today=date(today.year, today.month, today.day)).erased == 1
