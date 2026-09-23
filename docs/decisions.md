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

## ADR-008 — Liveness and readiness answer different questions

Decision: `/health/live` touches nothing and is what the container image probes.
`/health/ready` probes PostgreSQL, the RPC and the evidence store, and is what the compose
dependency gates, the smoke tests and any load balancer use.

Why: a dependency-aware probe on the container healthcheck lets a transient database or
RPC fault mark a working process for replacement, which fixes nothing and loses whatever
it was doing. A dependency-free probe on the compose gates lets the worker and the web
application start against an API that cannot reach its database.

Consequence that is easy to miss: five `depends_on: service_healthy` gates consume the
image healthcheck. Moving that to liveness without overriding the api service's healthcheck
in both compose files silently downgrades them from "can serve" to "process booted".

Alternatives considered: one endpoint for both, rejected because the two consumers want
opposite answers; and no probes at all, which is where this started.

Tradeoff: two endpoints and a compose override to keep in step with the image.

Date: 2026-09-22

## ADR-009 — One outbox, claimed by topic

Decision: chain intent and notification intent share `outbox`, and each worker selects
only the topics it can send.

Why: intent has to be written in the transaction that decided the thing, or a rollback
leaves a message about something that did not happen. That pattern already existed for the
chain, and a second table would have duplicated the durability, the locking and the
retry semantics to no benefit.

The failure this prevents: the chain worker originally claimed every unprocessed row
regardless of topic, treated an unknown one as a failed evidence registration, and looked
up an entity that does not exist. Any non-chain topic in that table would have been eaten
and marked failed.

Alternatives considered: a table per sender, rejected as duplicated machinery; and sending
inline at the point of decision, rejected because it cannot be rolled back.

Tradeoff: every future sender must scope its claim query, and the partial index on
unprocessed rows is shared by all of them.

Date: 2026-09-22

## ADR-010 — How many verifiers a claim needs is policy, not contract

Decision: `ImpactRegistry` records attestations and says nothing about how many are
enough. The N-of-M threshold lives on the program row and is evaluated off-chain by
`VerificationPolicyService`, which is versioned.

Why: ADR-001 chose a non-upgradeable registry. Encoding a threshold there would make every
change to what verification requires a contract migration, and the rule is exactly the sort
of thing that will change as the product learns. The chain's job is to make the
attestations undeniable; deciding what a set of them means is the application's.

Consequence: two requirements that were independent are now nested. Since only attestations
covering the current bundle are counted, every way of failing `BUNDLE_CURRENT` also starves
`INDEPENDENT_VERIFICATION`.

Alternatives considered: threshold enforcement in `createAttestation`, rejected as above;
and per-claim rather than per-program thresholds, deferred until something asks for it.

Tradeoff: a reader must trust the application for the count, having trusted the chain for
the signatures. The trust model already says which is which.

Date: 2026-09-22

## ADR-011 — Commitments are to publishable bytes, and originals are erasable

Decision: the content hash committed to the registry is taken over the bytes this system is
willing to publish — after redaction, after any client-side compression — and never over a
raw camera original. Where a raw original is retained at all, it is encrypted under a key
held per object, so destroying that key renders it unrecoverable while leaving the
commitment truthful about what was committed.

Why: UK GDPR gives a data subject a right to erasure, and ADR-001 chose a registry whose
records cannot be removed. The tension is only irreconcilable if the thing committed is the
thing that must be erased. Commit the redacted bytes and it dissolves: the published object
and its commitment are retained and remain checkable, and the erasable material — the
unredacted face, the household name — was never what the chain attested to.

The ordering follows from this and is not negotiable. `upload_evidence` hashes at
`main.py:944`, immediately on receipt, which is the correct integrity behaviour and the
wrong privacy behaviour if those bytes carry a face. Anything that changes bytes must
therefore happen before the hash: redaction, and the client-side compression that
offline field capture needs. Two features that look unrelated are sequenced by this.

The client must not compute the hash. The server hashing what it actually received is what
makes `verify_integrity` an independent check rather than a restatement of a client's
claim, and an offline capture queue must not become a way to commit bytes the server never
saw.

Consequence: today nothing redacts, and the hash is over the raw upload — so this decision
describes the boundary the upload path must be moved to, not where it currently sits. Until
it is, operators must not be invited to upload photographs of people. After it is, an
operator cannot prove the unredacted original once its key is destroyed. That is the
intended effect and not a defect: what survives is what the system said it would keep.

Alternatives considered: committing to the raw original and relying on access control,
rejected because a hash of erased bytes is a permanent public record of something that no
longer lawfully exists and cannot be shown to correspond to anything. Storing no original
at all, rejected because redaction is a judgement and an operator who redacted the wrong
region has no recourse. Keeping originals in an off-chain store deleted on request, which
is where crypto-erasure lands anyway but without a defensible story about backups.

Tradeoff: evidence becomes weaker than the camera made it, by design. A redacted
photograph corroborates less than an unredacted one, and the product is choosing the
corroboration it can lawfully keep over the corroboration it could briefly hold.

Date: 2026-09-23

## ADR-012 — Location metadata is kept, and is corroboration rather than proof

Decision: EXIF GPS captured by the device is retained on evidence photographs and is not
stripped by redaction. Faces and identifying content are what redaction removes; where a
delivery happened is what makes the photograph worth anything as evidence.

Why: ADR-011 requires commitments to cover publishable bytes, and #3 asks for personal
data to be minimised before hashing, while field capture wants the coordinates kept as a
corroborating signal. Those pull in opposite directions and the conflict has to be settled
rather than decided by whichever feature is built first. A coordinate is about a place; a
face is about a person. The first is the evidence, the second is the exposure.

Consequence: compression must preserve the EXIF segment. Canvas-based compression discards
it, so a naive "resize before upload" silently destroys the corroboration this decision
keeps — the APP1 segment is carried across explicitly, and a test pins it.

A coordinate is still only a claim about a device, not about the world. It says where a
phone reported being, which a determined operator can falsify, so it corroborates and
never proves. Nothing in verification policy may gate on it.

Alternatives considered: stripping all EXIF, which is the safer default and throws away
the reason to capture in the field at all; and keeping EXIF only for photographs with no
person in them, rejected because that judgement cannot be made reliably and a rule nobody
can apply consistently is worse than a rule stated plainly.

Reversible: retention is a policy, not a commitment. Deciding later to strip coordinates
affects photographs captured after that point and cannot un-commit the ones already
registered, which is the usual asymmetry and is worth knowing before it is relied on.

Date: 2026-09-23
