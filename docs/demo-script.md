# Demo script

## What can be demonstrated today

Every step below is performable in the browser. Setup: `make db-up && make migrate`, then `make anvil` and `make deploy-local`. Put the deployed address in `IMPACT_REGISTRY_ADDRESS`, `EVM_SENDER_ADDRESS` to the deployer and `VERIFIER_WALLET_ADDRESS` to a different local account -- `make bootstrap-chain` requires all three. Then `make bootstrap-chain`, `make seed`, `make api`, `make web`. Seed after bootstrapping: seeding registers the showcase commitment on the registry, and integrity verification reads it back from there.
`make db-up && make migrate && make seed`, then `make anvil`, `make deploy-local`,
`make bootstrap-chain`, `make api`, `make web`. `make bootstrap-chain` is required — without
it the registry has no program or claim entity and both journeys revert on chain. `make seed`
prints the demo account email addresses and their shared password.

1. **Donor context.** Open the donor dashboard, signed out. Introduce the Clean Water Kenya
   2026 program. Every figure here is read from the API, so it moves with the seed.
2. **Follow the money.** Open the claim. The provenance panel renders the graph the backend
   returns: funding → allocation → TX-9182 → delivery → outcome → claim, with the evidence
   branching off. The sidebar shows the policy requirements and why the claim is not yet
   verified, and the explainable score the backend computed.
3. **Evidence and integrity.** Follow the evidence node into the Evidence Inspector, still
   signed out. Press "Verify evidence integrity": the backend re-reads the stored object,
   rehashes it and reports MATCH. Explain what a byte match proves — that the bytes have
   not changed since registration — and what it does not: that the document is true.
4. **Operator journey.** Sign in as the operator. On `/operator`, load
   the INV-8291 fixture, upload and analyze, review the extraction and the deterministic
   reconciliation including its GPS warning, then accept and register. Narrate the states:
   the registration intent is persisted, the outbox worker submits, and the screen stays
   pending until the backend has independently observed the receipt, the expected
   `EvidenceRegistered` event from the configured registry, and the confirmation depth.
5. **Verifier journey.** Sign in as the verifier. On `/verifier`, press "Verify with
   wallet". The first time, the wallet signs a challenge proving it controls the address —
   this authorises no transaction, and the backend recovers the address from the signature
   rather than believing a header. Then the wallet signs the attestation over the exact
   bundle hash shown on screen. Narrate awaiting signature → submitted → confirmed, and that
   SUBMITTED is not success: the claim stays VERIFICATION_PENDING until the backend observes
   the receipt, the expected event from the configured registry, the committed bundle hash
   and the confirmation depth. Then return to the claim: it now reads VERIFIED, with the
   verifier's address and the attestation transaction.
6. **Tampering.** Sign in as the administrator and open `/admin`. Press "Tamper with stored
   evidence": this overwrites the stored bytes, not a flag. Re-run the integrity check —
   MISMATCH — and follow the link to the donor-facing evidence page to show the same result
   to an anonymous visitor. The registered commitment is unchanged: ImpactGraph can alter
   its own storage, but it cannot retroactively change what was committed. "Reset demo"
   restores the pristine bytes.

End: "ImpactGraph connects money, activity, evidence and outcomes into a verifiable chain
of provenance." Be explicit about what is mocked: the bank, the AI extraction, the NGO
records and every document are fictional. The registry, the commitments, the wallet
signature, the receipt and event validation, and the integrity check are real.
