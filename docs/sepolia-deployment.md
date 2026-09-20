# Sepolia deployment record

The deployment described here was executed on 2026-09-20 and the golden path completed
against the public network; the record below is of what actually happened. Never record a
private key, seed phrase, mnemonic, or authenticated RPC URL here.

## Required inputs

- `SEPOLIA_RPC_URL`: authenticated endpoint, supplied through the environment
- `DEPLOYER_PRIVATE_KEY`: funded Sepolia-only deployer, supplied through the environment
- `OPERATOR_WALLET_ADDRESS`: operator address
- `VERIFIER_WALLET_ADDRESS`: distinct independent verifier address
- `BLOCK_EXPLORER_URL=https://sepolia.etherscan.io`

## Explicit procedure

1. Run `make new-sepolia-demo-run` and retain the identifier.
2. Confirm the target chain is `11155111` and the deployer address is expected.
3. Run `SEPOLIA_RPC_URL=... make deploy-sepolia`.
4. Set the returned `IMPACT_REGISTRY_ADDRESS` locally.
5. Run `make configure-sepolia-roles` with all required environment values.
6. Verify `ADMIN_ROLE`, `OPERATOR_ROLE`, and `VERIFIER_ROLE` using `cast call`.
7. Run the golden path and retain every transaction hash.
8. Verify the expected registry events and confirmation depth through the API.
9. Open each transaction and the contract on the configured explorer.

## Public deployment record

```text
Demo run ID: clean-water-kenya-2026-sepolia-20260920-150700-27354bb8
Chain ID: 11155111
Contract address: 0xc581790457e726eed7db63b3de489c631bf7ca2b
Deployment transaction: 0xdf77a85d670210cf1379e4949b169a445b35fb5fe197b013cb4db6a8a49c3caf
Deployment block: 11744994
Deployer address: 0xb4Dac10Ad480b20F6793477FBdddfe8651d2676c
Operator address: 0xb4Dac10Ad480b20F6793477FBdddfe8651d2676c
Verifier address: 0xEAcA6bce983ACBDbad39144A84491A2bf590484A
Role configuration: verified onchain by scripts/deploy-sepolia.sh
Program entity: 0x9121824ece0f888aae7376e0c91ad287ffee5c92bb3b660ab84863b2bb4caca3
Claim entity: 0x1427f54a3dc3375e93c4c07716088433fad3a740b246d03dd6f461525d77bd89
Evidence registration transaction: 0x541c17130a0dcc3cdcc1acc19a17f437673e2f704beda5a6cf1ac5899902cc9a
Evidence registration block: 11745028
Evidence commitment: sha256:db91a11cf5cec7ac678bc93ae50cc38aae0f8453956041fe633c3fd566b78073
Verifier attestation transaction: 0x172c09a2de53b61d6d5f025566acb8174aea6ac327e201bbf5ad27a096d2dff7
Verifier attestation block: 11745139
Verifier attestation issuer (onchain): 0xEAcA6bce983ACBDbad39144A84491A2bf590484A
Onchain attestation id: 0xcc6c07b9218dce2710d5ac3bd99a5cd9e8aa7f11c6a47075b1f3b018a1124a20
Verification bundle hash: sha256:5ad180281aa741f93e8f38bf6fba3930a33b2d38fc91827e5980f6a0d11bfb79
Claim status: VERIFIED
Verified at: 2026-09-20T15:37:25Z
Policy: 8 of 8 requirements pass; evidence score 97/100
Explorer: https://sepolia.etherscan.io/address/0xc581790457e726eed7db63b3de489c631bf7ca2b
```

Do not mark any value as verified until a successful receipt, expected `ImpactRegistry`
event, and configured confirmation depth have been independently observed.

## Verified against the public network, 2026-09-20

Checked independently of the deployment script's own report:

- Registry holds 6,433 bytes of code at the recorded address.
- Operator holds `OPERATOR_ROLE`; verifier holds `VERIFIER_ROLE`; the verifier holds
  **no** operator role, so separation of duties is enforced on chain and not merely in
  the application.
- Program and claim entities both exist.
- The seeded invoice's commitment is registered, operator-signed, and the backend accepted
  the receipt only after confirming the event came from this registry and committed the
  expected hash.
- Integrity verification resolves the commitment **from Sepolia** and reports MATCH.
- The claim sits at `VERIFICATION_PENDING` with six of eight policy requirements passing.
  The two outstanding — `INDEPENDENT_VERIFICATION` and `BUNDLE_CURRENT` — are exactly what
  the verifier's attestation resolves.


### The tampering demonstration is disabled here, deliberately

`POST /demo/evidence/{id}/tamper` refuses with 409 when `DEMO_MODE=sepolia`. Altering
evidence in a public run would leave a permanently mismatched commitment on a chain that
cannot be reset. The tampering beat belongs to the local demo (`./scripts/demo.sh`), which
is disposable; Sepolia carries the public-proof half of the story instead.

## The golden path completed on Sepolia, 2026-09-20

The claim reached `VERIFIED` through the real path: an independent verifier signed an
attestation from a browser wallet, and the backend transitioned the claim only after
observing the receipt, the expected event, the committed bundle hash and the confirmation
depth.

Read back from the chain rather than from our own records:

```text
AttestationCreated
  attestationId          0xcc6c07b9218dce2710d5ac3bd99a5cd9e8aa7f11c6a47075b1f3b018a1124a20
  subjectId              0x1427f54a3dc3375e93c4c07716088433fad3a740b246d03dd6f461525d77bd89
  issuer                 0xEAcA6bce983ACBDbad39144A84491A2bf590484A
  attestationType        1  (INDEPENDENT_VERIFIER)
  statementHash          0x22a97a7d2f721d60c7572d652858e8f6d734da22041da0630c06594c8c0f9bff
  verificationBundleHash 0x5ad180281aa741f93e8f38bf6fba3930a33b2d38fc91827e5980f6a0d11bfb79
```

The issuer is the verifier's wallet, distinct from the operator's, and the bundle hash is
the one the claim carried when the intent was issued.

### Why filtering by emitting address, not by transaction target, was the right call

The attestation was not sent directly to the registry. `tx.to` is
`0xdb9b1e94b5b69df7e401ddbede43491141047db3` — a smart-account contract the wallet routed
through — and the registry appears only among the emitted logs.

Confirmation checks that the expected event was **emitted by the registry**, so this was
accepted correctly. The original review of that finding also suggested asserting
`tx.to == registry_address` as extra hardening. That would have rejected this transaction:
a legitimate attestation, signed by the right wallet, recorded by the registry. Smart
accounts, batchers and relayers all break the assumption that the caller talks to the
contract directly. The emitting address is the thing that actually matters.

registration and the attestation.
