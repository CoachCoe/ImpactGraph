# POC security and privacy

## Authentication

Operators, verifiers and administrators sign in with an email and password. Passwords are
hashed with Argon2id. A session is an opaque random token; only its SHA-256 is stored, so a
database disclosure does not hand over usable sessions, and sessions are revocable. The
token travels in an httpOnly, SameSite=Lax cookie, marked Secure outside development. The
web app proxies the API under its own origin so the cookie is first-party rather than
requiring SameSite=None.

Login answers identically for an unknown account and a wrong password, and hashes in both
cases, so it cannot be used to enumerate accounts.

Reads are deliberately public: programs, claims, provenance, verification status, public
evidence and integrity verification all work without an account, because public
verifiability is the point of the product. Mutations require a session, and the role comes
from the account rather than from the request.

A wallet address is bound to an account only after that account signs a single-use
server-issued nonce (EIP-191); the address is recovered from the signature rather than
supplied by the caller. A verifier cannot request verification until this binding exists.
Separation of duties — the operating organisation may not verify its own claim — is decided
against the program's operator recorded in the database.

The seeded demo accounts share a published password and exist only for a local database, in
the same spirit as Anvil's published keys. Never create them against a real deployment.

Not included: SSO, MFA, password reset, account lockout, rate limiting, CSRF tokens
(mitigated by SameSite), and any password policy beyond what the operator chooses.

## Evidence and transport

Evidence is offchain and carries PUBLIC, RESTRICTED or INTERNAL visibility, and visibility
is enforced: PUBLIC evidence is readable without an account, RESTRICTED requires a session,
and INTERNAL is limited to the operating organisation and administrators. Operator mutations
are scoped to projects owned by their organisation; ADMIN is the documented POC override.
Public Ethereum contains opaque identifiers and hashes only. Original bytes are hashed before
processing. The registered commitment and historical provenance are immutable; the local
filesystem object is deliberately mutable so the tamper demonstration can prove a mismatch.
Integrity verification reads that object back and compares it with the independently indexed or
queried registered commitment rather than trusting a mutable evidence-row hash or stored status.

The API requires idempotency keys for important operations. Upload adapters enforce the configured MIME allowlist and size cap before storage; the cap is `MAX_UPLOAD_BYTES`, checked by reading one byte past the limit. The allowlist trusts the client-declared `Content-Type` and does no content sniffing.

## Demo affordances that fabricate chain state

The PostgreSQL seed also writes an OPERATOR attestation with a placeholder wallet and transaction hash, marked CONFIRMED. It is served by the claim API exactly as a real attestation would be and satisfies the `OPERATOR_ATTESTATION` requirement. Only the verifier attestation path is real end to end.

`POST /blockchain/operations/{id}/confirm-evidence-demo` and `.../confirm-demo` mark an
operation CONFIRMED, and the latter transitions a claim to VERIFIED, without any receipt,
event or confirmation depth. They exist only for the in-memory demo store and now **refuse
whenever a database is configured**, so they are unreachable in any real deployment. They
are listed here rather than left for a reader to discover.

Secrets belong in environment variables. Never commit mnemonics, funded keys, RPC tokens,
AI keys, or production database credentials. Anvil keys are publicly known development
keys and must never hold value. Sepolia deployment is explicit; browser code never receives
a backend signing key.

This POC does not include identity proofing, malware scanning, hardened object storage IAM, encryption key management, rate
limiting, CSRF/session protection, KYC/AML, HSM signing, high-availability workers, or a
security audit. Add these before processing
real users or sensitive evidence.
