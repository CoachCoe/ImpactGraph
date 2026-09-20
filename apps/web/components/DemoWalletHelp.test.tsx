import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DemoWalletHelp } from "./DemoWalletHelp";

const ADDRESS = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC";
const KEY = "0x" + "5d".repeat(32);

function configure(chainId: string, withKey = true) {
  vi.stubEnv("NEXT_PUBLIC_CHAIN_ID", chainId);
  vi.stubEnv("NEXT_PUBLIC_DEMO_VERIFIER_KEY", withKey ? KEY : "");
  vi.stubEnv("NEXT_PUBLIC_DEMO_VERIFIER_ADDRESS", withKey ? ADDRESS : "");
}

describe("DemoWalletHelp", () => {
  afterEach(() => vi.unstubAllEnvs());

  it("offers the throwaway account on a local chain", () => {
    configure("31337");
    render(<DemoWalletHelp />);
    expect(screen.getByText(ADDRESS)).toBeInTheDocument();
    // The key is behind a deliberate action, not just printed.
    expect(screen.queryByText(KEY)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Show the private key/ })).toBeInTheDocument();
  });

  it("renders nothing on a public network, whatever is configured", () => {
    for (const chainId of ["1", "11155111"]) {
      configure(chainId);
      const { container } = render(<DemoWalletHelp />);
      expect(container).toBeEmptyDOMElement();
    }
  });

  it("renders nothing when no demo key is configured", () => {
    configure("31337", false);
    const { container } = render(<DemoWalletHelp />);
    expect(container).toBeEmptyDOMElement();
  });
});
