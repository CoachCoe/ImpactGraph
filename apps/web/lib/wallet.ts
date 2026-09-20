/**
 * Getting a browser wallet onto the right chain, and refusing to sign if it is not.
 *
 * The common failure in a wallet demo is not a rejected signature, it is a wallet quietly
 * pointed somewhere else. A visitor with anything already on the local RPC port, or a
 * wallet left on mainnet, would otherwise send a transaction that reverts for reasons the
 * UI cannot explain.
 */

export type EthereumProvider = {
  request(args: { method: string; params?: unknown[] }): Promise<unknown>;
};

export class WalletNetworkError extends Error {}

const USER_REJECTED = 4001;
const CHAIN_NOT_ADDED = 4902;

function errorCode(cause: unknown): number | undefined {
  return typeof cause === "object" && cause !== null
    ? (cause as { code?: number }).code
    : undefined;
}

/** Move the wallet to `chainId`, adding the network if it does not know it yet. */
export async function ensureChain(
  provider: EthereumProvider,
  chainId: number,
  chainName: string,
  rpcUrl: string,
): Promise<void> {
  const hex = `0x${chainId.toString(16)}`;
  const current = (await provider.request({ method: "eth_chainId" })) as string;
  if (current?.toLowerCase() === hex) return;

  try {
    await provider.request({ method: "wallet_switchEthereumChain", params: [{ chainId: hex }] });
    return;
  } catch (cause) {
    if (errorCode(cause) === USER_REJECTED) {
      throw new WalletNetworkError(
        `Your wallet stayed on another network. This attestation has to be signed on ${chainName}.`,
      );
    }
    if (errorCode(cause) !== CHAIN_NOT_ADDED) throw cause;
  }

  // The wallet has never seen this chain. Offer to add it, then switch.
  try {
    await provider.request({
      method: "wallet_addEthereumChain",
      params: [
        {
          chainId: hex,
          chainName,
          nativeCurrency: { name: "Ether", symbol: "ETH", decimals: 18 },
          rpcUrls: [rpcUrl],
        },
      ],
    });
  } catch (cause) {
    if (errorCode(cause) === USER_REJECTED) {
      throw new WalletNetworkError(
        `${chainName} was not added to your wallet, so there is nothing to sign on.`,
      );
    }
    throw cause;
  }
}

/**
 * Confirm the registry exists on whatever chain the wallet is actually talking to.
 *
 * A matching chain id is not enough: two different nodes can both claim 31337. Asking the
 * wallet's own provider for the contract code is the only check that covers it, and it
 * turns a mystifying revert into a sentence a visitor can act on.
 */
export async function assertRegistryReachable(
  provider: EthereumProvider,
  registryAddress: string,
  chainName: string,
): Promise<void> {
  const code = (await provider.request({
    method: "eth_getCode",
    params: [registryAddress, "latest"],
  })) as string | null;
  if (!code || code === "0x" || code === "0x0") {
    throw new WalletNetworkError(
      `No ImpactRegistry found at ${registryAddress} on the network your wallet is using. ` +
        `Your wallet reports ${chainName}, but it is reaching a different node — check that ` +
        `nothing else is bound to the demo's RPC port.`,
    );
  }
}


/**
 * Re-open the wallet's account picker.
 *
 * Switching the selected account inside the extension does not change which accounts a
 * site may see: that is a per-site permission. A visitor who switches in the UI and
 * returns is handed the same account as before, proves the same address, and hits the
 * same authorisation failure with no indication that the switch did not take effect.
 * `wallet_requestPermissions` forces the picker so the choice actually reaches the site.
 */
export async function promptAccountSelection(provider: EthereumProvider): Promise<string[]> {
  try {
    await provider.request({
      method: "wallet_requestPermissions",
      params: [{ eth_accounts: {} }],
    });
  } catch (cause) {
    if (errorCode(cause) === USER_REJECTED) {
      throw new WalletNetworkError("No account was chosen, so nothing changed.");
    }
    // Wallets that do not implement the permissions RPC fall through to the plain
    // request below, which is no worse than before.
  }
  return (await provider.request({ method: "eth_requestAccounts" })) as string[];
}
