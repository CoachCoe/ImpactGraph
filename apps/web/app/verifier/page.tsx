"use client";

import { useEffect, useState } from "react";
import { createWalletClient, custom, defineChain, type Address, type Hex } from "viem";
import { DemoWalletHelp } from "@/components/DemoWalletHelp";
import { RequireRole } from "@/components/RequireRole";
import { Status } from "@/components/Status";
import { useSession } from "@/components/SessionProvider";
import { ApiError, api } from "@/lib/api";
import { networkInfo } from "@/lib/network";
import {
  WalletNetworkError,
  assertRegistryReachable,
  ensureChain,
  promptAccountSelection,
} from "@/lib/wallet";
import type { Claim, Evidence, Verification } from "@/lib/types";

type TxState = "idle" | "proving" | "connecting" | "awaiting" | "submitted" | "confirmed" | "rejected" | "failed";
type EthereumProvider = { request(args: { method: string; params?: unknown[] }): Promise<unknown> };

declare global {
  interface Window { ethereum?: EthereumProvider }
}

type Intent = {
  operationId: string;
  attestationId: string;
  status: string;
  chainId: number;
  contractAddress: Address;
  arguments: {
    attestationId: Hex;
    subjectId: Hex;
    attestationType: number;
    statementHash: Hex;
    verificationBundleHash: Hex;
  };
};

type Operation = {
  status: string;
  transactionHash: Hex | null;
  confirmations: number;
  error: string | null;
  claimStatus: string | null;
};

const attestationAbi = [{
  type: "function",
  name: "createAttestation",
  stateMutability: "nonpayable",
  inputs: [
    { name: "id", type: "bytes32" },
    { name: "subjectId", type: "bytes32" },
    { name: "attestationType", type: "uint8" },
    { name: "statementHash", type: "bytes32" },
    { name: "verificationBundleHash", type: "bytes32" },
  ],
  outputs: [],
}] as const;

type QueueItem = { id: string; statement: string; status: string; projectId: string };
const sleep = (milliseconds: number) => new Promise((resolve) => setTimeout(resolve, milliseconds));

export default function VerifierPage() {
  return <RequireRole role="VERIFIER">{() => <Verifier />}</RequireRole>;
}

