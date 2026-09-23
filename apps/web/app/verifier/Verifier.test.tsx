import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const WALLET = "0x2222222222222222222222222222222222222222";
const refresh = vi.fn();

vi.mock("@/components/SessionProvider", () => ({
  useSession: () => ({
    session: {
      email: "verifier@impactverify.example",
      displayName: "Lars Jensen",
      role: "VERIFIER",
      organization: { id: "org-impactverify", name: "ImpactVerify" },
      // Already proven, so the wallet challenge is skipped in these tests.
      walletAddress: WALLET,
    },
    loading: false,
    signIn: vi.fn(),
    signOut: vi.fn(),
    refresh,
  }),
}));

const writeContract = vi.fn().mockResolvedValue("0x" + "a".repeat(64));
vi.mock("viem", () => ({
  custom: vi.fn(() => ({})),
  defineChain: vi.fn((value) => value),
  createWalletClient: vi.fn(() => ({ writeContract })),
}));

import Verifier from "./page";

const CLAIM = {
  id: "claim-water-12-200",
  programId: "program-clean-water-kenya-2026",
  projectId: "project-water-12",
  statement: "200 households gained access to clean drinking water.",
  status: "VERIFICATION_PENDING",
  payloadHash: "sha256:" + "0".repeat(64),
  verificationBundleHash: "sha256:" + "b".repeat(64),
  policyVersion: "1.0",
  verifiedAt: null,
  evidenceIds: ["ev-inv-8291"],
  attestations: [],
};
const VERIFICATION = {
  claimId: CLAIM.id,
  status: "VERIFICATION_PENDING",
  policyVersion: "1.0",
  requirements: [
    {
      requirement: "EVIDENCE_INTEGRITY",
      status: "PASS",
      reason: "Matches its commitments",
    },
  ],
  evidenceScore: { total: 72, components: [] },
};
const INTENT = {
  operationId: "00000000-0000-0000-0000-000000000001",
  attestationId: "att-1",
  status: "AWAITING_SIGNATURE",
  chainId: 31337,
  contractAddress: "0x9999999999999999999999999999999999999999",
  arguments: {
    attestationId: "0x" + "1".repeat(64),
    subjectId: "0x" + "2".repeat(64),
    attestationType: 1,
    statementHash: "0x" + "3".repeat(64),
    verificationBundleHash: "0x" + "4".repeat(64),
  },
};

/** Route responses by URL so polling can return the same state repeatedly. */
function stubFetch(operationStates: unknown[]) {
  const mock = vi.fn().mockImplementation((url: string) => {
    const path = String(url);
    // Order matters: "/verification-requests/.../intent" also contains "/verification".
    // The workspace reads its queue before it reads a claim, and picks the claim to
    // review from it. Checked before "/claims/" because a query string is not a path.
    const body = path.includes("/claims?")
      ? [
          {
            id: CLAIM.id,
            statement: CLAIM.statement,
            status: CLAIM.status,
            projectId: CLAIM.projectId,
          },
        ]
      : path.endsWith("/intent")
        ? INTENT
        : path.endsWith("/submitted")
          ? { status: "SUBMITTED" }
          : path.endsWith("/verification")
            ? VERIFICATION
            : path.includes("/evidence/")
              ? {
                  id: "ev-inv-8291",
                  reconciliation: { status: "MATCHED", checks: [] },
                }
              : path.includes("/claims/")
                ? CLAIM
                : (operationStates.shift() ?? operationStates.at(-1));
    return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
  });
  vi.stubGlobal("fetch", mock);
  return mock;
}

async function startVerification() {
  render(<Verifier />);
  await screen.findByText(`“${CLAIM.statement}”`);
  fireEvent.click(screen.getByRole("button", { name: "Verify with wallet" }));
}

describe("Verifier wallet workflow", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    writeContract.mockClear();
  });

  function stubWallet() {
    Object.defineProperty(window, "ethereum", {
      configurable: true,
      value: { request: vi.fn().mockResolvedValue([WALLET]) },
    });
  }

  it("shows the bundle hash the signature will commit to, taken from the API", async () => {
    stubWallet();
    stubFetch([]);
    render(<Verifier />);
    expect(
      await screen.findByText(CLAIM.verificationBundleHash),
    ).toBeInTheDocument();
  });

  it("records a verifier rejection without creating a wallet attestation", async () => {
    const fetchMock = stubFetch([]);
    vi.spyOn(window, "prompt").mockReturnValue(
      "The evidence does not establish the outcome.",
    );
    render(<Verifier />);
    await screen.findByText(`“${CLAIM.statement}”`);
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    expect(await screen.findByText("Claim rejected")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining(`/verification-requests/${CLAIM.id}/reject`),
      expect.objectContaining({ method: "POST" }),
    );
    expect(writeContract).not.toHaveBeenCalled();
  });

  it("waits for backend confirmation and policy before showing verified", async () => {
    stubWallet();
    stubFetch([
      {
        status: "CONFIRMED",
        claimStatus: "VERIFIED",
        confirmations: 1,
        error: null,
      },
    ]);
    await startVerification();
    await waitFor(() =>
      expect(screen.getByText("✓ Attestation confirmed")).toBeInTheDocument(),
    );
    expect(writeContract).toHaveBeenCalledOnce();
  });

  it("stays pending while the operation is only SUBMITTED", async () => {
    stubWallet();
    stubFetch([
      { status: "SUBMITTED", claimStatus: "VERIFICATION_PENDING", error: null },
    ]);
    await startVerification();
    await waitFor(() =>
      expect(screen.getByText("Attestation submitted")).toBeInTheDocument(),
    );
    expect(
      screen.queryByText("✓ Attestation confirmed"),
    ).not.toBeInTheDocument();
  });

  it("does not show verified when the chain confirmed but policy has not", async () => {
    stubWallet();
    stubFetch([
      {
        status: "CONFIRMED",
        claimStatus: "VERIFICATION_PENDING",
        confirmations: 1,
        error: null,
      },
    ]);
    await startVerification();
    await waitFor(() => expect(writeContract).toHaveBeenCalledOnce());
    // A confirmed transaction is not a verified claim: policy decides, not the chain.
    expect(
      screen.queryByText("✓ Attestation confirmed"),
    ).not.toBeInTheDocument();
  });

  it("reports a wallet rejection without implying an attestation exists", async () => {
    Object.defineProperty(window, "ethereum", {
      configurable: true,
      value: { request: vi.fn().mockResolvedValue([WALLET]) },
    });
    writeContract.mockRejectedValueOnce({ code: 4001 });
    stubFetch([]);
    await startVerification();
    const failure = await screen.findByText(/rejected in your wallet/i);
    expect(failure).toHaveTextContent(/No attestation was created/i);
    // It must name the actual network, not call a local chain "Ethereum".
    expect(failure.textContent).toMatch(/Anvil local EVM|No chain configured/);
  });
});
