from __future__ import annotations

import hashlib
from collections.abc import Iterable

HASH_ENCODING_VERSION = "impactgraph-hash-v1"
VERIFICATION_BUNDLE_VERSION = "1.0"


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _frame(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return len(encoded).to_bytes(4, "big") + encoded


def hash_fields(kind: str, fields: Iterable[tuple[str, str]]) -> str:
    payload = _frame(HASH_ENCODING_VERSION) + _frame(kind)
    for name, value in fields:
        payload += _frame(name) + _frame(value)
    return sha256_bytes(payload)


def claim_hash(claim_id: str, statement: str, outcome_id: str, schema_version: str = "1.0") -> str:
    return hash_fields(
        "claim",
        (
            ("schema_version", schema_version),
            ("claim_id", claim_id),
            ("statement", statement),
            ("outcome_id", outcome_id),
        ),
    )


def financial_transaction_hash(
    transaction_id: str,
    amount_minor: int,
    currency: str,
    payer_ref: str,
    payee_ref: str,
    occurred_on: str,
    source_ref: str,
    schema_version: str = "1.0",
) -> str:
    return hash_fields(
        "financial_transaction",
        (
            ("schema_version", schema_version),
            ("transaction_id", transaction_id),
            ("amount_minor", str(amount_minor)),
            ("currency", currency),
            ("payer_ref", payer_ref),
            ("payee_ref", payee_ref),
            ("occurred_on", occurred_on),
            ("source_ref", source_ref),
        ),
    )


def verification_bundle_hash(
    claim_id: str,
    claim_payload_hash: str,
    evidence_hashes: Iterable[str],
    outcome_id: str,
    provenance_refs: Iterable[str],
    policy_version: str,
) -> str:
    fields: list[tuple[str, str]] = [
        ("bundle_version", VERIFICATION_BUNDLE_VERSION),
        ("claim_id", claim_id),
        ("claim_payload_hash", claim_payload_hash),
        ("outcome_id", outcome_id),
        ("policy_version", policy_version),
    ]
    fields.extend(("evidence_hash", value) for value in sorted(evidence_hashes))
    fields.extend(("provenance_ref", value) for value in sorted(provenance_refs))
    return hash_fields("verification_bundle", fields)


def attestation_hash(
    attestation_id: str,
    attestation_type: str,
    subject_type: str,
    subject_id: str,
    issuer_ref: str,
    statement_hash: str,
    bundle_hash: str,
    schema_version: str = "1.0",
) -> str:
    return hash_fields(
        "attestation",
        (
            ("schema_version", schema_version),
            ("attestation_id", attestation_id),
            ("attestation_type", attestation_type),
            ("subject_type", subject_type),
            ("subject_id", subject_id),
            ("issuer_ref", issuer_ref),
            ("statement_hash", statement_hash),
            ("verification_bundle_hash", bundle_hash),
        ),
    )