function Verifier() {
  const { session, refresh } = useSession();
  // Which claim is under review, and what else is waiting. Both used to be one constant,
  // so a verifier could only ever review the seeded claim of the seeded program.
  const [queue, setQueue] = useState<QueueItem[] | null>(null);
  const [claimId, setClaimId] = useState<string | null>(null);
  const [claim, setClaim] = useState<Claim | null>(null);
  const [verification, setVerification] = useState<Verification | null>(null);
  const [evidence, setEvidence] = useState<Evidence | null>(null);
  const [state, setState] = useState<TxState>("idle");
  const [transactionHash, setTransactionHash] = useState<Hex | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Set when the connected account is the wrong one, so the account picker can be offered.
  const [wrongAccount, setWrongAccount] = useState(false);

  // The queue and the bundle under review come from the API, so the verifier reviews the
  // same state they are about to sign rather than a separate copy that could disagree.
  useEffect(() => {
    void (async () => {
      const waiting =
        (await api<QueueItem[]>("/claims?status=VERIFICATION_PENDING").catch(() => null)) ?? [];
      setQueue(waiting);
      const requested = new URLSearchParams(window.location.search).get("claim");
      setClaimId(requested ?? waiting[0]?.id ?? null);
    })();
  }, []);

  useEffect(() => {
    if (!claimId) return;
    void (async () => {
      const next = await api<Claim>(`/claims/${claimId}`).catch(() => null);
      setClaim(next);
      if (!next) return;
      setVerification(await api<Verification>(`/claims/${claimId}/verification`).catch(() => null));
      const first = next.evidenceIds[0];
      if (first) setEvidence(await api<Evidence>(`/evidence/${first}`).catch(() => null));
    })();
  }, [claimId]);

  /** Prove control of the wallet before it can be used to attest. */
  const proveWallet = async (account: Address) => {
    const challenge = await api<{ nonce: string; message: string }>("/auth/wallet/challenge", {
      method: "POST",
    });
    const signature = (await window.ethereum!.request({
      method: "personal_sign",
      params: [challenge.message, account],
    })) as string;
    await api("/auth/wallet/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ nonce: challenge.nonce, signature }),
    });
    await refresh();
  };

  const network = networkInfo();

  /** Force the wallet's account picker, then try again with whatever was chosen. */
  const chooseAnotherAccount = async () => {
    if (!window.ethereum) return;
    setError(null);
    try {
      await promptAccountSelection(window.ethereum);
      setWrongAccount(false);
      setState("idle");
      await verify();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "No account was chosen.");
    }
  };

  const verify = async () => {
    setError(null);
    setWrongAccount(false);
    if (!window.ethereum) {
      setState("failed");
      setError("No compatible wallet was found. Install MetaMask or another EVM wallet.");
      return;
    }
    try {
      setState("connecting");
      const accounts = await window.ethereum.request({ method: "eth_requestAccounts" }) as Address[];
      const account = accounts[0];
      if (!account) throw new Error("The wallet did not return an account.");

      // Get the wallet onto the demo chain, and refuse to sign if it is reaching a
      // different node than the one holding the registry. A silently wrong network is
      // the most likely way this goes wrong for a visitor.
      if (network.chainId) {
        await ensureChain(
          window.ethereum,
          Number(network.chainId),
          network.name,
          process.env.NEXT_PUBLIC_RPC_URL ?? "http://127.0.0.1:8545",
        );
      }
      if (network.registryAddress) {
        await assertRegistryReachable(window.ethereum, network.registryAddress, network.name);
      }

      if (session?.walletAddress?.toLowerCase() !== account.toLowerCase()) {
        setState("proving");
        await proveWallet(account);
      }
      setState("connecting");

      const intent = await api<Intent>(`/verification-requests/${claimId}/intent`, {
        method: "POST",
        headers: { "Idempotency-Key": crypto.randomUUID() },
      });
      const chain = defineChain({
        id: intent.chainId,
        name: intent.chainId === 11155111 ? "Ethereum Sepolia" : "Anvil Local EVM",
        nativeCurrency: { name: "Ether", symbol: "ETH", decimals: 18 },
        rpcUrls: { default: { http: [process.env.NEXT_PUBLIC_RPC_URL ?? "http://127.0.0.1:8545"] } },
        blockExplorers: process.env.NEXT_PUBLIC_BLOCK_EXPLORER_URL ? { default: { name: "Explorer", url: process.env.NEXT_PUBLIC_BLOCK_EXPLORER_URL } } : undefined,
      });
      const wallet = createWalletClient({ account, chain, transport: custom(window.ethereum) });
      setState("awaiting");
      const hash = await wallet.writeContract({
        address: intent.contractAddress,
        abi: attestationAbi,
        functionName: "createAttestation",
        args: [intent.arguments.attestationId, intent.arguments.subjectId, intent.arguments.attestationType, intent.arguments.statementHash, intent.arguments.verificationBundleHash],
      });
      setTransactionHash(hash);
      setState("submitted");
      await api(`/blockchain/operations/${intent.operationId}/submitted`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ transactionHash: hash, wallet: account }),
      });
      for (let attempt = 0; attempt < 120; attempt += 1) {
        const operation = await api<Operation>(`/blockchain/operations/${intent.operationId}`);
        if (operation.status === "CONFIRMED" && operation.claimStatus === "VERIFIED") {
          setState("confirmed");
          return;
        }
        if (operation.status === "FAILED") throw new Error(operation.error ?? `The ${network.name} transaction failed validation.`);
        await sleep(1000);
      }
      throw new Error("Confirmation is taking longer than expected. The transaction remains submitted and can be checked again.");
    } catch (reason) {
      const candidate = reason as {
        code?: number | string;
        shortMessage?: string;
        message?: string;
      };
      setState("failed");
      if (reason instanceof WalletNetworkError) {
        setError(reason.message);
        return;
      }
      if (
        reason instanceof ApiError &&
        (reason.code === "WALLET_IS_OPERATOR" || reason.code === "WALLET_LACKS_VERIFIER_ROLE")
      ) {
        setWrongAccount(true);
        setError(reason.message);
        return;
      }
      setError(candidate.code === 4001 ? `The ${network.name} transaction was rejected in your wallet. No attestation was created.` : candidate.shortMessage ?? candidate.message ?? "Verification failed.");
    }
  };

  const reject = async () => {
    const reason = window.prompt("Why is this claim being rejected?")?.trim();
    if (!reason) return;
    setError(null);
    try {
      await api(`/verification-requests/${claimId}/reject`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Idempotency-Key": crypto.randomUUID() },
        body: JSON.stringify({ reason }),
      });
      setState("rejected");
    } catch (candidate) {
      setState("failed");
      setError(candidate instanceof Error ? candidate.message : "The rejection could not be recorded.");
    }
  };

  return <div className="workspace">
    <div className="workspaceHead"><div><span className="eyebrow">INDEPENDENT VERIFIER</span><h1>Verification review</h1><p>{session?.organization.name} · {queue === null ? "loading…" : queue.length === 1 ? "1 pending request" : `${queue.length} pending requests`}</p></div><Status kind={state === "confirmed" ? "verified" : state === "failed" ? "failed" : "pending"}>{state === "confirmed" ? `Confirmed on ${network.name}` : state === "failed" ? "Action required" : "Review required"}</Status></div>
    {queue !== null && queue.length === 0 && !claim ? <section className="panel"><h2>Nothing is waiting for you</h2><p className="subtle">A claim appears here once its operator has filed the evidence and asked for independent verification.</p></section> : null}
    {queue !== null && queue.length > 1 ? <section className="panel queuePanel"><span className="eyebrow">PENDING REQUESTS</span><ul className="queue">{queue.map((item) => <li key={item.id}><button className={item.id === claimId ? "queueItem current" : "queueItem"} aria-current={item.id === claimId} onClick={() => setClaimId(item.id)}><b>{item.statement}</b><small>{item.projectId}</small></button></li>)}</ul></section> : null}
    <div className="reviewGrid"><section className="panel"><span className="eyebrow">{claim?.projectId ?? "CLAIM"}</span><h2>“{claim?.statement ?? "Loading…"}”</h2>
      <p className="subtle">Verification bundle <span className="hash">{claim?.verificationBundleHash ?? "…"}</span></p>
      <p className="note">This is the bundle hash your signature will commit to. The backend rejects the attestation if it does not match the claim&apos;s current bundle at confirmation time.</p>
      <div className="reviewFacts">{(verification?.requirements ?? []).map((requirement) => (
        <div key={requirement.requirement}><Status kind={requirement.status === "PASS" ? "verified" : "warning"}>{requirement.status}</Status><span>{requirement.requirement.replace(/_/g, " ").toLowerCase()}<small>{requirement.reason}</small></span></div>
      ))}</div>
      {evidence?.reconciliation && <details><summary>Inspect all reconciliation checks ({evidence.reconciliation.status.replace(/_/g, " ")})</summary><ul className="checks">{evidence.reconciliation.checks.map((check) => <li key={check.check} className={check.result.toLowerCase()}><b aria-hidden>{check.result === "PASS" ? "✓" : check.result === "FAIL" ? "×" : "!"}</b><span>{check.message}</span></li>)}</ul></details>}
    </section>
      <aside className="panel decision"><span className="eyebrow">YOUR DECISION</span><h2>Attest to this bundle</h2><p>You are asserting that you reviewed this exact claim, evidence set, outcome, and provenance state.</p>
        {state === "idle" && <><button className="button full" onClick={verify}>Verify with wallet</button><button className="secondary full" disabled title="Awaiting a product decision on claim-state semantics">Request more evidence</button><button className="danger full" onClick={reject}>Reject</button></>}
        {state === "proving" && <div className="txState"><div className="spinner"/><b>Proving wallet control…</b><span>Sign the challenge. It authorises no transaction.</span></div>}
        {state === "connecting" && <div className="txState"><div className="spinner"/><b>Connecting wallet…</b><span>ImpactGraph will request the current verification bundle next.</span></div>}
        {state === "awaiting" && <div className="txState"><div className="spinner"/><b>Awaiting wallet signature…</b><span>No attestation exists until you approve.</span></div>}
        {state === "submitted" && <div className="txState"><div className="spinner"/><b>Attestation submitted</b><span className="hash">{transactionHash}</span><span>Waiting for receipt, expected event, and confirmation depth…</span></div>}
        {state === "confirmed" && <div className="txState success"><b>✓ Attestation confirmed</b><span>The expected event was validated and verification policy passed.</span><a href={`/claims/${claimId}`}>Return to claim →</a></div>}
        {state === "rejected" && <div className="txState failure"><b>Claim rejected</b><span>The decision and reason were recorded in the application audit trail. No verifier attestation was created.</span></div>}
        {state === "failed" && <div className="txState failure"><b>Verification did not complete</b><span>{error}</span>{wrongAccount ? <><button className="button full" onClick={chooseAnotherAccount}>Choose a different account</button><span className="note">Switching accounts inside the extension is not enough — a site only sees the accounts it was granted. This reopens the picker.</span></> : <button className="secondary full" onClick={() => setState("idle")}>Try again</button>}</div>}
        <DemoWalletHelp />
        <p className="note">The wallet submits directly to the configured EVM. ImpactGraph independently validates the receipt and registry event before changing the claim.</p>
      </aside>
    </div>
  </div>;
}
