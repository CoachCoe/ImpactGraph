"""Erasing what is past its retention schedule, whether or not anyone asked.

A retention period nobody enforces is a promise, not a policy. This is the part of the
data protection story that has to run unattended: a subject who never comes back, and an
organisation that forgets, still get the outcome the schedule said they would.

Separate from the chain worker on purpose. It claims nothing from the outbox, its failures
must not hold up a registration, and a registration failure must not delay an erasure that
is already overdue.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .evidence import EvidenceStorage
from .observability import correlation_context, logger
from .persistence import AuditLogRecord, DataProtectionRecord, EvidenceRecord
from .services import strip_derivatives
from .verification import restate_claims_for

log = logger("impactgraph.retention")


@dataclass(frozen=True)
class RetentionResult:
    erased: int = 0
    failed: int = 0


class RetentionWorker:
    """Destroys the key of every object whose retention period has passed."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        storage: EvidenceStorage,
    ) -> None:
        self.session_factory = session_factory
        self.storage = storage

    def due(self, session: Session, today: date) -> list[str]:
        """Records past their schedule and not yet erased.

        Compared as ISO strings because the column stores one, which sorts and compares
        the same way a date does for this format and nothing else.
        """
        return list(
            session.scalars(
                select(DataProtectionRecord.evidence_ref).where(
                    # Stated although `NULL <= date` is already never true: an absent
                    # retention period is not a period of zero, and reading it as one
                    # would erase everything the first time this ran.
                    DataProtectionRecord.retain_until.is_not(None),
                    DataProtectionRecord.retain_until <= today.isoformat(),
                    DataProtectionRecord.erased_at.is_(None),
                )
            )
        )

    def run_once(self, today: date | None = None) -> RetentionResult:
        today = today or datetime.now(UTC).date()
        with self.session_factory() as session:
            due = self.due(session, today)
        erased = failed = 0
        for evidence_id in due:
            with correlation_context(f"retention-{evidence_id}"):
                if self._erase_one(evidence_id):
                    erased += 1
                else:
                    failed += 1
        return RetentionResult(erased=erased, failed=failed)

    def _erase_one(self, evidence_id: str) -> bool:
        """One object, in its own transaction.

        Per object rather than per batch: one unreadable file must not roll back the
        erasures that already succeeded, because a retention deadline that passed is not
        undone by a later failure.
        """
        try:
            with self.session_factory() as session, session.begin():
                # Locked, and skipped where another worker already holds it. Two workers
                # otherwise both pass the erased_at check and both write an audit entry
                # claiming to be the erasure. Ignored by SQLite, which has no concurrent
                # writers for this to matter to.
                record = session.scalar(
                    select(DataProtectionRecord)
                    .where(DataProtectionRecord.evidence_ref == evidence_id)
                    .with_for_update(skip_locked=True)
                )
                evidence = session.scalar(
                    select(EvidenceRecord).where(EvidenceRecord.external_id == evidence_id)
                )
                if record is None or evidence is None or record.erased_at is not None:
                    return False
                destroyed = self.storage.destroy_key(evidence.storage_uri)
                cleared = strip_derivatives(evidence)
                now = datetime.now(UTC)
                record.erased_at = now
                evidence.integrity_status = "UNRECOVERABLE"
                restated = restate_claims_for(session, evidence_id, f"retention-{evidence_id}")
                session.add(
                    AuditLogRecord(
                        actor_id="retention",
                        action="EVIDENCE_ERASED",
                        entity_type="EVIDENCE",
                        entity_id=evidence_id,
                        # Never the subject or the contents. An erasure entry that quotes
                        # what was erased is not an erasure.
                        metadata_json={
                            "reason": "Retention schedule reached",
                            "retainUntil": record.retain_until,
                            "keyDestroyed": destroyed,
                            "derivativesCleared": cleared,
                            "claimsRestated": restated,
                        },
                        correlation_id=f"retention-{evidence_id}",
                    )
                )
        except Exception as exc:  # noqa: BLE001 -- one failure must not stop the rest
            # The class, never the text: a storage path can name the object it holds.
            log.error(
                "retention.erase_failed",
                evidence_id=evidence_id,
                error_type=exc.__class__.__name__,
            )
            return False
        log.info("retention.erased", evidence_id=evidence_id, key_destroyed=destroyed)
        return True
