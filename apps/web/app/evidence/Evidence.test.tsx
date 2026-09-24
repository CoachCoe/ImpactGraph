import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/network", () => ({
  networkInfo: () => ({ name: "Anvil local EVM", isPublicEthereum: false }),
  explorerLink: () => null,
}));

const readFromApi = vi.fn();
vi.mock("@/lib/api", () => ({
  readFromApi: (path: string) => readFromApi(path),
}));

const { default: EvidencePage } = await import("./[id]/page");

describe("evidence inspector", () => {
  it("names the provider that actually read the document", async () => {
    readFromApi.mockResolvedValue({
      id: "ev-inkling",
      type: "INVOICE",
      projectId: "project-water-12",
      contentHash: `sha256:${"a".repeat(64)}`,
      mimeType: "image/png",
      visibility: "RESTRICTED",
      workflowStatus: "ANALYZED",
      analysisStatus: "COMPLETED",
      integrityStatus: "NOT_CHECKED",
      blockchainStatus: "PENDING",
      extraction: {
        documentType: "invoice",
        invoiceNumber: "INV-8291",
        vendor: "Aqua Systems Ltd.",
        amountMinor: 420000,
        currency: "USD",
        equipment: "AquaPure X200",
        quantity: 2,
        date: "2026-08-17",
      },
      providerMetadata: {
        provider: "tinker",
        model: "thinkingmachines/Inkling-Small",
        processedAt: "2026-09-23T00:00:00Z",
      },
    });

    render(await EvidencePage({ params: Promise.resolve({ id: "ev-inkling" }) }));

    expect(screen.getByText(/Read by tinker using thinkingmachines\/Inkling-Small/)).toBeInTheDocument();
    expect(screen.queryByText(/mock analysis provider/i)).not.toBeInTheDocument();
    expect(screen.getByText(/self-reported per field/i)).toBeInTheDocument();
  });
});
