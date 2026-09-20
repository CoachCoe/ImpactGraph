/**
 * Network-aware proof language.
 *
 * "Verified on Ethereum" is only truthful on a public Ethereum network. Anvil is a local
 * EVM and must say so, and a run with no registry configured has committed nothing at all.
 */
const CHAIN_NAMES: Record<string, string> = {
  "1": "Ethereum",
  "11155111": "Ethereum Sepolia",
  "31337": "Anvil local EVM",
};

export type NetworkInfo = {
  chainId: string | null;
  name: string;
  isPublicEthereum: boolean;
  registryAddress: string | null;
  explorerBase: string | null;
};

export function networkInfo(): NetworkInfo {
  const chainId = process.env.NEXT_PUBLIC_CHAIN_ID ?? null;
  const explorer = process.env.NEXT_PUBLIC_BLOCK_EXPLORER_URL || null;
  const registry = process.env.NEXT_PUBLIC_IMPACT_REGISTRY_ADDRESS || null;
  const configured = chainId ? (CHAIN_NAMES[chainId] ?? `EVM chain ${chainId}`) : null;
  return {
    chainId,
    name: process.env.NEXT_PUBLIC_CHAIN_NAME || configured || "No chain configured",
    isPublicEthereum: chainId === "1" || chainId === "11155111",
    registryAddress: registry,
    explorerBase: explorer ? explorer.replace(/\/$/, "") : null,
  };
}

export function explorerLink(base: string | null, kind: "tx" | "address", value: string) {
  return base ? `${base}/${kind}/${value}` : null;
}
