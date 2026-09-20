# Canonical hashing

ImpactGraph never hashes arbitrary JSON. All long-lived encodings are versioned. Hashes
use SHA-256 and are displayed as `sha256:` followed by 64 lowercase hexadecimal digits.
Solidity stores the raw 32-byte digest.

Application entity identifiers are mapped to opaque Solidity `bytes32` identifiers as
`SHA-256(exact UTF-8 application identifier)`. This avoids exposing names or requiring
UUID-specific contract parsing while keeping retries stable across languages.

## Evidence

Evidence is `SHA-256(exact original uploaded bytes)`. It is computed before OCR, previews,
normalization, metadata changes, or AI processing. Derived artifacts have independent
hashes and never replace the original commitment.

## Framed domain encoding v1

Claims, financial transactions, attestations, and bundles use this binary sequence:

```text
frame("impactgraph-hash-v1") || frame(kind) ||
frame(field_name_1) || frame(field_value_1) || ...
```

`frame(s)` is a 4-byte unsigned big-endian byte length followed by exact UTF-8 bytes.
Fields occur in the documented order. Numbers use base-10 ASCII with no leading zeros;
dates use ISO 8601; identifiers and enum values are case-sensitive.

Claims: `schema_version, claim_id, statement, outcome_id`.

Financial transactions: `schema_version, transaction_id, amount_minor, currency,
payer_ref, payee_ref, occurred_on, source_ref`.

Attestations: `schema_version, attestation_id, attestation_type, subject_type, subject_id,
issuer_ref, statement_hash, verification_bundle_hash`.

Verification bundles: `bundle_version, claim_id, claim_payload_hash, outcome_id,
policy_version`, then zero or more `evidence_hash` fields sorted lexicographically, then
zero or more `provenance_ref` fields sorted lexicographically. Version is `1.0`.

## Test vectors

```text
evidence bytes: "Invoice INV-8291\n"
sha256:76874e018df604591aab602a926e0f70f1c93e699afa32eb277e13e8ffbb358c

claim("claim-water-12-200", "200 households gained access to clean drinking water.",
      "outcome-water-12-200", "1.0")
sha256:22a97a7d2f721d60c7572d652858e8f6d734da22041da0630c06594c8c0f9bff

financial_transaction("TX-9182", 420000, "USD", "org-gwi", "vendor-aqua",
                      "2026-08-17", "mock-bank-9182", "1.0")
sha256:821156a29c50ddbd64fb917773eb5862145bd76e8d5e44e4c696e080689bc5e6

bundle(claim_id="claim-water-12-200",
       claim_payload_hash=<the claim vector above>,
       evidence=[sha256:<64 x "a">, sha256:<64 x "b">],
       outcome_id="outcome-water-12-200",
       provenance=["edge-2", "edge-1"],
       policy_version="1.0")
sha256:aab4f7360a3fb2c50bf646d9ada99b1b06365f51fdcc83fc7c63f5ad5e67ad0b
```

Every vector above is asserted in `apps/api/tests/test_domain.py`
(`test_documented_hash_vectors_in_docs_hashing_md`), so the document and the implementation
cannot drift apart silently.

This encoding is currently implemented in Python only. `docs/decisions.md` ADR-003 describes
reproducing the same bytes in TypeScript and Solidity tooling; that has not been built, and
there is no `packages/shared` holding shared schemas or vectors. A second implementation
should be checked against the vectors above before any commitment spans languages.
