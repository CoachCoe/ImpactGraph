import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/components/SessionProvider", () => ({
  useSession: () => ({
    session: {
      email: "operator@globalwater.example",
      displayName: "Amina Otieno",
      role: "OPERATOR",
      organization: { id: "org-global-water", name: "Global Water Initiative" },
      walletAddress: null,
    },
    loading: false,
    signIn: vi.fn(),
    signOut: vi.fn(),
    refresh: vi.fn(),
  }),
}));

import Operator from "./page";

const extraction = {
  invoiceNumber: "INV-8291",
  vendor: "Aqua Systems Ltd.",
  amountMinor: 420000,
  currency: "USD",
  date: "2026-08-17",
  equipment: "AquaPure X200",
  quantity: 2,
  projectReference: "Water Project #12",
  confidence: 0.97,
};
const analyzed = {
  id: "ev-test",
  workflowStatus: "ANALYZED",
  contentHash: "sha256:abc",
  extraction,
  reconciliation: {
    status: "PARTIAL_MATCH",
    checks: [
      { result: "PASS", message: "Vendor matches approved vendor" },
      { result: "WARNING", message: "One photograph lacks GPS metadata" },
    ],
  },
};

function stubFetch(responses: unknown[]) {
  const mock = vi.fn().mockImplementation(() =>
    Promise.resolve({ ok: true, json: () => Promise.resolve(responses.shift()) }),
  );
  vi.stubGlobal("fetch", mock);
  return mock;
}

describe("Operator evidence workflow", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("uploads, analyzes, reviews, submits, and confirms evidence in order", async () => {
    const fetchMock = stubFetch([
      { ...analyzed, workflowStatus: "UPLOADED" },
      analyzed,
      { ...analyzed, workflowStatus: "REVIEWED" },
      { operationId: "op-1", status: "CREATED" },
      { operationId: "op-1", status: "CONFIRMED", transactionHash: "0xabc" },
    ]);
    render(<Operator />);
    fireEvent.click(screen.getByRole("button", { name: "Load demo INV-8291" }));
    fireEvent.click(screen.getByRole("button", { name: "Upload & analyze" }));
    await screen.findByText("Invoice INV-8291");
    fireEvent.click(screen.getByRole("button", { name: "Accept & register evidence" }));
    await waitFor(() =>
      expect(screen.getByText("✓ Evidence registration confirmed")).toBeInTheDocument(),
    );
    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual(
      expect.arrayContaining([
        expect.stringContaining("/analyze"),
        expect.stringContaining("/review"),
        expect.stringContaining("/register"),
        expect.stringContaining("/blockchain/operations/op-1"),
      ]),
    );
    // Every call must carry the session cookie; identity is no longer a header.
    for (const [, init] of fetchMock.mock.calls) {
      expect((init as RequestInit).credentials).toBe("include");
    }
  });

  it("does not claim success while the operation is only SUBMITTED", async () => {
    stubFetch([
      { ...analyzed, workflowStatus: "UPLOADED" },
      analyzed,
      { ...analyzed, workflowStatus: "REVIEWED" },
      { operationId: "op-2", status: "CREATED" },
      { operationId: "op-2", status: "SUBMITTED", transactionHash: "0xdef" },
      { operationId: "op-2", status: "SUBMITTED", transactionHash: "0xdef" },
    ]);
    render(<Operator />);
    fireEvent.click(screen.getByRole("button", { name: "Load demo INV-8291" }));
    fireEvent.click(screen.getByRole("button", { name: "Upload & analyze" }));
    await screen.findByText("Invoice INV-8291");
    fireEvent.click(screen.getByRole("button", { name: "Accept & register evidence" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Waiting for confirmation…" })).toBeInTheDocument(),
    );
    expect(screen.queryByText("✓ Evidence registration confirmed")).not.toBeInTheDocument();
  });

  it("surfaces an actionable error when registration fails", async () => {
    stubFetch([
      { ...analyzed, workflowStatus: "UPLOADED" },
      analyzed,
      { ...analyzed, workflowStatus: "REVIEWED" },
      { operationId: "op-3", status: "CREATED" },
      { operationId: "op-3", status: "FAILED" },
    ]);
    render(<Operator />);
    fireEvent.click(screen.getByRole("button", { name: "Load demo INV-8291" }));
    fireEvent.click(screen.getByRole("button", { name: "Upload & analyze" }));
    await screen.findByText("Invoice INV-8291");
    fireEvent.click(screen.getByRole("button", { name: "Accept & register evidence" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/remains safe/i);
    expect(screen.queryByText("✓ Evidence registration confirmed")).not.toBeInTheDocument();
  });
});
