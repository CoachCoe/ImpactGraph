"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { useSession } from "@/components/SessionProvider";
import { api } from "@/lib/api";

type ImportResult = {
  provider: string;
  imported: string[];
  skipped: string[];
  rejected: { sourceRef: string; reason: string }[];
};

/** Operator-only control that pulls the provider statement into the ledger. */
export function ImportStatement({ programId }: { programId: string }) {
  const { session } = useSession();
  const router = useRouter();
  const [result, setResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (session?.role !== "OPERATOR" && session?.role !== "ADMIN") return null;

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      setResult(
        await api<ImportResult>(`/financial/programs/${programId}/import`, {
          method: "POST",
          headers: { "Idempotency-Key": crypto.randomUUID() },
        }),
      );
      router.refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The import failed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel">
      <span className="eyebrow">OPERATOR</span>
      <h2>Import the provider statement</h2>
      <p className="subtle">
        Pulls observed payments from the financial data provider. Re-importing is a no-op:
        payments are unique per provider reference. Spend beyond the allocation is rejected
        rather than recorded.
      </p>
      <button className="button" onClick={run} disabled={busy}>
        {busy ? "Importing…" : "Import statement"}
      </button>
      {error && (
        <div className="errorMessage" role="alert">
          <b>Import failed</b>
          <span>{error}</span>
        </div>
      )}
      {result && (
        <div className="integrityResult" role="status">
          <p>
            <b>{result.imported.length}</b> recorded · <b>{result.skipped.length}</b> already
            observed · <b>{result.rejected.length}</b> rejected
          </p>
          {result.rejected.map((item) => (
            <p key={item.sourceRef} className="note">
              {item.sourceRef}: {item.reason}
            </p>
          ))}
        </div>
      )}
    </section>
  );
}
