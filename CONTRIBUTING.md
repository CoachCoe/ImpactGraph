# Contributing

Thanks for your interest in ImpactGraph. This document covers how to get the project
running, what the gates are, and the conventions a change is expected to follow.

## Prerequisites

Python 3.12+, Node 22+, Docker, and [Foundry](https://getfoundry.sh).

## Setup

```bash
make install          # Python venv, npm install
cd contracts && forge build && cd ..   # the API decodes registry events from this artifact
make db-up            # PostgreSQL on host port 55432
make migrate          # Alembic to head
make seed             # deterministic showcase data
```

`make dev` runs the API and web application together. `./scripts/demo.sh up` brings up
a self-contained stack including a local chain.

## Gates

Every change must pass all three suites. CI runs exactly these:

```bash
cd contracts && forge fmt --check && forge test
cd apps/api  && ruff check impactgraph tests scripts && pytest -q
cd apps/web  && npm run lint && npm test -- --run && npm run typecheck && npm run build
```

`make test` runs the three test suites but not the formatter, the linter or the
production build. Run the three commands above before pushing, or CI will catch what
`make test` skips.

Two further suites are not in CI because they need a database and a chain:
`make test-local-e2e` exercises intent → outbox → transaction → validated event →
confirmed state against Anvil, and `make test-browser-e2e` drives Chromium through the
donor and operator journeys. Run them before changing anything on the chain path.

### Do not suppress a gate to pass it

No `@ts-ignore`, no widening to `any`, no `eslint-disable`, no `.skip` or `.only`, no
deleting or weakening assertions, no loosening `tsconfig` or lint configuration, and no
`--no-verify`. If a gate fails, the gate is usually right. Fix the cause.

## Conventions

**Fix causes, not symptoms.** A change that makes a failure invisible is not a fix.

**Money is integer minor units plus an ISO currency code.** Never a float, never a bare
number. A value without its currency is a bug.

**Hashes are canonical and framed.** See [docs/hashing.md](docs/hashing.md). If you
change what goes into a hash, you have changed what a published commitment means — say so
explicitly in the pull request.

**Nothing may imply verification that has not happened.** The product's whole value is
that its claims are checkable. Do not let a mock, a default, or a fallback produce output
that reads as confirmed. Where the demo fabricates something, it must say so.

**Comment the why, not the what.** Record a constraint, a workaround and the bug it
avoids, or an invariant that is not in the type. Do not restate the next line, and do not
leave commentary about the edit itself — that belongs in the commit message.

**Working documents stay out of the repository.** Plans, status reports, findings and
session notes belong in the issue or pull request that owns the conversation. Committed
Markdown is limited to what someone cloning the repository needs.

## Commits and pull requests

One coherent change per commit; do not mix code movement with logic changes. Write the
commit message in the imperative mood and explain why the change is correct, not just
what it does.

Open pull requests against `dev`. Describe what you verified and how — a reviewer should
not have to guess whether a claim was tested or assumed.

## Security

Do not open a public issue for a vulnerability. See [SECURITY.md](SECURITY.md).
