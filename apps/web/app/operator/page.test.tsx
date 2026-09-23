import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import OperatorPage from "./page";

vi.mock("@/components/RequireRole", () => ({
  RequireRole: ({ children }: { children: (session: unknown) => React.ReactNode }) =>
    children({ role: "OPERATOR" }),
}));

const api = vi.fn();
vi.mock("@/lib/api", () => ({ api: (...args: unknown[]) => api(...args) }));

// What the model read, and what the API says a person still has to confirm. The vendor is
// wrong on purpose: a confident misread is the failure this screen exists to catch.
const PROGRAMS = [
  {
    id: "program-clean-water-kenya-2026",
    name: "Clean Water Kenya",
    region: "Kisumu County",
    operator: "Global Water Initiative",
    chainStatus: "CONFIRMED",
    projects: [{ id: "project-water-12", name: "Water Project #12" }],
  },
];

const ANALYZED = {
  id: "ev-1",
  workflowStatus: "ANALYZED",
  contentHash: "sha256:abc",
  reviewRequired: ["amountMinor", "currency", "invoiceNumber"],
  providerMetadata: { provider: "tinker", model: "thinkingmachines/Inkling-Small" },
  extraction: {
    documentType: "invoice",
    invoiceNumber: "INV-6291",
    amountMinor: 420000,
    currency: "USD",
    vendor: "Aqua Systems Ltd.",
    date: "2026-08-17",
    equipment: "AquaPure X200",
    quantity: 2,
    projectReference: "Water Project #12",
    selfReportedConfidence: { invoiceNumber: 0.99, vendor: 0.98 },
  },
  reconciliation: { status: "MATCHED", checks: [] },
};

async function analyze() {
  render(<OperatorPage />);
  fireEvent.click(screen.getByRole("button", { name: "Load demo INV-8291" }));
  // The workspace has to know which project the document is filed under before it can
  // upload one, so the control stays disabled until that has arrived.
  const upload = screen.getByRole("button", { name: "Upload & analyze" });
  await waitFor(() => expect(upload).toBeEnabled());
  fireEvent.click(upload);
  await screen.findByDisplayValue("INV-6291");
}

describe("operator review", () => {
  beforeEach(() => {
    api.mockReset();
    api.mockImplementation((path: string) =>
      Promise.resolve(
        path.includes("/operator/programs")
          ? PROGRAMS
          : path.endsWith("/analyze")
            ? ANALYZED
            : { operationId: "op-1", status: "CONFIRMED" },
      ),
    );
  });

  it("will not register until a person has confirmed every flagged field", async () => {
    await analyze();
    const register = screen.getByRole("button", { name: /Confirm 3 fields to continue/ });
    expect(register).toBeDisabled();

    fireEvent.click(screen.getByRole("checkbox", { name: "Confirm Invoice number" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Confirm Currency" }));
    expect(screen.getByRole("button", { name: /Confirm 1 field to continue/ })).toBeDisabled();

    fireEvent.click(screen.getByRole("checkbox", { name: "Confirm Amount (minor units)" }));
    expect(screen.getByRole("button", { name: "Accept & register evidence" })).toBeEnabled();
  });

  it("registers the value the operator corrected, not the one the model read", async () => {
    await analyze();
    fireEvent.change(screen.getByLabelText("Invoice number"), { target: { value: "INV-8291" } });
    for (const field of ["Invoice number", "Amount (minor units)", "Currency"]) {
      fireEvent.click(screen.getByRole("checkbox", { name: `Confirm ${field}` }));
    }
    fireEvent.click(screen.getByRole("button", { name: "Accept & register evidence" }));

    await waitFor(() => {
      const review = api.mock.calls.find(([path]) => String(path).endsWith("/review"));
      expect(review).toBeDefined();
      const sent = JSON.parse(review![1].body);
      expect(sent.extraction.invoiceNumber).toBe("INV-8291");
      // The API refuses a review that does not cover every flagged field, so what the
      // operator ticked has to travel with the extraction rather than staying on screen.
      expect(sent.confirmed.sort()).toEqual(["amountMinor", "currency", "invoiceNumber"]);
    });
  });

  it("leaves a field nobody was asked to check alone", async () => {
    await analyze();
    expect(screen.getByLabelText("Vendor")).toHaveAttribute("readonly");
    expect(screen.queryByRole("checkbox", { name: "Confirm Vendor" })).not.toBeInTheDocument();
  });
});
