"use client";

import { useState } from "react";
import Link from "next/link";
import { RequireRole } from "@/components/RequireRole";
import { Status } from "@/components/Status";
import { api } from "@/lib/api";
import type { IntegrityResult } from "@/lib/types";

const EVIDENCE_ID = "ev-inv-8291";

export default function AdminPage() {
  return <RequireRole role="ADMIN">{() => <AdminConsole />}</RequireRole>;
}

function AdminConsole() {
  const [result, setResult] = useState<IntegrityResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const run = async (label: string, action: () => Promise<void>) => {
    setBusy(label);
    setError(null);
    try {
      await action();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The action failed.");
    } finally {
      setBusy(null);
    }
  };

  const tamper = () =>
    run("tamper", async () => {
      setResult(
        await api<IntegrityResult>(`/demo/evidence/${EVIDENCE_ID}/tamper`, { method: "POST" }),
      );
    });

  const recheck = () =>
    run("recheck", async () => {
      setResult(
        await api<IntegrityResult>(`/evidence/${EVIDENCE_ID}/verify-integrity`, {
          method: "POST",
        }),
      );
    });

  const reset = () =>
    run("reset", async () => {
      await api("/demo/reset", { method: "POST" });
      setResult(null);
    });

  return (
    <div className="workspace">
      <div className="workspaceHead">
        <div>
          <span className="eyebrow">ADMINISTRATION</span>
          <h1>Tampering demonstration</h1>
          <p>Local demo controls. Refused when the demo is running against Sepolia.</p>
        </div>
      </div>

      <section className="panel">
        <h2>Alter the stored evidence</h2>
        <p className="subtle">
          This overwrites the stored bytes of {EVIDENCE_ID} — it does not set a flag. The
          registered commitment is untouched, so the next integrity check must disagree with
          it. ImpactGraph can change its own storage; it cannot change what was committed.
        </p>
        <div className="uploadActions">
          <button className="danger" onClick={tamper} disabled={busy !== null}>
            {busy === "tamper" ? "Altering…" : "Tamper with stored evidence"}
          </button>
          <button className="secondary" onClick={recheck} disabled={busy !== null}>
            {busy === "recheck" ? "Re-hashing…" : "Re-run integrity check"}
          </button>
          <button className="secondary" onClick={reset} disabled={busy !== null}>
            {busy === "reset" ? "Restoring…" : "Reset demo"}
          </button>
        </div>

        {error && (
          <div className="errorMessage" role="alert">
            <b>Action failed</b>
            <span>{error}</span>
          </div>
        )}

        {result && (
          <div className="integrityResult" role="status">
            <Status kind={result.status === "MATCH" ? "verified" : "failed"}>
              {result.status}
            </Status>
            <p>{result.explanation}</p>
            <dl>
              <dt>Registered commitment</dt>
              <dd className="hash">{result.expected}</dd>
              <dt>Hash of the stored object now</dt>
              <dd className="hash">{result.current}</dd>
            </dl>
            <p className="note">
              <Link href={`/evidence/${EVIDENCE_ID}`}>
                See the same check on the donor-facing evidence page →
              </Link>
            </p>
          </div>
        )}
      </section>
    </div>
  );
}
