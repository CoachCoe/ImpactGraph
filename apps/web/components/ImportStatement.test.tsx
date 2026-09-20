import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));

const session = { current: null as null | { role: string } };
vi.mock("@/components/SessionProvider", () => ({
  useSession: () => ({ session: session.current, loading: false }),
}));

import { ImportStatement } from "./ImportStatement";

describe("ImportStatement", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("is not offered to a donor or a verifier", () => {
    session.current = null;
    const { container } = render(<ImportStatement programId="p1" />);
    expect(container).toBeEmptyDOMElement();

    session.current = { role: "VERIFIER" };
    const { container: verifier } = render(<ImportStatement programId="p1" />);
    expect(verifier).toBeEmptyDOMElement();
  });

  it("reports what was recorded, already observed, and rejected", async () => {
    session.current = { role: "OPERATOR" };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: () =>
          Promise.resolve({
            provider: "mock-bank",
            imported: ["ftx-1"],
            skipped: ["mock-bank-9182", "mock-bank-9183"],
            rejected: [{ sourceRef: "mock-bank-9999", reason: "Exceeds the remaining allocation" }],
          }),
      }),
    );
    render(<ImportStatement programId="p1" />);
    fireEvent.click(screen.getByRole("button", { name: "Import statement" }));
    await waitFor(() => expect(screen.getByRole("status")).toBeInTheDocument());
    // A rejected payment must be surfaced, not silently dropped.
    expect(screen.getByText(/Exceeds the remaining allocation/)).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("1");
  });

  it("surfaces a failed import instead of appearing to succeed", async () => {
    session.current = { role: "OPERATOR" };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        json: () => Promise.resolve({ detail: { message: "Idempotency-Key is required" } }),
      }),
    );
    render(<ImportStatement programId="p1" />);
    fireEvent.click(screen.getByRole("button", { name: "Import statement" }));
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
