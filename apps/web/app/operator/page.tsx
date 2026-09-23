"use client";

import { ChangeEvent, useState } from "react";
import { RequireRole } from "@/components/RequireRole";
import { Status } from "@/components/Status";
import { api } from "@/lib/api";

type WorkflowState = "idle" | "uploading" | "analyzing" | "review" | "registering" | "submitted" | "confirmed" | "failed";
// documentType is required by the review endpoint, and is passed straight back from
// the analysis response. Omitting it here made a hand-built extraction typecheck and
// then fail validation at the only point it is ever sent.
type Extraction = { documentType: string; invoiceNumber: string; vendor: string; amountMinor: number; currency: string; date: string; equipment: string; quantity: number; projectReference: string; confidence: number };
type Finding = { result: "PASS" | "WARNING" | "FAIL"; message: string };
type EvidenceResponse = { id: string; workflowStatus: string; contentHash: string; extraction?: Extraction; reconciliation?: { status: string; checks: Finding[] } };
type RegistrationResponse = { operationId: string; status?: string; transactionHash?: string };

const invoiceText = `IMPACTGRAPH DEMO INVOICE
Invoice INV-8291
Aqua Systems Ltd.
2 x AquaPure X200
USD 4,200.00
Project: Water Project #12
Date: 2026-08-17
`;
const sleep = (milliseconds: number) => new Promise((resolve) => setTimeout(resolve, milliseconds));

export default function OperatorPage() {
  return <RequireRole role="OPERATOR">{() => <Operator />}</RequireRole>;
}

