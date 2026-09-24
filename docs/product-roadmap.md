# Product roadmap

ImpactGraph exists to answer one donor question: **“You say my money produced this
outcome. Show me why.”** The product connects a contribution to its allocation, observed
spend, delivery evidence, independent verification and measured outcome without asking AI
or a blockchain to prove more than either can prove.

This is a maintained product roadmap, not an implementation plan. “De-risked” means the
proof of concept contains an executable path and tests for the underlying decision; it does
not mean the capability is ready for real money, vulnerable people or production evidence.

## Product principles

- The donor starts with one contribution and can inspect every downstream record.
- Operators review extracted facts; AI may read and correlate evidence but cannot verify it.
- Independent verifiers sign the exact evidence bundle they reviewed.
- Missing, disputed and failed records remain visible rather than being rounded into success.
- ImpactGraph observes money. A regulated provider moves it.

## 30–90 day sequence

| Product area | De-risked by this POC | Next 30 days | Deliberately deferred and why |
| --- | --- | --- | --- |
| Donor trust | Public contribution-to-outcome attribution, provenance, evidence integrity checks, proof pages and change notifications | Test the contribution-first landing with donors; add program failure/remediation reporting; publish cost per outcome with its method | Personalized donor accounts until identity, consent and support obligations are understood |
| AI-assisted evidence | Inkling Small reads invoice text or images into a strict schema with per-field self-reported confidence; reconciliation-critical fields always require human confirmation | Verify the hosted deployment is using `AI_PROVIDER=tinker`, visibly show provider/model and review boundaries, and measure extraction corrections without treating model confidence as accuracy | Automated approval or verification: the model cannot establish that a document or outcome is true |
| Funding intake | Funding may arrive before program assignment; individual and aggregated contributions remain traceable; the system observes rather than initiates payments | Select the organisation’s contribution provider, then ingest signed, idempotent success/refund/dispute webhooks into `FundingRecord`; prefer GoCardless for UK recurring direct debit or Stripe for international cards only after the operating model is confirmed | Building payment initiation, custody or payout infrastructure: regulated providers already solve it and the risk is outside the product’s differentiator |
| Financial activity | Integer-minor-unit ledger, allocation controls, settlement states, reconciliation, held-versus-deployed reporting and a TrueLayer adapter seam | Connect a sandbox bank account, exercise credential rotation, and reconcile real provider payloads without exposing credentials to operators | Multi-currency aggregation until an auditable exchange-rate policy exists |
| Field evidence | Mobile capture survives restarts and weak connectivity; original bytes are encrypted, hashed and registered through a durable outbox | Pilot capture with field operators; add PDF page rendering and production object-storage controls | Broad offline workflow expansion until the narrow photograph journey survives real devices and poor networks |
| Verification and safeguarding | Independent wallet attestations, configurable thresholds, beneficiary disputes and a risk-review queue | Complete verifier onboarding and the beneficiary safeguarding review before enabling outreach | Identity proofing and production beneficiary contact until legal, safeguarding and escalation ownership are staffed |
| Operations | Health probes, structured logs, metrics, migrations, containers, CI and browser journeys | Add hosted alerts, backup/restore rehearsal, incident ownership and a documented recovery target | Multi-region and high-availability infrastructure until usage demonstrates the need |

## First 12 months

The first year should turn the proven seams into a small production system: a live donor
experience, one real contribution provider, one real bank-data connection, field-tested
evidence capture, an operating verifier network, published failures and outcome methods,
and measured extraction/reconciliation quality. Production readiness additionally requires
managed secrets and keys, hardened storage, backups, SSO/MFA, identity proofing, malware
scanning, privacy operations and an independent contract and application security review.

The system should scale from observed demand rather than forecasts. The defensible part is
the connected, inspectable provenance record and the policy around what may be claimed—not
custom payment rails, an opaque impact score or infrastructure built for hypothetical load.
