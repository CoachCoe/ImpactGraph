import { notFound } from "next/navigation";
import { IntegrityCheck } from "@/components/IntegrityCheck";
import { Status } from "@/components/Status";
import { readFromApi } from "@/lib/api";
import { explorerLink, networkInfo } from "@/lib/network";
import type { Evidence } from "@/lib/types";
import { crossCheckSentence } from "@/lib/sentences";

const money = (minor: number, currency: string) =>
  new Intl.NumberFormat("en-US", { style: "currency", currency }).format(minor / 100);

export default async function EvidenceInspector({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const evidence = await readFromApi<Evidence>(`/evidence/${id}`);
  if (!evidence) notFound();

  const network = networkInfo();
  const extraction = evidence.extraction;
  const checks = evidence.reconciliation?.checks ?? [];
  const txHash = evidence.blockchainReference?.transactionHash;

  return (
    <div className="inspector">
      <section className="claimHero">
        <div>
          <span className="eyebrow">EVIDENCE</span>
          <h1>{String(extraction?.invoiceNumber ?? evidence.id)}</h1>
          <p>
            {evidence.type} · {evidence.mimeType} · visibility {evidence.visibility}
          </p>
        </div>
        <Status kind={evidence.blockchainStatus === "CONFIRMED" ? "verified" : "pending"}>
          {evidence.workflowStatus.replace(/_/g, " ")}
        </Status>
      </section>

      <div className="contentGrid">
        <div>
          <section className="panel">
            <span className="eyebrow">INTEGRITY</span>
            <h2>Does this still match what was committed?</h2>
            <p className="subtle">
              The check reads the stored object back and re-hashes it, then compares the
              result with the commitment registered when the evidence was accepted. A match
              proves the bytes have not changed. It does not prove the document is true.
            </p>
            <IntegrityCheck
              evidenceId={evidence.id}
              documentType={String(extraction?.documentType ?? "")}
            />
          </section>

          {extraction && (
            <section className="panel">
              <span className="eyebrow">EXTRACTION</span>
              <h2>What the document says</h2>
              <div className="evidenceSummary">
                <div>
                  <small>VENDOR</small>
                  <b>{String(extraction.vendor)}</b>
                </div>
                <div>
                  <small>AMOUNT</small>
                  <b>
                    {money(Number(extraction.amountMinor), String(extraction.currency))}
                  </b>
                </div>
                <div>
                  <small>EQUIPMENT</small>
                  <b>
                    {String(extraction.quantity)} × {String(extraction.equipment)}
                  </b>
                </div>
                <div>
                  <small>DATE</small>
                  <b>{String(extraction.date)}</b>
                </div>
              </div>
              <p className="note">
                Extracted by the mock analysis provider at{" "}
                {Math.round(Number(extraction.confidence) * 100)}% confidence. This is
                extraction confidence, not trust: AI proposes facts and never verifies a
                claim.
              </p>
            </section>
          )}

          {checks.length > 0 && (
            <section className="panel">
              <span className="eyebrow">RECONCILIATION</span>
              <h2>{crossCheckSentence(checks)}</h2>
              <ul className="checks">
                {checks.map((check) => (
                  <li key={check.check} className={check.result.toLowerCase()}>
                    <b aria-hidden>
                      {check.result === "PASS" ? "✓" : check.result === "FAIL" ? "×" : "!"}
                    </b>
                    <span>{check.message}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>

        <aside>
          <section className="panel proof">
            <span className="eyebrow">COMMITMENT</span>
            <dl>
              <dt>Original content hash</dt>
              <dd className="hash">{evidence.contentHash}</dd>
              <dt>Network</dt>
              <dd>{network.name}</dd>
              <dt>Onchain status</dt>
              <dd>{evidence.blockchainStatus}</dd>
              {txHash && (
                <>
                  <dt>Registration transaction</dt>
                  <dd className="hash">
                    {explorerLink(network.explorerBase, "tx", txHash) ? (
                      <a
                        href={explorerLink(network.explorerBase, "tx", txHash)!}
                        target="_blank"
                        rel="noreferrer"
                      >
                        {txHash}
                      </a>
                    ) : (
                      txHash
                    )}
                  </dd>
                </>
              )}
            </dl>
            <p className="note">
              The hash is taken over the exact uploaded bytes, before any analysis or
              transformation.
            </p>
          </section>
        </aside>
      </div>
    </div>
  );
}
