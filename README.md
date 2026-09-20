# ImpactGraph

ImpactGraph is a Transparency Layer connecting funding, observed financial activity,
real-world delivery, evidence, attestations, and outcomes into an inspectable provenance
graph. It answers: **“You say my money produced this outcome. Show me why.”**

Ethereum is used for immutable commitments, provenance, and identified attestations—not
for moving the program's fiat money or storing documents. AI extracts and correlates; it
cannot establish verification.

```text
Donor / Operator / Verifier UI
              ↓
      FastAPI Transparency API
       ↓       ↓          ↓
 PostgreSQL  Evidence   AI/financial mocks
       ↓
 Outbox / chain observer ↔ ImpactRegistry ↔ Anvil or Sepolia
```

> **Proof of concept.** This is a demonstration of an architecture, not production
> software. It is unaudited, the contract has had no third-party review, and the
> financial, AI, and NGO data are deterministic fixtures. Do not deploy it to mainnet or
> use it with real funds. See [Limitations](#limitations) and [SECURITY.md](SECURITY.md).

## Quick start

Requires Python 3.12+, Node 22+, Docker, and Foundry.

If `make` or `git` fails with "You have not agreed to the Xcode license agreements", run
`sudo xcodebuild -license`. Until then `/usr/bin/make` and `/usr/bin/git` are unusable,
though the binaries under `/Library/Developer/CommandLineTools/usr/bin/` still work.

ImpactGraph maps PostgreSQL to host port `55432` to avoid common local PostgreSQL and
other Docker development ports. The database itself still listens on 5432 in its container.

```bash
cp .env.example .env
make install
make db-up
make migrate
make seed
```

Then start the chain and the applications:

```bash
make anvil                      # terminal 1
make deploy-local               # terminal 2 -- needs DEPLOYER_PRIVATE_KEY in the environment
# put the deployed address in IMPACT_REGISTRY_ADDRESS, set EVM_SENDER_ADDRESS to the deployer
# and VERIFIER_WALLET_ADDRESS to a *different* local account, then:
#
# Seed AFTER the registry exists. Seeding is what registers the showcase commitment, and
# integrity verification reads that commitment back from the registry -- seed first and the
# check answers 409 for the rest of the run.
make bootstrap-chain
make api                        # terminal 2
make web                        # terminal 3
```

Open <http://localhost:3000>. Local Anvil deployment needs `DEPLOYER_PRIVATE_KEY` from a
development account in the shell environment; never commit it.

`make bootstrap-chain` is required. Deployment alone leaves the registry empty, so the program
and claim entities the golden path commits against do not exist and `registerEvidence` reverts
with `UnknownProgram` while `createAttestation` reverts with `UnknownEntity`. The command
creates both entities, grants `VERIFIER_ROLE` to the configured verifier wallet, refuses to
grant it to the operator's own address, is idempotent, and refuses to run against Sepolia.

Operators, verifiers and administrators sign in at `/login`; `make seed` creates the demo
accounts and prints their email addresses and shared password. Reading a claim, its provenance,
its evidence and its integrity needs no account — public verifiability is the point.
The showcase starts at VERIFICATION_PENDING. `make demo-reset` is explicitly local-only. The verifier screen uses
viem and an injected wallet to submit the attestation, while the backend independently
validates the receipt, the expected registry event, the verification-bundle hash committed
onchain and the confirmation depth before changing the claim to VERIFIED.

The operator screen accepts an evidence file or can load the INV-8291 fixture. It exercises
upload, original-byte hashing, mock structured extraction, reconciliation, operator review,
durable outbox registration, and independently observed confirmation through the API. This
is the one journey that is wired end to end through the real persistence and chain path.
The mock extractor only recognises documents containing `INV-8291`; anything else is
rejected with a 422. Backend signing is restricted to the configured unlocked local Anvil
sender; the application does not silently use this path for Sepolia.

The donor dashboard, Claim Inspector and Evidence Inspector read the API. The claim page
renders the policy requirements, the explainable score and the bundle hash the backend
computed, and shows VERIFIED once the backend gets there. The evidence page runs the
integrity check on demand, and `/admin` drives the tampering demonstration.

## Run the demo

One command, nothing else installed but Docker:

```bash
cd contracts && forge build && cd ..   # the API image deploys from this artifact
./scripts/demo.sh up
```

That starts a disposable chain, deploys the registry, creates the onchain program and
claim entities, migrates, seeds, and smoke-tests the result — then prints the URLs. The
whole golden path completes on it, including the wallet-signed attestation. The showcase
resets itself every 30 minutes, because anyone can drive it to VERIFIED.

`./scripts/demo.sh down` discards the chain and the data; `reset` puts the showcase back
immediately; `logs` follows a service.

The chain publishes port 8545 so a browser wallet can reach it. If something else on the
host already holds that port, the wallet will silently talk to the wrong chain — set
`CHAIN_PORT` and `PUBLIC_RPC_URL` to move it.

On a host, set `PUBLIC_API_URL` and `PUBLIC_RPC_URL` to the addresses a visitor's browser
will use; they are inlined into the web bundle at build time.

This stack is deliberately separate from `docker-compose.prod.yml`, which points at a real
network and must never run a chain of its own.

## Deployment

Two separate things, deliberately kept apart: putting the contract on a public chain, and
running the application.

### Sepolia

```bash
export SEPOLIA_RPC_URL=...          # authenticated endpoint
export DEPLOYER_PRIVATE_KEY=...     # Sepolia-only, funded
export OPERATOR_WALLET_ADDRESS=...
export VERIFIER_WALLET_ADDRESS=...  # must differ from the operator and the deployer
./scripts/deploy-sepolia.sh
```

The script deploys the registry, assigns the operator and verifier roles, bootstraps the
program and claim entities, verifies all five facts on chain with `cast`, and writes the
addresses and transaction hashes into [`docs/sepolia-deployment.md`](docs/sepolia-deployment.md).

It is not a Make target because it is irreversible and public. It refuses to run in CI,
checks the RPC really reports chain 11155111, checks the deployer is funded, refuses a
verifier equal to the operator or the deployer, prints what it is about to do, and requires
you to type `deploy to sepolia`. Nothing is recorded unless the on-chain verification
passes. It never runs merely because an RPC URL is set.

Entity identifiers and commitments are computed by `make chain-args` and passed to the
Foundry script: they use the framed encoding in [`docs/hashing.md`](docs/hashing.md), which
Solidity cannot reproduce. Signing stays in Foundry — the backend holds no key material,
and verifier attestations are signed by an externally managed browser wallet.

Afterwards set `CHAIN_ID=11155111`, `DEMO_MODE=sepolia`, the RPC, the registry address and
the explorer URL in your environment, leaving `EVM_SENDER_ADDRESS` empty. Use
`make new-sepolia-demo-run` to mint a collision-free identifier for each public rehearsal
rather than trying to reset a public chain. The individual steps remain available as
`make deploy-sepolia` and `make configure-sepolia-roles` if you would rather drive them
yourself.

No Sepolia deployment is claimed in this repository until a real address and transaction
are recorded in [`docs/sepolia-deployment.md`](docs/sepolia-deployment.md).

### Registering the seeded evidence on a public network

The backend holds no key on a public network, so it cannot register the seeded commitment
itself. The operator signs it through Foundry, and the backend records the result only
after verifying the receipt:

```bash
export IMPACT_REGISTRY_ADDRESS=0x...        # from the deployment
export RPC_URL=https://...                  # your Sepolia RPC
export DEPLOYER_PRIVATE_KEY=0x...           # the operator wallet
./scripts/register-seed-evidence.sh
```

It checks the wallet actually holds `OPERATOR_ROLE` before spending anything, refuses if
the evidence is already registered (registrations are immutable by design), and records
the transaction only if the receipt carries an `EvidenceRegistered` event for that
evidence, from that registry, committing the expected hash.

Without this step, integrity verification returns 409: it reads the commitment back from
the registry, and there is nothing to read.

### The application

Platform-neutral containers: an API image, a web image, an outbox worker sharing the API
image, and PostgreSQL. They run on any host that speaks Docker Compose, and the same images
push to any registry.

```bash
cp .env.production.example .env.production   # then fill it in
./scripts/deploy.sh release                  # build, up, migrate, seed, smoke
```

Individual steps are `build`, `push`, `up`, `migrate`, `seed`, `smoke`, `logs`, `down`, also
available as `make deploy-build`, `make deploy-up`, `make deploy-release` and so on.

`release` ends with a smoke test that checks the API health endpoint, the program, claim and
provenance read models, the web app, and that evidence integrity resolves the stored object
and reports MATCH — not merely that a container started.

Preflight refuses to start when the contract ABI has not been built, when `AI_PROVIDER`
names a provider that does not exist, and, on Sepolia, when `CHAIN_ID` is wrong, the
registry address is missing, or `EVM_SENDER_ADDRESS` is set — the backend must not sign on a
public network.

`NEXT_PUBLIC_*` values are inlined into the web bundle at build time, so changing them
requires a rebuild rather than a restart.

Not included, and required before this faces real users: TLS termination, a managed database
with backups, production secret management, MFA/SSO, and identity proofing. The POC does
have Argon2 password authentication, revocable server-side sessions, role checks, wallet
proof-of-control, and project-scoped operator authorization. See
[security](docs/security.md).

## Financial data

Money is always an integer number of minor units plus an ISO currency, never a float:
$4,200.00 is `420000` and `"USD"`. `Money` refuses a float, a bool, a negative, and any
attempt to add two currencies without a recorded rate.

Funding, allocations, observed payments, deliveries and outcomes are real records. Payments
are imported from a `FinancialDataProvider` rather than written by hand, and are unique per
provider reference, so re-importing a statement records nothing new. Spend beyond the
remaining allocation is rejected rather than recorded.

Reconciliation resolves the payment and delivery an invoice refers to *from the ledger* and
compares against those. A document with no counterpart payment is `UNMATCHED`; one that
contradicts the payment it cites is `CONFLICT`. The verdict is written back onto the
payment, so the money trail shows which spend is evidenced and which is not.

```bash
curl localhost:8000/financial/programs/program-clean-water-kenya-2026   # public
curl localhost:8000/financial/transactions/ftx-9182                     # public
# operator only, idempotent:
curl -X POST -H 'Idempotency-Key: ...' \
  localhost:8000/financial/programs/program-clean-water-kenya-2026/import
```

The seeded statement deliberately contains a payment with no invoice and one that
contradicts the invoice it cites, so `UNMATCHED` and `CONFLICT` are states the demo can
actually reach. `/financial` shows the whole trail without an account.

## Tests

```bash
make test-contracts
make test-api
make test-web
make test-browser-e2e
cd apps/web && npm run build
```

With PostgreSQL, Anvil, and a deployed local registry configured, run
`make test-local-e2e` to exercise database intent → outbox → EVM transaction → validated
event → confirmed database state. `make test-browser-e2e` starts the API and web application
and drives Chromium through donor provenance inspection and durable operator evidence
registration. Install its browser once with `cd apps/web && npx playwright install chromium`.

See [architecture](docs/architecture.md), [trust model](docs/trust-model.md),
[provenance](docs/provenance-model.md), [state machines](docs/state-machines.md),
[hashing](docs/hashing.md), the [demo script](docs/demo-script.md), the
[security notes](docs/security.md), and the [decision record](docs/decisions.md).

## Limitations

The banking provider, AI provider, NGO records, outcomes and all demo evidence are
fictional and deterministic. Never present mock or Anvil results as Ethereum verification.

What is real: the Solidity registry and its role checks; evidence upload, original-byte
hashing and the immutability of the registered commitment; integrity verification against the stored bytes; the
transactional outbox, idempotency records, audit log and correlation IDs; the chain
observer's receipt, event, bundle-hash and confirmation-depth checks; and the verifier
wallet signing path.

What is not:

- **Authentication is basic by design.** Email and password with server-side sessions; no
  SSO, MFA or password reset. See [security](docs/security.md) and ADR-005.
- **No payment *initiation*, by design.** ImpactGraph observes financial activity through
  a `FinancialDataProvider` and never moves money. The provider is a deterministic mock;
  a real adapter implements the same interface without anything downstream changing.
- **No AI.** The extractor is a fixed dictionary returned for documents containing
  `INV-8291`. `AI_PROVIDER` set to anything but `mock` fails at startup rather than silently using the mock.
- **Incomplete verifier decisions.** Reject is implemented as an idempotent audited domain
  operation. Request-more-evidence remains disabled because the specification does not define
  whether it returns a claim to EVIDENCE_PENDING or leaves it VERIFICATION_PENDING.
- **Attestation state dimensions are not separated.** A single attestation status still carries
  wallet/chain progress; blockchain confirmation and semantic review need separate fields.
- **Partial evidence set.** The current seeded verification bundle contains the real invoice
  commitment only. Receipt, completion report, and photograph records/artifacts remain to be
  seeded and linked before the five-object showcase is complete.
- **The seeded operator attestation is a fixture.** `seed_read_model` writes an OPERATOR
  attestation with a placeholder wallet and transaction hash, marked CONFIRMED, and the
  claim API serves it exactly as it would a real one. It satisfies the
  `OPERATOR_ATTESTATION` requirement without any chain activity. Only the *verifier*
  attestation path is real end to end.
- **No observability.** `structlog` is a declared dependency with no call sites.
- **Sepolia only, and unaudited.** The registry is deployed to Sepolia and the golden
  path has completed there; see [docs/sepolia-deployment.md](docs/sepolia-deployment.md).
  Nothing here has been audited and none of it belongs on mainnet.
- **Two parallel models.** Four endpoints run only on the in-process demo store:
  `POST /verification-requests/{id}/submit-attestation`,
  `POST /blockchain/operations/{id}/confirm-evidence-demo`,
  `POST /blockchain/operations/{id}/confirm-demo` and
  `GET /blockchain/transactions/{hash}`. The middle two fabricate onchain confirmation and
  the first fabricates a transaction hash. All are listed in
  [security](docs/security.md) rather than left to be discovered.
- **CI coverage is narrower than the trust boundary.** CI runs Foundry, backend unit/API,
  frontend unit/type, and production build gates, but not PostgreSQL migrations, Anvil-backed
  integration, or Playwright. Run `make test-local-e2e` and `make test-browser-e2e` locally
  before changing anything on the chain path.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, the gates a change must pass, and the
conventions this codebase follows.

## Security

This is an unaudited proof of concept. Do not deploy it to mainnet or use it with real
funds. To report a vulnerability, see [SECURITY.md](SECURITY.md) — please do not open a
public issue.

## License

[MIT](LICENSE) © 2026 Shawn Coe.
