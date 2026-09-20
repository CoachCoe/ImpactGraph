import { describe, expect, it, vi } from "vitest";
import {
  WalletNetworkError,
  assertRegistryReachable,
  ensureChain,
  promptAccountSelection,
} from "./wallet";

const REGISTRY = "0x5FbDB2315678afecb367f032d93F642f64180aa3";

function provider(handlers: Record<string, (params?: unknown[]) => unknown>) {
  return {
    request: vi.fn(({ method, params }: { method: string; params?: unknown[] }) => {
      const handler = handlers[method];
      if (!handler) throw new Error(`unexpected call: ${method}`);
      return Promise.resolve(handler(params));
    }),
  };
}

describe("ensureChain", () => {
  it("does nothing when the wallet is already on the right chain", async () => {
    const p = provider({ eth_chainId: () => "0x7a69" });
    await ensureChain(p, 31337, "Anvil local EVM", "http://localhost:8545");
    expect(p.request).toHaveBeenCalledTimes(1);
  });

  it("switches when the wallet is elsewhere", async () => {
    const p = provider({
      eth_chainId: () => "0x1",
      wallet_switchEthereumChain: () => null,
    });
    await ensureChain(p, 31337, "Anvil local EVM", "http://localhost:8545");
    expect(p.request).toHaveBeenCalledWith(
      expect.objectContaining({ method: "wallet_switchEthereumChain" }),
    );
  });

  it("adds the chain when the wallet has never seen it", async () => {
    const p = provider({
      eth_chainId: () => "0x1",
      wallet_switchEthereumChain: () => {
        throw { code: 4902 };
      },
      wallet_addEthereumChain: () => null,
    });
    await ensureChain(p, 31337, "Anvil local EVM", "http://localhost:8545");
    expect(p.request).toHaveBeenCalledWith(
      expect.objectContaining({ method: "wallet_addEthereumChain" }),
    );
  });

  it("explains a refusal instead of signing on the wrong network", async () => {
    const p = provider({
      eth_chainId: () => "0x1",
      wallet_switchEthereumChain: () => {
        throw { code: 4001 };
      },
    });
    await expect(
      ensureChain(p, 31337, "Anvil local EVM", "http://localhost:8545"),
    ).rejects.toBeInstanceOf(WalletNetworkError);
  });
});

describe("assertRegistryReachable", () => {
  it("passes when the registry has code", async () => {
    const p = provider({ eth_getCode: () => "0x6080604052" });
    await assertRegistryReachable(p, REGISTRY, "Anvil local EVM");
  });

  it("catches a wallet on the right chain id but the wrong node", async () => {
    // Two nodes can both claim 31337. Only the code check distinguishes them, and this
    // is the failure a visitor is most likely to hit.
    const p = provider({ eth_getCode: () => "0x" });
    await expect(assertRegistryReachable(p, REGISTRY, "Anvil local EVM")).rejects.toThrow(
      /different node/,
    );
  });
});

describe("promptAccountSelection", () => {
  it("reopens the picker before reading accounts", async () => {
    const order: string[] = [];
    const p = {
      request: vi.fn(({ method }: { method: string }) => {
        order.push(method);
        return Promise.resolve(method === "eth_requestAccounts" ? ["0xabc"] : null);
      }),
    };
    const accounts = await promptAccountSelection(p);
    // The permission request must come first, or the site is handed the same account.
    expect(order).toEqual(["wallet_requestPermissions", "eth_requestAccounts"]);
    expect(accounts).toEqual(["0xabc"]);
  });

  it("still works on a wallet without the permissions RPC", async () => {
    const p = {
      request: vi.fn(({ method }: { method: string }) => {
        if (method === "wallet_requestPermissions") throw { code: -32601 };
        return Promise.resolve(["0xdef"]);
      }),
    };
    expect(await promptAccountSelection(p)).toEqual(["0xdef"]);
  });

  it("says plainly when the visitor picks nothing", async () => {
    const p = {
      request: vi.fn(() => {
        throw { code: 4001 };
      }),
    };
    await expect(promptAccountSelection(p)).rejects.toBeInstanceOf(WalletNetworkError);
  });
});
