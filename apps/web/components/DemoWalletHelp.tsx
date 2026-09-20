"use client";

import { useState } from "react";
import { networkInfo } from "@/lib/network";

/**
 * Setup help for the throwaway demo chain.
 *
 * Reaching the demo's climax otherwise means importing an account into a wallet
 * extension, which is where most visitors stop. The key comes from the environment
 * rather than the source tree, so the repository ships no key at all, and the panel
 * cannot render on a public network however it is configured.
 */
export function DemoWalletHelp() {
  const network = networkInfo();
  const key = process.env.NEXT_PUBLIC_DEMO_VERIFIER_KEY;
  const address = process.env.NEXT_PUBLIC_DEMO_VERIFIER_ADDRESS;
  const [shown, setShown] = useState(false);

  // Belt and braces: a published key must never be offered on a real network.
  if (!key || !address || network.isPublicEthereum) return null;

  return (
    <section className="panel">
      <span className="eyebrow">DEMO WALLET</span>
      <h3>Signing on {network.name}</h3>
      <p className="subtle">
        Attesting needs a wallet holding the verifier role on this throwaway chain. Import
        the account below, then press Verify — your wallet will be offered the network
        automatically.
      </p>
      <dl className="proof">
        <dt>Network</dt>
        <dd>
          {network.name} · chain {network.chainId} ·{" "}
          {process.env.NEXT_PUBLIC_RPC_URL ?? "http://127.0.0.1:8545"}
        </dd>
        <dt>Verifier account</dt>
        <dd className="hash">{address}</dd>
      </dl>
      {shown ? (
        <>
          <p className="note">Private key, for importing into a wallet:</p>
          <p className="hash">{key}</p>
        </>
      ) : (
        <button className="secondary" onClick={() => setShown(true)}>
          Show the private key
        </button>
      )}
      <p className="note">
        This is a published development key on a disposable chain. It holds nothing, it is
        not secret, and it must never be used anywhere else.
      </p>
    </section>
  );
}
