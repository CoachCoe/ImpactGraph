# Trust model

## Users trust Ethereum for

- persistence of compact evidence and claim commitments;
- ordering and timestamps within normal blockchain semantics;
- the wallet address that issued an attestation;
- tamper-evident transaction/event history.

## Users trust ImpactGraph for

- application presentation and offchain indexing;
- wallet-to-organization identity mapping;
- workflow implementation, reconciliation rules, and policy versions;
- correctly retrieving evidence and calculating its current byte hash.

## Users trust external actors for

- source-document accuracy and payment-provider records;
- field reports and real-world delivery statements;
- operator honesty and independent verifier judgment.

ImpactGraph is not trustless, fully decentralized, or an oracle of objective truth. It
makes this chain of trust visible, preserves commitments, and makes later alteration
detectable. “Verified on Ethereum” means a verifier attestation and its evidence bundle
are confirmed there—not that Ethereum observed the real-world outcome.

