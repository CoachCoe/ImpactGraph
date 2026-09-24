import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/network", () => ({
  networkInfo: () => ({
    name: "Anvil local EVM",
    isPublicEthereum: false,
    chainId: 31337,
  }),
}));

const readProof = vi.fn();
vi.mock("@/lib/proof", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/proof")>("@/lib/proof");
  return { ...actual, readProof: (id: string) => readProof(id) };
});

const { default: ProofPage, generateMetadata } = await import("./[id]/page");

const PROOF = {
  claim: {
    id: "claim-water-12-200",
    statement: "200 households gained access to clean drinking water.",
    status: "VERIFIED",
    verifiedAt: "2026-09-01T00:00:00+00:00",
    payloadHash: "sha256:" + "a".repeat(64),
    verificationBundleHash: "sha256:" + "b".repeat(64),
    policyVersion: "1.0",
  },
  operator: {
    name: "Global Water Initiative",
    organisationRef: "org-global-water",
    program: "Clean Water Kenya 2026",
    region: "Kisumu County",
  },
  requirements: [
    {
      requirement: "EVIDENCE_INTEGRITY",
      status: "PASS",
      reason: "Re-hashing matches.",
    },
    {
      requirement: "INDEPENDENT_VERIFICATION",
      status: "FAIL",
      reason: "Nobody has signed.",
    },
  ],
  attestations: [
    {
      id: "a1",
      type: "OPERATOR",
      issuer: "Global Water Initiative",
      wallet: null,
      status: "RECORDED",
      onchain: false,
      transactionHash: null,
    },
  ],
  onchain: null,
  publishedAt: "2026-09-02T00:00:00+00:00",
  proves: ["The documents were committed to a public blockchain."],
  doesNotProve: ["That the work described actually helped anyone."],
};

const ok = (data: unknown) => ({ state: "ok" as const, data });

describe("public proof page", () => {
  it("leads with the organisation that did the work, not the platform", async () => {
    readProof.mockResolvedValue(ok(PROOF));
    render(
      await ProofPage({ params: Promise.resolve({ id: PROOF.claim.id }) }),
    );
    const heading = screen.getByRole("heading", { level: 1 });
    expect(heading).toHaveTextContent("200 households gained access");
    // The byline beside the claim, not the mention further down in the attestations.
    expect(heading.parentElement).toHaveTextContent(
      "Global Water Initiative · Kisumu County",
    );
  });

  it("says what the verification does not prove, not only what it does", async () => {
    readProof.mockResolvedValue(ok(PROOF));
    render(
      await ProofPage({ params: Promise.resolve({ id: PROOF.claim.id }) }),
    );
    const limits = screen
      .getByRole("heading", { name: "What it does not" })
      .closest("div");
    expect(
      within(limits!).getByText(/actually helped anyone/),
    ).toBeInTheDocument();
  });

  it("shows a failing requirement rather than only the passing ones", async () => {
    readProof.mockResolvedValue(ok(PROOF));
    render(
      await ProofPage({ params: Promise.resolve({ id: PROOF.claim.id }) }),
    );
    expect(screen.getByText(/Nobody has signed/)).toBeInTheDocument();
  });

  it("offers an embed that is one line and points at the live badge", async () => {
    const previousSiteUrl = process.env.NEXT_PUBLIC_SITE_URL;
    process.env.NEXT_PUBLIC_SITE_URL = "https://proofs.example/";
    readProof.mockResolvedValue(ok(PROOF));
    try {
      render(
        await ProofPage({ params: Promise.resolve({ id: PROOF.claim.id }) }),
      );
      const snippet = screen.getByText(/badge\.svg/);
      expect(snippet.textContent).toContain(
        `https://proofs.example/api/claims/${PROOF.claim.id}/badge.svg`,
      );
      expect(snippet.textContent).toContain(
        `https://proofs.example/proof/${PROOF.claim.id}`,
      );
      expect(snippet.textContent!.split("\n")).toHaveLength(1);
    } finally {
      if (previousSiteUrl === undefined) delete process.env.NEXT_PUBLIC_SITE_URL;
      else process.env.NEXT_PUBLIC_SITE_URL = previousSiteUrl;
    }
  });

  it("tells a reader the page shows current status, not the status when it was shared", async () => {
    readProof.mockResolvedValue(ok(PROOF));
    render(
      await ProofPage({ params: Promise.resolve({ id: PROOF.claim.id }) }),
    );
    expect(screen.getByText(/current status/)).toBeInTheDocument();
  });

  it("does not report an unpublished claim as a missing page", async () => {
    readProof.mockResolvedValue({ state: "missing" });
    render(
      await ProofPage({ params: Promise.resolve({ id: "claim-unpublished" }) }),
    );
    expect(screen.getByText(/chooses to\s+publish one/)).toBeInTheDocument();
  });

  it("puts the status in the social card, so a cached card cannot claim more", async () => {
    readProof.mockResolvedValue(
      ok({ ...PROOF, claim: { ...PROOF.claim, status: "CHALLENGED" } }),
    );
    const metadata = await generateMetadata({
      params: Promise.resolve({ id: PROOF.claim.id }),
    });
    expect(String(metadata.title)).toContain("Verification withdrawn");
    expect(String(metadata.openGraph?.title)).toContain(
      "Verification withdrawn",
    );
    expect(String(metadata.description)).toContain("Current status: Verification withdrawn");
    expect(String(metadata.description)).not.toContain("Verified against evidence");
  });

  it("keeps an unpublished claim out of search results", async () => {
    readProof.mockResolvedValue({ state: "missing" });
    const metadata = await generateMetadata({
      params: Promise.resolve({ id: "nope" }),
    });
    expect(metadata.robots).toMatchObject({ index: false });
  });
});

describe("a printed proof", () => {
  it("carries the hashes and its own address, so paper stays checkable", async () => {
    readProof.mockResolvedValue(ok(PROOF));
    render(
      await ProofPage({ params: Promise.resolve({ id: PROOF.claim.id }) }),
    );
    const printed = screen
      .getByRole("heading", { name: "Checking this from a printed copy" })
      .closest("section");
    expect(
      within(printed!).getByText(`/proof/${PROOF.claim.id}`),
    ).toBeInTheDocument();
    expect(
      within(printed!).getByText(PROOF.claim.payloadHash),
    ).toBeInTheDocument();
    expect(within(printed!).getByText(/shows live status/)).toBeInTheDocument();
  });
});
