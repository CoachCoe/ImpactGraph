export type Money = { amountMinor: number; currency: string };

export type Program = {
  id: string;
  name: string;
  operator: string;
  region: string;
  status: string;
  funding: Money;
  deployed: Money;
  filtrationSystems: number;
  peopleServed: number;
  verificationPercent: number;
  featuredClaimId: string;
};

export type Attestation = {
  id: string;
  type: string;
  issuer: string;
  wallet: string | null;
  status: string;
  /** True only when a transaction hash backs it. An operator attestation has none. */
  onchain?: boolean;
  transactionHash: string | null;
  verificationBundleHash: string | null;
};

export type Claim = {
  id: string;
  programId: string;
  projectId: string;
  statement: string;
  status: string;
  payloadHash: string;
  verificationBundleHash: string;
  policyVersion: string;
  verifiedAt: string | null;
  evidenceIds: string[];
  attestations: Attestation[];
};

export type ProvenanceNode = {
  id: string;
  type: string;
  title: string;
  detail: string;
  method?: string;
  source?: string;
  confidencePercent?: number | null;
};
export type ProvenanceEdge = {
  source: string;
  relationship: string;
  target: string;
  confirmedOnchain: boolean;
};
export type Provenance = { nodes: ProvenanceNode[]; edges: ProvenanceEdge[] };

export type Requirement = { requirement: string; status: string; reason: string };
export type ScoreComponent = { component: string; score: number; maximum: number };
export type Verification = {
  claimId: string;
  status: string;
  policyVersion: string;
  requirements: Requirement[];
  evidenceScore: { total: number; components: ScoreComponent[] };
};

export type ReconciliationCheck = { check: string; result: string; message: string };
export type Evidence = {
  id: string;
  type: string;
  projectId: string;
  contentHash: string;
  mimeType: string;
  visibility: string;
  workflowStatus: string;
  analysisStatus: string;
  integrityStatus: string;
  blockchainStatus: string;
  extraction?: Record<string, string | number>;
  reconciliation?: { status: string; checks: ReconciliationCheck[] };
  blockchainReference?: { transactionHash: string; blockNumber: number };
};

export type IntegrityResult = {
  evidenceId: string;
  status: "MATCH" | "MISMATCH";
  expected: string;
  current: string;
  explanation: string;
  /** Bytes actually read back and hashed, reported by the API. */
  byteCount?: number;
  /** When the chain says the commitment was mined. Absent if it cannot be read. */
  registeredAt?: string | null;
};

export type FinancialTransaction = {
  id: string;
  programId: string;
  allocationId: string | null;
  payerRef: string;
  payeeRef: string;
  payee: string;
  amount: Money;
  occurredOn: string;
  memo: string;
  provider: string;
  sourceRef: string;
  matchStatus: "MATCHED" | "PARTIAL_MATCH" | "UNMATCHED" | "CONFLICT";
};

export type FinancialSummary = {
  programId: string;
  received: Money;
  committed: Money;
  spent: Money;
  uncommitted: Money;
  unspent: Money;
  matchCounts: Record<string, number>;
  funding: {
    id: string;
    funder: string;
    amount: Money;
    receivedOn: string;
    sourceRef: string;
  }[];
  allocations: {
    id: string;
    projectId: string;
    purpose: string;
    amount: Money;
    spent: Money;
    remaining: Money;
  }[];
  transactions: FinancialTransaction[];
};

export type AttributedClaim = { id: string; statement: string; status: string };
export type AttributedOutcome = {
  id: string;
  metric: string;
  value: number;
  unit: string;
  region: string;
  claims: AttributedClaim[];
};
export type AttributedDelivery = {
  id: string;
  item: string;
  quantity: number;
  deliveredOn: string;
  outcomes: AttributedOutcome[];
};
export type AttributedTransaction = {
  id: string;
  payee: string;
  amount: Money;
  occurredOn: string;
  matchStatus: string;
  deliveries: AttributedDelivery[];
};
export type AttributedAllocation = {
  id: string;
  purpose: string;
  projectId: string;
  amount: Money;
  spent: Money;
  unspent: Money;
  overspent: Money;
  transactions: AttributedTransaction[];
};
export type FundingAttribution = {
  fundingId: string;
  funder: string;
  programId: string;
  receivedOn: string;
  received: Money;
  committed: Money;
  spent: Money;
  uncommitted: Money;
  overcommitted: Money;
  allocations: AttributedAllocation[];
  method: { basis: string; explanation: string };
};