function Operator() {
  const [state, setState] = useState<WorkflowState>("idle");
  const [file, setFile] = useState<File | null>(null);
  const [evidenceId, setEvidenceId] = useState("");
  const [evidence, setEvidence] = useState<EvidenceResponse | null>(null);
  const [operation, setOperation] = useState<RegistrationResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const selectFile = (event: ChangeEvent<HTMLInputElement>) => { setFile(event.target.files?.[0] ?? null); setError(null); };
  const loadDemoInvoice = () => { setFile(new File([`${invoiceText}Demo upload: ${crypto.randomUUID()}\n`], "INV-8291.txt", { type: "text/plain" })); setError(null); };

  const processEvidence = async () => {
    if (!file) return;
    const id = `ev-inv-8291-${crypto.randomUUID()}`;
    setEvidenceId(id); setError(null);
    try {
      setState("uploading");
      const form = new FormData();
      form.set("evidence_id", id); form.set("project_id", "project-water-12"); form.set("evidence_type", "INVOICE"); form.set("visibility", "RESTRICTED"); form.set("file", file);
      await api<EvidenceResponse>("/evidence", { method: "POST", headers: { "Idempotency-Key": crypto.randomUUID() }, body: form });
      setState("analyzing");
      const analyzed = await api<EvidenceResponse>(`/evidence/${id}/analyze`, { method: "POST" });
      setEvidence(analyzed); setState("review");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Evidence processing failed."); setState("failed"); }
  };

  const acceptAndRegister = async () => {
    if (!evidence?.extraction || !evidenceId) return;
    setError(null);
    try {
      setState("registering");
      await api<EvidenceResponse>(`/evidence/${evidenceId}/review`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ extraction: evidence.extraction }) });
      const submitted = await api<RegistrationResponse>(`/evidence/${evidenceId}/register`, { method: "POST", headers: { "Idempotency-Key": crypto.randomUUID() } });
      setOperation(submitted); setState("submitted");
      for (let attempt = 0; attempt < 120; attempt += 1) {
        const observed = await api<RegistrationResponse>(`/blockchain/operations/${submitted.operationId}`);
        setOperation(observed);
        if (observed.status === "CONFIRMED") { setState("confirmed"); return; }
        if (observed.status === "FAILED") throw new Error("Evidence registration failed before confirmation. Your uploaded evidence remains safe.");
        await sleep(1000);
      }
      throw new Error("Evidence registration is still pending. You can safely check again shortly.");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Evidence registration failed."); setState("failed"); }
  };

  const extraction = evidence?.extraction;
  const findings = evidence?.reconciliation?.checks ?? [];
  const busy = ["uploading", "analyzing", "registering", "submitted"].includes(state);
  return <div className="workspace">
    <div className="workspaceHead"><div><span className="eyebrow">PROGRAM OPERATOR</span><h1>Water Project #12</h1><p>Global Water Initiative · Kisumu County</p></div><Status kind={state === "confirmed" ? "verified" : state === "failed" ? "failed" : "pending"}>{state === "confirmed" ? "Evidence registered" : state === "failed" ? "Action required" : "Verification pending"}</Status></div>
    <section className="metrics compact"><article><span>Budget</span><strong>$8,500</strong></article><article><span>Spent</span><strong>$4,200</strong></article><article><span>Deliveries</span><strong>1</strong></article><article><span>Evidence objects</span><strong>{state === "confirmed" ? 6 : 5}</strong></article></section>
    {state === "idle" || (state === "failed" && !extraction) ? <section className="panel uploadPanel"><span className="eyebrow">UPLOAD EVIDENCE</span><h2>Process an original document</h2><p className="subtle">ImpactGraph hashes the exact selected bytes before analysis or transformation.</p><label className="filePicker"><span>{file?.name ?? "Choose invoice or report"}</span><input aria-label="Choose evidence file" type="file" accept="text/plain,application/pdf,image/jpeg,image/png" onChange={selectFile} /></label><div className="uploadActions"><button className="secondary" onClick={loadDemoInvoice}>Load demo INV-8291</button><button className="button" disabled={!file || busy} onClick={processEvidence}>Upload & analyze</button></div>{error && <div className="errorMessage" role="alert"><b>Evidence processing failed</b><span>{error}</span><button className="secondary" onClick={processEvidence} disabled={!file}>Try again</button></div>}</section> : null}
    {busy && !extraction ? <section className="panel txState"><div className="spinner"/><b>{state === "uploading" ? "Storing and hashing original bytes…" : "Analyzing evidence…"}</b><span>AI output proposes facts; it does not verify the claim.</span></section> : null}
    {extraction ? <div className="contentGrid operatorGrid"><section className="panel"><span className="eyebrow">EVIDENCE ANALYSIS</span><div className="documentTitle"><div>DOC</div><span><b>Invoice {extraction.invoiceNumber}</b><small>Original bytes hashed before processing</small></span><Status kind="verified">Analyzed</Status></div><div className="formGrid">{[["Vendor",extraction.vendor],["Amount",`$${(extraction.amountMinor / 100).toLocaleString()} ${extraction.currency}`],["Date",extraction.date],["Equipment",extraction.equipment],["Quantity",String(extraction.quantity)],["Project",extraction.projectReference]].map(([label,value])=><label key={label}>{label}<input value={value} readOnly /></label>)}</div><div className="aiNote"><b>AI confidence {Math.round(extraction.confidence * 100)}%</b><span>Mock provider · structured schema v1</span></div></section>
      <section className="panel"><span className="eyebrow">RECONCILIATION</span><h2>Cross-check results</h2><ul className="checks">{findings.map((finding)=><li key={finding.message} className={finding.result.toLowerCase()}><b>{finding.result === "PASS" ? "✓" : "!"}</b>{finding.message}</li>)}</ul>{state === "confirmed" ? <div className="txState success"><b>✓ Evidence registration confirmed</b><span>{operation?.transactionHash ?? "Expected EvidenceRegistered event observed"}</span></div> : <button className="button full" onClick={acceptAndRegister} disabled={busy}>{state === "registering" ? "Creating registration intent…" : state === "submitted" ? "Waiting for confirmation…" : state === "failed" ? "Retry registration" : "Accept & register evidence"}</button>}<p className="note">The extraction is operator-reviewed. Registration is not complete until the expected registry event is confirmed.</p>{error && <div className="errorMessage" role="alert">{error}</div>}</section></div> : null}
  </div>;
}
