"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { RequireRole } from "@/components/RequireRole";
import { Status } from "@/components/Status";
import { api } from "@/lib/api";
import type { Claim, IntegrityResult } from "@/lib/types";

export default function AdminPage() {
  return <RequireRole role="ADMIN">{() => <AdminConsole />}</RequireRole>;
}

function AdminConsole() {
  const [result, setResult] = useState<IntegrityResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  // Resolved rather than named: the demonstration has to act on evidence that exists, and
  // "ev-inv-8291" only exists where the showcase seed ran.
  const [evidenceId, setEvidenceId] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      const claims = (await api<{ id: string }[]>("/claims").catch(() => null)) ?? [];
      const first = claims[0]?.id;
      if (!first) return;
      const claim = await api<Claim>(`/claims/${first}`).catch(() => null);
      setEvidenceId(claim?.evidenceIds[0] ?? null);
    })();
  }, []);

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
        await api<IntegrityResult>(`/demo/evidence/${evidenceId}/tamper`, { method: "POST" }),
      );
    });

  const recheck = () =>
    run("recheck", async () => {
      setResult(
        await api<IntegrityResult>(`/evidence/${evidenceId}/verify-integrity`, {
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
          This overwrites the stored bytes of {evidenceId ?? "the registered evidence"} — it does not set a flag. The
          registered commitment is untouched, so the next integrity check must disagree with
          it. ImpactGraph can change its own storage; it cannot change what was committed.
        </p>
        <div className="uploadActions">
          <button className="danger" onClick={tamper} disabled={busy !== null || !evidenceId}>
            {busy === "tamper" ? "Altering…" : "Tamper with stored evidence"}
          </button>
          <button className="secondary" onClick={recheck} disabled={busy !== null || !evidenceId}>
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
              <Link href={`/evidence/${evidenceId}`}>
                See the same check on the donor-facing evidence page →
              </Link>
            </p>
          </div>
        )}
      </section>
    </div>
  );
}
