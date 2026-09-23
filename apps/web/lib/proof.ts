import { readFromApiResult, type ApiRead } from "@/lib/api";

export type Proof = {
  claim: {
    id: string;
    statement: string;
    status: string;
    verifiedAt: string | null;
    payloadHash: string;
    verificationBundleHash: string | null;
    policyVersion: string;
  };
  operator: {
    name: string;
    organisationRef: string;
    program: string;
    region: string;
  };
  requirements: { requirement: string; status: string; reason: string }[];
  attestations: {
    id: string;
    type: string;
    issuer: string;
    wallet: string | null;
    status: string;
    onchain: boolean;
    transactionHash: string | null;
  }[];
  onchain: { transactionHash: string | null; issuer: string } | null;
  publishedAt: string;
  proves: string[];
  doesNotProve: string[];
};

export function readProof(claimId: string): Promise<ApiRead<Proof>> {
  return readFromApiResult<Proof>(`/claims/${claimId}/proof`);
}

/** The requirement name as a sentence, for a reader who has never seen the policy. */
export function requirementLabel(requirement: string): string {
  return requirement.replace(/_/g, " ").toLowerCase();
}
