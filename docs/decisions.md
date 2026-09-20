# Architectural decisions

## ADR-001 — One immutable registry

Decision: use one non-upgradeable `ImpactRegistry` with compact entity commitments/events.

Why: one inspectable contract is sufficient for the POC's provenance authority.

Alternatives considered: per-entity contracts; proxy upgradeability.

Tradeoff: future schemas require a versioned registry migration.

Date: 2026-09-18

## ADR-004 — Local PostgreSQL host port 55432

Decision: expose the Docker development database on host port 55432 while PostgreSQL keeps
its standard container port 5432.

Why: the development host already runs a separate PostgreSQL instance on loopback port
5432, and another Docker project already owns 5433. ImpactGraph therefore uses a clearly
isolated high port.

Alternatives considered: stop or reconfigure the user's existing PostgreSQL instance.

Tradeoff: local URLs use a non-default port, but both databases remain isolated and intact.

Date: 2026-09-18

## ADR-002 — Transactional PostgreSQL outbox

Decision: persist blockchain intent and an outbox job with the domain mutation.

Why: PostgreSQL and Ethereum cannot share an atomic transaction.

Alternatives considered: submit directly inside HTTP handlers.

Tradeoff: operations are eventually consistent and require a worker/reconciler.

Date: 2026-09-18

## ADR-003 — Domain hashing uses framed UTF-8 fields

Decision: use versioned, length-prefixed binary encodings rather than JSON serialization.

Why: the same bytes can be reproduced across Python, TypeScript, and Solidity tooling.

Status: only the Python codec exists. `packages/shared` was never created, so the
cross-language agreement this ADR is written for is not yet demonstrated. The published
test vectors in `docs/hashing.md` are asserted in the Python suite and are what a second
implementation should be checked against.

Alternatives considered: canonical JSON; ABI encoding all offchain structures.

Tradeoff: small custom codecs must be tested in every implementation language, which is a
cost not yet paid.

Date: 2026-09-18

## ADR-005 — Build authentication rather than buy it

Decision: implement session authentication in the application — organisations, users and
sessions in PostgreSQL, Argon2id password hashing, opaque session tokens stored only as
their SHA-256 and delivered in an httpOnly cookie.

Why: a hosted identity provider would break two properties the project depends on. CI must
run without external credentials, and the demo must run entirely on a laptop; both would
fail if signing in required an account with a third party. It would also put a signup step
in front of anyone evaluating the repository. The scope here is small and well understood:
three tables, password verification, and session lookup.

Alternatives considered: Clerk, Auth0 or WorkOS, which would be the right answer the moment
real users, SSO, MFA or account recovery are in scope — none of which this POC has; and
stateless JWTs, rejected because a system whose claim is auditability should be able to
revoke a session, and a database is already present.

Tradeoff: no SSO, no MFA, no password reset, and the password policy is whatever the
operator chooses. The seeded demo accounts share a published password and are intended
only for a local database, in the same spirit as Anvil's published keys.

Date: 2026-09-18

## ADR-006 — Wallet control is proven, not asserted

Decision: a wallet address is bound to an account only after the account signs a
server-issued single-use nonce (EIP-191). The address is recovered from the signature.

Why: the address was previously supplied in an `X-Wallet-Address` header and believed. The
separation-of-duties rule and the identity shown beside an attestation both rest on it, so
an unproven address made them decorative.

Alternatives considered: Sign-In With Ethereum (EIP-4361), which is the better answer if
wallet-first login is ever wanted; it was not adopted because application identity and
blockchain identity are deliberately distinct here, and the wallet proves control of an
address rather than authenticating a person.

Tradeoff: the verifier performs one extra signature before their first attestation.

Date: 2026-09-18

## ADR-007 — Observe financial activity, never initiate it

Decision: financial transactions enter the system only through a read-only
`FinancialDataProvider`, imported idempotently by the provider's own reference. Money is
stored as integer minor units plus an ISO currency. An allocation is a ceiling, and an
import that would breach it is rejected.

Why: the product's claim is that a payment can be traced to an outcome, which requires the
payment record to be something ImpactGraph observed rather than something it asserted. A
system that both initiates and attests to a payment is its own witness. Idempotency by
provider reference matters because statement feeds are replayed routinely; the cost of a
duplicate is a double-counted payment in a transparency report.

Alternatives considered: initiating payments through a processor, rejected as both out of
scope and in tension with the trust model; and a single decimal column for money, rejected
because binary floating point cannot represent most decimal amounts exactly and money that
is a fraction of a cent out is money that does not reconcile.

Tradeoff: the demo cannot show a payment being made, only one being observed. Multi-currency
programs would need a recorded conversion rate, which `Money` deliberately refuses to
invent.

Date: 2026-09-18
