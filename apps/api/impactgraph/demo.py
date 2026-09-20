from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from .hashing import claim_hash, sha256_bytes, verification_bundle_hash
from .verification import EvidenceScoreService

INVOICE_BYTES = b"IMPACTGRAPH DEMO INVOICE\nInvoice INV-8291\nAqua Systems Ltd.\n2 x AquaPure X200\nUSD 4,200.00\nProject: Water Project #12\nDate: 2026-08-17\n"
# The tampering demo overwrites the stored object with these bytes. The altered amount is
# what a bad actor editing the invoice after the fact would change.
TAMPERED_INVOICE_BYTES = INVOICE_BYTES.replace(b"USD 4,200.00", b"USD 9,800.00")


class DemoStore:
    """Deterministic in-process read model used by the UI and API smoke tests.

    PostgreSQL mappings/outbox are the durable architecture; this read model keeps the
    cloned POC instantly demonstrable when infrastructure is not running.
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.verified = False
        self.idempotency: dict[str, dict[str, Any]] = {}
        self.evidence_operations: dict[str, dict[str, Any]] = {}
        evidence_hash = sha256_bytes(INVOICE_BYTES)
        self.evidence: dict[str, Any] = {
            "ev-inv-8291": {
                "id": "ev-inv-8291",
                "type": "INVOICE",
                "filename": "INV-8291.txt",
                "contentHash": evidence_hash,
                "mimeType": "text/plain",
                "visibility": "PUBLIC",
                "workflowStatus": "SUBMITTED_FOR_VERIFICATION",
                "analysisStatus": "COMPLETED",
                "integrityStatus": "MATCH",
                "blockchainStatus": "CONFIRMED",
                "uploadedBy": "Global Water Initiative",
                "source": "Operator upload",
                "storageUri": "",
                "tampered": False,
                "extraction": {
                    "documentType": "invoice",
                    "invoiceNumber": "INV-8291",
                    "vendor": "Aqua Systems Ltd.",
                    "amountMinor": 420000,
                    "currency": "USD",
                    "date": "2026-08-17",
                    "equipment": "AquaPure X200",
                    "quantity": 2,
                    "projectReference": "Water Project #12",
                    "confidence": 0.97,
                },
                "reconciliation": {
                    "status": "MATCHED",
                    "checks": [
                        {
                            "check": "VENDOR",
                            "result": "PASS",
                            "message": "Vendor matches approved vendor",
                        },
                        {
                            "check": "AMOUNT",
                            "result": "PASS",
                            "message": "Amount matches financial transaction TX-9182",
                        },
                        {
                            "check": "EQUIPMENT",
                            "result": "PASS",
                            "message": "Equipment matches delivery",
                        },
                        {
                            "check": "PROJECT",
                            "result": "PASS",
                            "message": "Project reference matches",
                        },
                        {
                            "check": "COMPLETION_REPORT",
                            "result": "PASS",
                            "message": "Completion report supports installation",
                        },
                        {
                            "check": "PHOTO_GPS",
                            "result": "WARNING",
                            "message": "One installation photograph has no GPS metadata, so it cannot corroborate "
                    "the location on its own",
                        },
                    ],
                },
                "blockchainReference": {"transactionHash": "0x" + "2" * 64, "blockNumber": 9123401},
            }
        }
        payload_hash = claim_hash(
            "claim-water-12-200",
            "200 households gained access to clean drinking water.",
            "outcome-water-12-200",
        )
        self.bundle = verification_bundle_hash(
            "claim-water-12-200",
            payload_hash,
            [evidence_hash, "sha256:" + "3" * 64, "sha256:" + "4" * 64],
            "outcome-water-12-200",
            ["funding-jane-10000", "allocation-water-12-8500", "ftx-9182", "delivery-water-12"],
            "1.0",
        )
        self.claim = {
            "id": "claim-water-12-200",
            "programId": "program-clean-water-kenya-2026",
            "projectId": "project-water-12",
            "statement": "200 households gained access to clean drinking water.",
            "status": "VERIFICATION_PENDING",
            "payloadHash": payload_hash,
            "verificationBundleHash": self.bundle,
            "policyVersion": "1.0",
            "evidenceIds": list(self.evidence),
            "attestations": [
                {
                    "id": "att-operator-water-12",
                    "type": "OPERATOR",
                    "issuer": "Global Water Initiative",
                    "wallet": "0x1111111111111111111111111111111111111111",
                    "status": "CONFIRMED",
                }
            ],
        }
        self.pending_tx: dict[str, Any] | None = None

    def remember_idempotent(
        self, key: str, request_fingerprint: str, value: dict[str, Any]
    ) -> dict[str, Any]:
        existing = self.idempotency.get(key)
        if existing:
            if existing["fingerprint"] != request_fingerprint:
                raise ValueError("Idempotency key was already used for a different request")
            return deepcopy(existing["value"])
        self.idempotency[key] = {"fingerprint": request_fingerprint, "value": deepcopy(value)}
        return value

    def programs(self) -> list[dict[str, Any]]:
        return [self.program()]

    def program(self) -> dict[str, Any]:
        return {
            "id": "program-clean-water-kenya-2026",
            "name": "Clean Water Kenya 2026",
            "operator": "Global Water Initiative",
            "region": "Kisumu County, Kenya",
            "status": "ACTIVE",
            "funding": {"amountMinor": 10000000, "currency": "USD"},
            "deployed": {"amountMinor": 8742000, "currency": "USD"},
            "filtrationSystems": 12,
            "peopleServed": 2840,
            "verificationPercent": 92,
            "featuredClaimId": self.claim["id"],
        }

    def project(self) -> dict[str, Any]:
        return {
            "id": "project-water-12",
            "name": "Water Project #12",
            "programId": "program-clean-water-kenya-2026",
            "budget": {"amountMinor": 850000, "currency": "USD"},
            "spent": {"amountMinor": 420000, "currency": "USD"},
            "deliveries": 1,
            "evidenceObjects": 5,
            "verification": self.claim["status"],
            "actions": ["ADD_FINANCIAL_TRANSACTION", "RECORD_DELIVERY", "UPLOAD_EVIDENCE"],
        }

    def graph(self) -> dict[str, Any]:
        nodes = [
            {
                "id": "funding-jane-10000",
                "type": "FUNDING",
                "title": "Jane Smith",
                "detail": "$10,000 contributed",
            },
            {
                "id": "allocation-water-12-8500",
                "type": "ALLOCATION",
                "title": "Water Project #12",
                "detail": "$8,500 allocated",
            },
            {
                "id": "ftx-9182",
                "type": "FINANCIAL_TRANSACTION",
                "title": "Aqua Systems Ltd.",
                "detail": "$4,200 observed payment",
            },
            {
                "id": "delivery-water-12",
                "type": "DELIVERY",
                "title": "2 filtration systems",
                "detail": "AquaPure X200",
            },
            {
                "id": "ev-inv-8291",
                "type": "EVIDENCE",
                "title": "Invoice INV-8291",
                "detail": "Integrity confirmed",
            },
            {
                "id": "outcome-water-12-200",
                "type": "OUTCOME",
                "title": "200 households served",
                "detail": "Kisumu County",
            },
            {
                "id": self.claim["id"],
                "type": "CLAIM",
                "title": "Clean drinking water",
                "detail": self.claim["status"],
            },
        ]
        edges = [
            {"source": nodes[0]["id"], "relationship": "FUNDS", "target": nodes[1]["id"]},
            {"source": nodes[1]["id"], "relationship": "PAYS", "target": nodes[2]["id"]},
            {"source": nodes[2]["id"], "relationship": "SUPPORTS", "target": nodes[3]["id"]},
            {"source": nodes[4]["id"], "relationship": "EVIDENCES", "target": nodes[3]["id"]},
            {"source": nodes[3]["id"], "relationship": "PRODUCES", "target": nodes[5]["id"]},
            {"source": nodes[5]["id"], "relationship": "SUPPORTS", "target": nodes[6]["id"]},
            {"source": nodes[4]["id"], "relationship": "SUPPORTS", "target": nodes[6]["id"]},
        ]
        return {"nodes": nodes, "edges": edges}

    def verification(self) -> dict[str, Any]:
        requirements = [
            ("PROVENANCE_COMPLETE", True, "Required provenance is complete"),
            ("EVIDENCE_REGISTERED", True, "Required evidence commitments are confirmed onchain"),
            (
                "EVIDENCE_INTEGRITY",
                not self.evidence["ev-inv-8291"]["tampered"],
                "Current evidence matches its commitments",
            ),
            ("FINANCIAL_RECONCILIATION", True, "Required deterministic reconciliation passed"),
            ("OPERATOR_ATTESTATION", True, "Operator attestation is confirmed"),
            (
                "INDEPENDENT_VERIFICATION",
                self.verified,
                "Independent verifier attestation is confirmed onchain"
                if self.verified
                else "Independent verification is pending",
            ),
            (
                "BUNDLE_CURRENT",
                self.verified,
                "Attestation is bound to the current verification bundle"
                if self.verified
                else "No confirmed verifier attestation is bound to this bundle",
            ),
            ("ACTOR_SEPARATION", True, "Verifier is distinct from operator"),
        ]
        score = EvidenceScoreService().score(
            financial=True,
            integrity=not self.evidence["ev-inv-8291"]["tampered"],
            operator=True,
            verifier=self.verified,
            location_points=7,
            consistency=True,
        )
        return {
            "claimId": self.claim["id"],
            "status": self.claim["status"],
            "policyVersion": "1.0",
            "requirements": [
                {"requirement": name, "status": "PASS" if passed else "FAIL", "reason": reason}
                for name, passed, reason in requirements
            ],
            "evidenceScore": score,
            "verificationBundleHash": self.bundle,
        }

    def submit_verification(self) -> dict[str, Any]:
        if self.verified:
            return deepcopy(self.pending_tx or {})
        self.pending_tx = {
            "operationId": "bcop-verifier-water-12",
            "status": "SUBMITTED",
            "transactionHash": "0x" + "7ab" * 21 + "7",
            "expectedEvent": "AttestationCreated",
            "network": "Anvil Local EVM",
            "submittedAt": datetime.now(UTC).isoformat(),
        }
        return deepcopy(self.pending_tx)

    def confirm_verification(self) -> dict[str, Any]:
        if not self.pending_tx:
            raise ValueError("Verification transaction has not been submitted")
        self.pending_tx.update(
            {
                "status": "CONFIRMED",
                "blockNumber": 9123456,
                "confirmations": 1,
                "eventValidated": True,
            }
        )
        self.verified = True
        self.claim["status"] = "VERIFIED"
        self.claim["verifiedAt"] = datetime.now(UTC).isoformat()
        self.claim["attestations"].append(
            {
                "id": "att-impactverify-water-12",
                "type": "INDEPENDENT_VERIFIER",
                "issuer": "ImpactVerify",
                "wallet": "0x2222222222222222222222222222222222222222",
                "status": "CONFIRMED",
                "verificationBundleHash": self.bundle,
                "transactionHash": self.pending_tx["transactionHash"],
            }
        )
        return {
            "claim": deepcopy(self.claim),
            "blockchainOperation": deepcopy(self.pending_tx),
            "verification": self.verification(),
        }


store = DemoStore()
