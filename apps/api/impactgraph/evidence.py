from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .domain import ReconciliationStatus, Result
from .hashing import sha256_bytes


class EvidenceUnrecoverable(LookupError):
    """The object is still there and can no longer be read.

    Distinct from a missing file on purpose: "this was erased" and "this was never here"
    are different answers, and a data subject who asked for erasure is owed the first.
    """


class EvidenceStorage(Protocol):
    def uri_for(self, evidence_id: str) -> str: ...
    def store(self, evidence_id: str, content: bytes) -> str: ...
    def overwrite(self, storage_uri: str, content: bytes) -> None: ...
    def retrieve(self, storage_uri: str) -> bytes: ...
    def exists(self, storage_uri: str) -> bool: ...
    def destroy_key(self, storage_uri: str) -> bool: ...


class FileEvidenceStorage:
    """Evidence at rest, encrypted under a key that can be destroyed.

    Each object gets its own random data key, which encrypts the content and is itself
    stored wrapped under the configured key-encryption key. Erasure destroys the wrapped
    data key: the ciphertext may remain, and no longer means anything.

    That is what makes ADR-011 workable. A commitment on an immutable ledger cannot be
    withdrawn, so the erasable thing has to be the content rather than the record that
    something was committed. Deleting the file instead would leave the same commitment
    pointing at nothing, and could not be told apart from an object that never existed.
    """

    def __init__(self, root: Path, key: bytes) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        if len(key) != 32:
            raise ValueError("The evidence encryption key must be 32 bytes")
        self._cipher = AESGCM(key)

    def uri_for(self, evidence_id: str) -> str:
        return f"file://{self.root / f'{evidence_id}.bin'}"

    def _paths(self, storage_uri: str) -> tuple[Path, Path]:
        target = Path(storage_uri.removeprefix("file://")).resolve()
        if self.root not in target.parents:
            raise ValueError("Evidence path is outside configured storage")
        return target, target.with_suffix(".key")

    def _seal(self, target: Path, key_path: Path, content: bytes) -> None:
        data_key = AESGCM.generate_key(bit_length=256)
        nonce = secrets.token_bytes(12)
        target.write_bytes(nonce + AESGCM(data_key).encrypt(nonce, content, None))
        wrap_nonce = secrets.token_bytes(12)
        key_path.write_bytes(wrap_nonce + self._cipher.encrypt(wrap_nonce, data_key, None))
        key_path.chmod(0o600)

    def store(self, evidence_id: str, content: bytes) -> str:
        if not evidence_id.replace("-", "").replace("_", "").isalnum():
            raise ValueError("Unsafe evidence identifier")
        target = self.root / f"{evidence_id}.bin"
        key_path = target.with_suffix(".key")
        if target.exists():
            # Ciphertext differs for identical bytes, so immutability is checked against
            # what was stored rather than against the encrypted form.
            if not key_path.exists() or self.retrieve(f"file://{target}") != content:
                raise FileExistsError("Evidence is immutable; create a new evidence identifier")
            return f"file://{target}"
        self._seal(target, key_path, content)
        return f"file://{target}"

    def retrieve(self, storage_uri: str) -> bytes:
        target, key_path = self._paths(storage_uri)
        if not key_path.exists():
            raise EvidenceUnrecoverable(
                "The key for this evidence has been destroyed; its contents are unrecoverable"
            )
        wrapped = key_path.read_bytes()
        data_key = self._cipher.decrypt(wrapped[:12], wrapped[12:], None)
        sealed = target.read_bytes()
        return AESGCM(data_key).decrypt(sealed[:12], sealed[12:], None)

    def exists(self, storage_uri: str) -> bool:
        target, _ = self._paths(storage_uri)
        return target.exists()

    def destroy_key(self, storage_uri: str) -> bool:
        """Make the object unrecoverable. Returns whether a key was there to destroy."""
        _, key_path = self._paths(storage_uri)
        if not key_path.exists():
            return False
        key_path.unlink()
        return True

    def overwrite(self, storage_uri: str, content: bytes) -> None:
        """Replace stored bytes, bypassing the immutability guard in `store`.

        Only two callers are legitimate: seeding, which re-establishes the deterministic
        showcase object, and the tampering demo, which has to produce a real byte-level
        divergence from the registered commitment. A flag the integrity check is separately
        told about would prove nothing. Operator uploads must always go through `store`.
        """
        target, key_path = self._paths(storage_uri)
        self._seal(target, key_path, content)


@dataclass(frozen=True)
class AnalysisResult:
    extraction: dict[str, Any]
    provider: str
    model: str
    processed_at: str
    raw_response: dict[str, Any]


class EvidenceAnalysisProvider(Protocol):
    def analyze(self, content: bytes, mime_type: str) -> AnalysisResult: ...


