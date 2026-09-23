"use client";

import { ChangeEvent, useEffect, useState } from "react";
import { RequireRole } from "@/components/RequireRole";
import { Status } from "@/components/Status";
import { api } from "@/lib/api";

type WorkflowState = "idle" | "uploading" | "analyzing" | "review" | "registering" | "submitted" | "confirmed" | "failed";
// documentType is required by the review endpoint, and is passed straight back from
// the analysis response. Omitting it here made a hand-built extraction typecheck and
// then fail validation at the only point it is ever sent.
type Extraction = { documentType: string; invoiceNumber: string; amountMinor: number; currency: string; vendor: string | null; date: string | null; equipment: string | null; quantity: number | null; projectReference: string | null; selfReportedConfidence: Record<string, number> };
type Finding = { result: "PASS" | "WARNING" | "FAIL"; message: string };
type EvidenceResponse = { id: string; workflowStatus: string; contentHash: string; extraction?: Extraction; reviewRequired?: string[]; reconciliation?: { status: string; checks: Finding[] }; providerMetadata?: { provider: string; model: string } };

// Every field the API can put in reviewRequired is editable here. One that is flagged and
// not rendered would leave registration permanently blocked on a field nobody can reach.
type Project = { id: string; name: string };
type OperatorProgram = { id: string; name: string; region: string; operator: string; chainStatus: string; projects: Project[] };

type Basis = {
  lawfulBasis: "LEGITIMATE_INTEREST" | "CONSENT";
  specialCategory: boolean;
  subjectReference: string;
  purpose: string;
  retainUntil: string;
};

