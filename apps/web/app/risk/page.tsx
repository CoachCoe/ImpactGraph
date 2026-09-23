"use client";

import { useCallback, useEffect, useState } from "react";
import { RequireRole } from "@/components/RequireRole";
import { Status } from "@/components/Status";
import { api } from "@/lib/api";

type Finding = {
  id: string;
  kind: string;
  explanation: string;
  subjects: Record<string, unknown>;
  state: "OPEN" | "INVESTIGATING" | "CONFIRMED" | "DISMISSED";
  assignedTo: string | null;
  dispositionNote: string | null;
  raisedAt: string | null;
  closedAt: string | null;
};

type Precision = Record<
  string,
  { confirmed: number; dismissed: number; decided: number; precision: number | null }
>;

const KIND_LABELS: Record<string, string> = {
  DUPLICATE_INVOICE: "Duplicate invoice",
  REUSED_IMAGE: "Reused photograph",
  VENDOR_CONCENTRATION: "Vendor concentration",
};

function subjectLines(subjects: Record<string, unknown>): [string, string][] {
  return Object.entries(subjects).map(([key, value]) => [
    key.replace(/([A-Z])/g, " $1").toLowerCase(),
    Array.isArray(value) ? value.join(", ") : String(value),
  ]);
}

export default function RiskPage() {
  return <RequireRole role="OPERATOR">{() => <RiskQueue />}</RequireRole>;
}

function RiskQueue() {
  const [findings, setFindings] = useState<Finding[] | null>(null);
  const [precision, setPrecision] = useState<Precision>({});
  const [scanning, setScanning] = useState(false);
  // Page-level problems only. A refusal to close one finding belongs beside that
  // finding, not at the top of a queue the reviewer may have scrolled past.
  const [error, setError] = useState<string | null>(null);
  const [findingError, setFindingError] = useState<Record<string, string>>({});
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [queue, measured] = await Promise.all([
        api<{ findings: Finding[] }>("/risk/findings"),
        api<{ byKind: Precision }>("/risk/precision"),
      ]);
      setFindings(queue.findings);
      setPrecision(measured.byKind);
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The queue could not be loaded.");
      setFindings([]);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const scan = async () => {
    setScanning(true);
    setError(null);
    try {
      await api("/risk/scan", { method: "POST" });
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The scan did not complete.");
    } finally {
      setScanning(false);
    }
  };

  const failFinding = (id: string, message: string) =>
    setFindingError((current) => ({ ...current, [id]: message }));

  const dispose = async (finding: Finding, state: Finding["state"]) => {
    const note = (notes[finding.id] ?? "").trim();
    if ((state === "CONFIRMED" || state === "DISMISSED") && !note) {
      failFinding(
        finding.id,
        "Say why before closing this one. The note is what makes these checks measurable.",
      );
      return;
    }
    setBusy(finding.id);
    setFindingError((current) => {
      const next = { ...current };
      delete next[finding.id];
      return next;
    });
    try {
      await api(`/risk/findings/${finding.id}/disposition`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ state, note }),
      });
      setNotes((current) => ({ ...current, [finding.id]: "" }));
      await load();
    } catch (reason) {
      failFinding(
        finding.id,
        reason instanceof Error ? reason.message : "The decision was not recorded.",
      );
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="workspace">
      <div className="workspaceHead">
        <div>
          <span className="eyebrow">REVIEW QUEUE</span>
          <h1>Things that look wrong</h1>
          <p>
            Patterns across your own programmes. Nothing here has changed a claim, and
            nothing here is an accusation — each one is a question for you to answer.
          </p>
        </div>
        <button className="button" onClick={scan} disabled={scanning}>
          {scanning ? "Scanning…" : "Run a scan"}
        </button>
      </div>

      {error ? (
        <section className="panel">
          <Status kind="failed">Action required</Status>
          <p>{error}</p>
        </section>
      ) : null}

      {Object.keys(precision).length > 0 ? (
        <section className="panel">
          <span className="eyebrow">HOW OFTEN THESE CHECKS WERE RIGHT</span>
          <ul className="checks">
            {Object.entries(precision).map(([kind, counts]) => (
              <li key={kind}>
                <b aria-hidden>·</b>
                <span>
                  {KIND_LABELS[kind] ?? kind}:{" "}
                  {counts.precision === null
                    ? "not yet judged"
                    : `${Math.round(counts.precision * 100)}% confirmed`}
                  <small>
                    {counts.confirmed} confirmed, {counts.dismissed} dismissed. Measured from
                    your decisions; it cannot say what was never surfaced.
                  </small>
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {findings === null ? <section className="panel">Loading…</section> : null}

      {findings !== null && findings.length === 0 ? (
        <section className="panel">
          <h2>Nothing is waiting for you</h2>
          <p className="subtle">
            No duplicate invoice, reused photograph or concentrated supplier was found in
            your programmes. Run a scan after new evidence or payments arrive.
          </p>
        </section>
      ) : null}

      {(findings ?? []).map((finding) => (
        <section className="panel" key={finding.id}>
          <div className="workspaceHead">
            <div>
              <span className="eyebrow">{KIND_LABELS[finding.kind] ?? finding.kind}</span>
              <h2>{finding.explanation}</h2>
            </div>
            <Status kind={finding.state === "INVESTIGATING" ? "pending" : "warning"}>
              {finding.state === "INVESTIGATING" ? "Being looked at" : "Not yet reviewed"}
            </Status>
          </div>

          <dl className="riskFacts">
            {subjectLines(finding.subjects).map(([label, value]) => (
              <div key={label}>
                <dt>{label}</dt>
                <dd className="hash">{value}</dd>
              </div>
            ))}
          </dl>

          <label className="field" htmlFor={`note-${finding.id}`}>
            What did you find?
          </label>
          <textarea
            id={`note-${finding.id}`}
            value={notes[finding.id] ?? ""}
            onChange={(event) =>
              setNotes((current) => ({ ...current, [finding.id]: event.target.value }))
            }
            className="riskNote"
            placeholder="Required to confirm or dismiss. This is what makes the checks measurable."
          />

          <div className="riskActions">
            {finding.state === "OPEN" ? (
              <button
                className="secondary"
                disabled={busy === finding.id}
                onClick={() => dispose(finding, "INVESTIGATING")}
              >
                I&apos;m looking into it
              </button>
            ) : null}
            <button
              className="danger"
              disabled={busy === finding.id}
              onClick={() => dispose(finding, "CONFIRMED")}
            >
              Confirm the problem
            </button>
            <button
              className="secondary"
              disabled={busy === finding.id}
              onClick={() => dispose(finding, "DISMISSED")}
            >
              Not a problem
            </button>
          </div>
          {findingError[finding.id] ? (
            <p className="errorMessage" role="alert">
              {findingError[finding.id]}
            </p>
          ) : null}
          <p className="note">
            Confirming records what you found. It does not change the claim, notify anyone
            outside your organisation, or make an accusation — deciding what to do about it
            stays with you.
          </p>
        </section>
      ))}
    </div>
  );
}
