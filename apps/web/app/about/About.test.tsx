import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/network", () => ({
  networkInfo: () => ({ name: "Anvil local EVM", isPublicEthereum: false, chainId: 31337 }),
}));

// The page links its explanation to a record that exists rather than to a named one, so
// it reads the claim and its evidence rather than carrying their identifiers in source.
vi.mock("@/lib/api", () => ({
  readFromApi: (path: string) =>
    Promise.resolve(
      path === "/claims"
        ? [{ id: "claim-water-12-200" }]
        : { id: "claim-water-12-200", evidenceIds: ["ev-inv-8291"] },
    ),
}));

import About from "./page";

describe("How it works", () => {
  it("names every stage of the chain the product exists to trace", async () => {
    render(await About());
    for (const stage of [
      "Funding",
      "Allocation",
      "Transaction",
      "Delivery",
      "Evidence",
      "Verification",
      "Outcome",
    ]) {
      expect(screen.getByText(stage)).toBeInTheDocument();
    }
  });

  it("answers each donor question with a link to the record, not with prose", async () => {
    render(await About());
    const questions = [
      "What happened?",
      "Where did the funding originate?",
      "Where did the money go?",
      "What evidence supports it?",
      "Who verified the result?",
      "How reliable is the evidence?",
    ];
    for (const question of questions) {
      expect(screen.getByRole("link", { name: question })).toHaveAttribute("href");
    }
  });

  it("states what is fictional as plainly as what is real", async () => {
    // A page whose argument is honesty cannot be coy about its own limits.
    render(await About());
    const fictional = screen.getByRole("heading", { name: "Fictional" }).closest("div");
    expect(fictional).not.toBeNull();
    expect(within(fictional!).getByText(/never moves it/)).toBeInTheDocument();
    expect(screen.getByText(/Nothing here is audited/)).toBeInTheDocument();
  });

  it("does not claim the local chain is Ethereum verification", async () => {
    render(await About());
    expect(screen.getByText(/a local chain/)).toBeInTheDocument();
    expect(screen.queryByText(/verified on Ethereum/i)).not.toBeInTheDocument();
  });

  it("lists the policy requirements a claim must pass", async () => {
    render(await About());
    expect(screen.getByText("The verifier is not the operator")).toBeInTheDocument();
    expect(screen.getByText(/not from a request header/)).toBeInTheDocument();
  });
});