type Field = Exclude<keyof Extraction, "selfReportedConfidence">;
const FIELDS: [Field, string][] = [
  ["documentType", "Document type"],
  ["invoiceNumber", "Invoice number"],
  ["vendor", "Vendor"],
  ["amountMinor", "Amount (minor units)"],
  ["currency", "Currency"],
  ["date", "Date"],
  ["equipment", "Equipment"],
  ["quantity", "Quantity"],
  ["projectReference", "Project"],
];
const NUMERIC = new Set(["amountMinor", "quantity"]);
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
  // Which project the evidence is filed under. This was "project-water-12" in the source,
  // so an operator at any other organisation filed against a project they do not own.
  const [programs, setPrograms] = useState<OperatorProgram[] | null>(null);
  const [projectId, setProjectId] = useState("");
  // Declared by the operator, because nothing else can tell whether a photograph has a
  // person in it. Registration is refused for such a document until a lawful basis and a
  // controller have been recorded, so the form for that follows the analysis.
  const [personalData, setPersonalData] = useState(false);
  const [basis, setBasis] = useState<Basis>({
    lawfulBasis: "LEGITIMATE_INTEREST",
    specialCategory: false,
    subjectReference: "",
    purpose: "",
    retainUntil: "",
  });
  const [basisRecorded, setBasisRecorded] = useState(false);
  const [draft, setDraft] = useState<Extraction | null>(null);
  const [confirmed, setConfirmed] = useState<Record<string, boolean>>({});
  const [operation, setOperation] = useState<RegistrationResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      const mine = (await api<OperatorProgram[]>("/operator/programs").catch(() => null)) ?? [];
      setPrograms(mine);
      // A program still waiting for its registry entity cannot accept evidence, so it is
      // not offered as somewhere to file it.
      const ready = mine.filter((program) => program.chainStatus === "CONFIRMED");
      setProjectId(ready.flatMap((program) => program.projects)[0]?.id ?? "");
    })();
  }, []);

  const selectFile = (event: ChangeEvent<HTMLInputElement>) => { setFile(event.target.files?.[0] ?? null); setError(null); };
  const loadDemoInvoice = () => { setFile(new File([`${invoiceText}Demo upload: ${crypto.randomUUID()}\n`], "INV-8291.txt", { type: "text/plain" })); setError(null); };

  const processEvidence = async () => {
    if (!file || !projectId) return;
    const id = `ev-${crypto.randomUUID()}`;
    setEvidenceId(id); setError(null);
    try {
      setState("uploading");
      const form = new FormData();
      form.set("evidence_id", id); form.set("project_id", projectId); form.set("evidence_type", "INVOICE"); form.set("visibility", "RESTRICTED"); form.set("personal_data", String(personalData)); form.set("file", file);
      await api<EvidenceResponse>("/evidence", { method: "POST", headers: { "Idempotency-Key": crypto.randomUUID() }, body: form });
      setState("analyzing");
      const analyzed = await api<EvidenceResponse>(`/evidence/${id}/analyze`, { method: "POST" });
      setEvidence(analyzed); setDraft(analyzed.extraction ?? null); setConfirmed({}); setState("review");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Evidence processing failed."); setState("failed"); }
  };

  const recordBasis = async () => {
    if (!evidenceId || !selectedProgram) return;
    setError(null);
    try {
      await api(`/evidence/${evidenceId}/data-protection`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lawfulBasis: basis.lawfulBasis,
          specialCategory: basis.specialCategory,
          // From the programme this is filed under, not typed: the controller is the
          // organisation answerable for the programme, not whatever someone entered.
          controllerOrgRef: selectedProgram.operator,
          subjectReference: basis.subjectReference,
          purpose: basis.purpose,
          retainUntil: basis.retainUntil || null,
        }),
      });
      setBasisRecorded(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The basis could not be recorded.");
    }
  };

  const acceptAndRegister = async () => {
    if (!draft || !evidenceId || outstanding.length) return;
    setError(null);
    try {
      setState("registering");
      await api<EvidenceResponse>(`/evidence/${evidenceId}/review`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ extraction: draft, confirmed: Object.keys(confirmed).filter((field) => confirmed[field]) }) });
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

  const findings = evidence?.reconciliation?.checks ?? [];
  const busy = ["uploading", "analyzing", "registering", "submitted"].includes(state);
  const reviewRequired = evidence?.reviewRequired ?? [];
  const filing = (programs ?? [])
    .filter((program) => program.chainStatus === "CONFIRMED")
    .flatMap((program) => program.projects.map((project) => ({ program, project })));
  const selected = filing.find((entry) => entry.project.id === projectId);
  const selectedProgram = selected?.program;
  const selectedProject = selected?.project;
  const outstanding = state === "confirmed" ? [] : reviewRequired.filter((field) => !confirmed[field]);
  const basisOutstanding = personalData && !basisRecorded;
  const basisComplete = basis.subjectReference.trim() !== "" && basis.purpose.trim() !== "";
  const editField = (field: Field, value: string) => setDraft((current) => current && { ...current, [field]: NUMERIC.has(field) ? Number(value) : value });
  return <div className="workspace">
    <div className="workspaceHead"><div><span className="eyebrow">PROGRAM OPERATOR</span><h1>{selectedProject?.name ?? "Evidence"}</h1><p>{selectedProgram ? `${selectedProgram.operator} · ${selectedProgram.region}` : "Choose where this document belongs"}</p></div><Status kind={state === "confirmed" ? "verified" : state === "failed" ? "failed" : "pending"}>{state === "confirmed" ? "Evidence registered" : state === "failed" ? "Action required" : "Verification pending"}</Status></div>
    {programs !== null && filing.length === 0 ? <section className="panel"><h2>No project is ready for evidence</h2><p className="subtle">{programs.length === 0 ? "Your organisation has no programs yet." : "A program cannot accept evidence until its registry entity is confirmed on chain, and none of yours has a project under a confirmed program."}</p></section> : null}
    {state === "idle" || (state === "failed" && !draft) ? <section className="panel uploadPanel"><span className="eyebrow">UPLOAD EVIDENCE</span><h2>Process an original document</h2><p className="subtle">ImpactGraph hashes the exact selected bytes before analysis or transformation.</p><label className="filePicker"><span>{file?.name ?? "Choose invoice or report"}</span><input aria-label="Choose evidence file" type="file" accept="text/plain,application/pdf,image/jpeg,image/png" onChange={selectFile} /></label>{filing.length > 1 ? <label className="projectPicker">File under<select aria-label="Project" value={projectId} onChange={(event) => setProjectId(event.target.value)}>{filing.map(({ program, project }) => <option key={project.id} value={project.id}>{program.name} — {project.name}</option>)}</select></label> : null}<label className="personalDataCheck"><input type="checkbox" aria-label="This document contains personal data" checked={personalData} onChange={(event) => setPersonalData(event.target.checked)} /><span><b>This document contains personal data</b><small>A photograph of someone, a household register, anything naming a person. It cannot be registered until a lawful basis and a controller are recorded.</small></span></label><div className="uploadActions"><button className="secondary" onClick={loadDemoInvoice}>Load demo INV-8291</button><button className="button" disabled={!file || busy || !projectId} onClick={processEvidence}>Upload & analyze</button></div>{error && <div className="errorMessage" role="alert"><b>Evidence processing failed</b><span>{error}</span><button className="secondary" onClick={processEvidence} disabled={!file}>Try again</button></div>}</section> : null}
    {busy && !draft ? <section className="panel txState"><div className="spinner"/><b>{state === "uploading" ? "Storing and hashing original bytes…" : "Analyzing evidence…"}</b><span>AI output proposes facts; it does not verify the claim.</span></section> : null}
    {draft ? <div className="contentGrid operatorGrid"><section className="panel"><span className="eyebrow">EVIDENCE ANALYSIS</span><div className="documentTitle"><div>DOC</div><span><b>Invoice {draft.invoiceNumber}</b><small>Original bytes hashed before processing</small></span><Status kind="verified">Analyzed</Status></div>
      {outstanding.length ? <p className="reviewNote" role="status"><b>{outstanding.length} field{outstanding.length === 1 ? "" : "s"} need your confirmation.</b> The invoice number, amount and currency are always checked by a person: reconciliation resolves a payment against them, and one misread digit turns a matched payment into an unmatched one.</p> : null}
      <div className="formGrid">{FIELDS.map(([field,label])=>{const needed=reviewRequired.includes(field);const stated=draft.selfReportedConfidence?.[field];return <label key={field} className={needed && !confirmed[field] ? "needsReview" : undefined}>{label}<input aria-label={label} value={draft[field] ?? ""} readOnly={!needed || state !== "review"} onChange={(event)=>editField(field,event.target.value)} />{needed ? <span className="confirmField"><input type="checkbox" aria-label={`Confirm ${label}`} checked={!!confirmed[field]} disabled={state !== "review"} onChange={(event)=>setConfirmed((current)=>({...current,[field]:event.target.checked}))} />Confirm{stated === undefined ? " — the model stated no confidence in this field" : ` — model stated ${Math.round(stated * 100)}%`}</span> : null}</label>;})}</div>
      {basisOutstanding ? <div className="basisPanel"><span className="eyebrow">LAWFUL BASIS</span><h3>Why this document may be held</h3><p className="subtle">Recorded before registration, because the commitment cannot be withdrawn afterwards.</p>
        <label>Basis<select aria-label="Lawful basis" value={basis.lawfulBasis} onChange={(event) => setBasis({ ...basis, lawfulBasis: event.target.value as Basis["lawfulBasis"] })}><option value="LEGITIMATE_INTEREST">Legitimate interest — with a right to object</option><option value="CONSENT">Explicit consent</option></select></label>
        <label>Who it is about<input aria-label="Subject reference" value={basis.subjectReference} placeholder="subject-household-14" onChange={(event) => setBasis({ ...basis, subjectReference: event.target.value })} /></label>
        <label>Purpose<input aria-label="Purpose" value={basis.purpose} placeholder="Showing a funder what their money delivered" onChange={(event) => setBasis({ ...basis, purpose: event.target.value })} /></label>
        <label>Erase on<input aria-label="Retain until" type="date" value={basis.retainUntil} onChange={(event) => setBasis({ ...basis, retainUntil: event.target.value })} /></label>
        <label className="personalDataCheck"><input type="checkbox" aria-label="Special category data" checked={basis.specialCategory} onChange={(event) => setBasis({ ...basis, specialCategory: event.target.checked, lawfulBasis: event.target.checked ? "CONSENT" : basis.lawfulBasis })} /><span><b>Health, vulnerability or other special-category data</b><small>Legitimate interest cannot carry this, so it requires explicit consent.</small></span></label>
        <button className="button full" disabled={!basisComplete} onClick={recordBasis}>Record the basis</button></div> : null}
      <div className="aiNote"><b>Read by {evidence?.providerMetadata?.provider ?? "the analysis provider"}</b><span>{evidence?.providerMetadata?.model ?? ""} · confidence is self-reported, not measured</span></div></section>
      <section className="panel"><span className="eyebrow">RECONCILIATION</span><h2>Cross-check results</h2><ul className="checks">{findings.map((finding)=><li key={finding.message} className={finding.result.toLowerCase()}><b>{finding.result === "PASS" ? "✓" : "!"}</b>{finding.message}</li>)}</ul>{state === "confirmed" ? <div className="txState success"><b>✓ Evidence registration confirmed</b><span>{operation?.transactionHash ?? "Expected EvidenceRegistered event observed"}</span></div> : <button className="button full" onClick={acceptAndRegister} disabled={busy || outstanding.length > 0 || basisOutstanding}>{state === "registering" ? "Creating registration intent…" : state === "submitted" ? "Waiting for confirmation…" : basisOutstanding ? "Record a lawful basis to continue" : outstanding.length ? `Confirm ${outstanding.length} field${outstanding.length === 1 ? "" : "s"} to continue` : state === "failed" ? "Retry registration" : "Accept & register evidence"}</button>}<p className="note">The extraction is operator-reviewed. Registration is not complete until the expected registry event is confirmed.</p>{error && <div className="errorMessage" role="alert">{error}</div>}</section></div> : null}
  </div>;
}
