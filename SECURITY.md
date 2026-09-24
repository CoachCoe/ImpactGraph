# Security policy

## This is a proof of concept

ImpactGraph is a demonstration of a verifiable-impact architecture. It has **not** been
audited, and it is not suitable for production use, for real donor funds, or for custody
of anything of value. The smart contract has not been reviewed by a third party. Treat
every deployment as a testnet deployment.

Do not deploy this to Ethereum mainnet.

## Reporting a vulnerability

Please do not open a public issue.

Report privately through
[GitHub Security Advisories](https://github.com/CoachCoe/ImpactGraph/security/advisories/new),
which lets us discuss and fix the issue before it is disclosed.

Please include what an attacker gains, the steps to reproduce, and the commit you tested.
A proof of concept helps but is not required. You will get an acknowledgement within a
week.

## What is in scope

The trust boundary is the part of this system that is meant to be checkable without
trusting the operator:

- The `ImpactRegistry` contract and its role separation
- Evidence hashing, storage, and the integrity check against published commitments
- The chain observer's receipt, event, emitting-address, bundle-hash and
  confirmation-depth validation
- Authentication, session handling, and the wallet ownership proof
- Authorization and evidence visibility enforcement
- The transactional outbox and its idempotency and replay protection

A report showing that the system can be made to display a verified result that is not
actually backed by a valid onchain attestation is the most valuable kind, because that is
the one claim the product makes.

## What is out of scope

These are known and documented, not vulnerabilities:

- The financial activity, NGO records, outcomes and demo evidence are fictional by design.
  The offline extractor is deterministic; a configured hosted demo may run real Inkling
  extraction against those fictional documents. Model output is never verification.
- Demo-only endpoints under `/demo/*`, including the tampering fixture. They are refused
  when the stack runs against a public network.
- Anvil's default accounts and their well-known private keys, which appear in the demo
  compose file. They are published by Foundry and protect nothing.
- Authentication is intentionally minimal: email and password with server-side sessions,
  no SSO, MFA, or password reset. See [docs/security.md](docs/security.md) and ADR-005.
- Denial of service through resource exhaustion against a local demo stack.

## Operational notes

The backend holds no private key when running against a public network, and
`EVM_SENDER_ADDRESS` must be empty there — verifier attestations are signed by the
browser wallet. `scripts/deploy.sh` refuses to start a public-network stack otherwise.

Never commit a filled `.env`, a private key, a seed phrase, or an authenticated RPC URL.