MOCK_INVOICE_EXTRACTION: dict[str, Any] = {
    "documentType": "invoice",
    "invoiceNumber": "INV-8291",
    "vendor": "Aqua Systems Ltd.",
    "amountMinor": 420000,
    "currency": "USD",
    "date": "2026-08-17",
    "equipment": "AquaPure X200",
    "quantity": 2,
    "projectReference": "Water Project #12",
    # Per field, because that is what an operator confirms. A single number for a whole
    # document says nothing about which part of it to look at.
    "selfReportedConfidence": {
        "documentType": 0.97,
        "invoiceNumber": 0.97,
        "vendor": 0.97,
        "amountMinor": 0.97,
        "currency": 0.97,
        "date": 0.97,
        "equipment": 0.97,
        "quantity": 0.97,
        "projectReference": 0.97,
    },
}


class MockEvidenceAnalysisProvider:
    def analyze(self, content: bytes, mime_type: str) -> AnalysisResult:
        text = content.decode("utf-8", errors="ignore")
        if "INV-8291" not in text:
            raise ValueError("Mock provider only recognizes deterministic demo evidence")
        extraction = dict(MOCK_INVOICE_EXTRACTION)
        return AnalysisResult(
            extraction,
            "mock",
            "impactgraph-invoice-v1",
            datetime.now(UTC).isoformat(),
            {"fixture": "INV-8291", "schemaValid": True},
        )


class ReconciliationService:
    def reconcile_invoice(
        self,
        extraction: dict[str, Any],
        financial_transaction: dict[str, Any] | None,
        delivery: dict[str, Any] | None,
        completion_report_present: bool,
        photos_have_gps: list[bool],
    ) -> dict[str, Any]:
        """Compare an extracted document against the payment and delivery it claims.

        The counterparts are resolved from the ledger by the caller and may be absent.
        A missing counterpart is UNMATCHED -- there is nothing to reconcile against --
        which is a different answer from CONFLICT, where a counterpart exists and
        disagrees.
        """
        if financial_transaction is None:
            return {
                "status": ReconciliationStatus.UNMATCHED,
                "checks": [
                    {
                        "check": "TRANSACTION_REFERENCE",
                        "result": Result.FAIL,
                        "message": (
                            "No observed payment matches this document; nothing to "
                            "reconcile against"
                        ),
                    }
                ],
                "reasons": ["No observed payment matches this document"],
            }

        checks = [
            self._check(
                "VENDOR",
                extraction.get("vendor") == financial_transaction.get("payee"),
                f"Vendor matches the payee of {financial_transaction['id']}",
                f"Vendor conflicts with the payee of {financial_transaction['id']}",
            ),
            self._check(
                "AMOUNT",
                extraction.get("amountMinor") == financial_transaction.get("amountMinor"),
                f"Amount matches financial transaction {financial_transaction['id']}",
                f"Amount conflicts with financial transaction {financial_transaction['id']}",
            ),
            self._check(
                "CURRENCY",
                extraction.get("currency") == financial_transaction.get("currency"),
                "Currency matches",
                "Currency conflicts",
            ),
            self._check(
                "TRANSACTION_REFERENCE",
                bool(financial_transaction.get("memo"))
                and str(extraction.get("invoiceNumber", "")) in str(financial_transaction["memo"]),
                f"Payment {financial_transaction['id']} references this document",
                f"Payment {financial_transaction['id']} does not reference this document",
            ),
        ]

        if delivery is None:
            checks.append(
                {
                    "check": "DELIVERY",
                    "result": Result.FAIL,
                    "message": "No delivery is recorded against this payment",
                }
            )
        else:
            checks.extend(
                [
                    self._check(
                        "DELIVERY",
                        delivery.get("financialTransactionId") == financial_transaction["id"],
                        f"Delivery {delivery['id']} is supported by this payment",
                        f"Delivery {delivery['id']} is not linked to this payment",
                    ),
                    self._check(
                        "EQUIPMENT",
                        extraction.get("equipment") == delivery.get("item")
                        and extraction.get("quantity") == delivery.get("quantity"),
                        "Equipment matches delivery",
                        "Equipment conflicts with delivery",
                    ),
                    self._check(
                        "PROJECT",
                        delivery.get("projectId") is not None,
                        "Project reference matches",
                        "Project reference conflicts",
                    ),
                ]
            )

        checks.append(
            self._check(
                "COMPLETION_REPORT",
                completion_report_present,
                "Completion report supports installation",
                "Completion report is missing",
            )
        )
        if not all(photos_have_gps):
            checks.append(
                {
                    "check": "PHOTO_GPS",
                    "result": Result.WARNING,
                    "message": "One installation photograph has no GPS metadata, so it cannot corroborate "
                    "the location on its own",
                }
            )
        failures = sum(item["result"] == Result.FAIL for item in checks)
        warnings = sum(item["result"] == Result.WARNING for item in checks)
        status = (
            ReconciliationStatus.CONFLICT
            if failures
            else (ReconciliationStatus.PARTIAL_MATCH if warnings else ReconciliationStatus.MATCHED)
        )
        return {"status": status, "checks": checks, "reasons": [item["message"] for item in checks]}

    @staticmethod
    def _check(name: str, passes: bool, success: str, failure: str) -> dict[str, Any]:
        return {
            "check": name,
            "result": Result.PASS if passes else Result.FAIL,
            "message": success if passes else failure,
        }


def verify_integrity(content: bytes, registered_hash: str) -> tuple[bool, str]:
    current = sha256_bytes(content)
    return current == registered_hash, current
