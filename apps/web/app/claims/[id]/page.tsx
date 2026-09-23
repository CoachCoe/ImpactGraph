import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ProvenanceGraph } from "@/components/ProvenanceGraph";
import { Status } from "@/components/Status";
import { readFromApi } from "@/lib/api";
import { explorerLink, networkInfo } from "@/lib/network";
import type { Claim, Provenance, Verification } from "@/lib/types";
import { claimStanding, largestScoreGap, supportingEvidenceSentence } from "@/lib/sentences";

const SCORE_LABELS: Record<string, string> = {
  financialReconciliation: "Financial reconciliation",
  evidenceIntegrity: "Evidence integrity",
  operatorAttestation: "Operator attestation",
  independentVerification: "Independent verification",
  locationCorroboration: "Location corroboration",
  evidenceConsistency: "Evidence consistency",
};

// A requirement can also warn: something a reader should see that does not stop the
// claim verifying. Rendering anything that is not a pass as a red cross put a failure
// mark beside a verified claim.
const REQUIREMENT_TONE: Record<string, string> = { PASS: "pass", WARNING: "warning", FAIL: "fail" };
const REQUIREMENT_MARK: Record<string, string> = { PASS: "✓", WARNING: "!", FAIL: "×" };

const REQUIREMENT_LABELS: Record<string, string> = {
  PROVENANCE_COMPLETE: "Provenance is complete",
  EVIDENCE_REGISTERED: "Evidence is committed onchain",
  EVIDENCE_INTEGRITY: "Evidence still matches its commitment",
  FINANCIAL_RECONCILIATION: "Financial reconciliation passed",
  OPERATOR_ATTESTATION: "The operator has attested",
  INDEPENDENT_VERIFICATION: "An independent verifier has confirmed",
  BUNDLE_CURRENT: "The attestation covers the current evidence",
  ACTOR_SEPARATION: "The verifier is not the operator",
};

export async function generateMetadata({
  params,
}: {
  params: Promise<{ id: string }>;
}): Promise<Metadata> {
  const claim = await readFromApi<Claim>(`/claims/${(await params).id}`);
  if (!claim) return { title: "No such claim — ImpactGraph", robots: { index: false } };
  const standing = claimStanding(claim.status);
  return {
    title: `${standing.badge} — ${claim.statement}`,
    description: `${claim.statement} Inspect the evidence, the money and the verification behind it.`,
  };
}

export default async function ClaimInspector({ params }: { params: Promise<{ id: string }> }) {
  // params is a Promise in Next 16. The route segment was previously ignored entirely,
  // so every claim id rendered the same hardcoded page.
  const { id } = await params;
  const claim = await readFromApi<Claim>(`/claims/${id}`);
  if (!claim) notFound();

  const [provenance, verification] = await Promise.all([
    readFromApi<Provenance>(`/claims/${id}/provenance`),
    readFromApi<Verification>(`/claims/${id}/verification`),
  ]);

  const network = networkInfo();
  const standing = claimStanding(claim.status);
  const verified = standing.verified;
  const verifier = claim.attestations.find(
    (item) => item.type === "INDEPENDENT_VERIFIER" && item.status === "CONFIRMED",
  );
  const proofTx = verifier?.transactionHash ?? null;

  return (
    <div className="inspector">
      <div className="breadcrumbs">
        <Link href="/">Programs</Link> <span aria-hidden>/</span> {claim.projectId}{" "}
        <span aria-hidden>/</span> Claim
      </div>

      <section className="claimHero">
        <div>
          <span className="eyebrow">IMPACT CLAIM</span>
          <h1>“{claim.statement}”</h1>
          <p>
            Policy version {claim.policyVersion}
            {claim.verifiedAt ? ` · verified ${claim.verifiedAt}` : ""}
          </p>
        </div>
        <Status kind={standing.tone}>{standing.badge}</Status>
      </section>

      <div className="contentGrid">
        <div>
          <section className="panel">
            <div className="panelHead">
              <div>
                <span className="eyebrow">CHAIN OF PROVENANCE</span>
                <h2>Follow the claim to its source</h2>
              </div>
              <span className="subtle">
                {provenance ? `${provenance.nodes.length} records · ${provenance.edges.length} relationships` : ""}
              </span>
            </div>
            {provenance ? (
              <>
                <ProvenanceGraph graph={provenance} />
                <p className="note">
                  These relationships are recorded in ImpactGraph&rsquo;s own database. What
                  is committed to {network.name} is the evidence commitment and the
                  verifier&rsquo;s attestation &mdash; not the links between records.
                </p>
              </>
            ) : (
              <p className="subtle">Provenance is unavailable.</p>
            )}
          </section>

          <section className="panel">
            <div className="panelHead">
              <div>
                <span className="eyebrow">SUPPORTING EVIDENCE</span>
                <h2>{supportingEvidenceSentence(claim.evidenceIds.length)}</h2>
              </div>
            </div>
            <ul className="checks">
              {claim.evidenceIds.map((evidenceId) => (
                <li key={evidenceId}>
                  <Link href={`/evidence/${evidenceId}`}>Inspect {evidenceId}</Link>
                </li>
              ))}
            </ul>
            <p className="note">
              Integrity is checked on the evidence page, by re-reading the stored object and
              re-hashing it.
            </p>
          </section>

          <section className="panel">
            <div className="panelHead">
              <div>
                <span className="eyebrow">ATTESTATIONS</span>
                <h2>Who asserted what</h2>
              </div>
            </div>
            {claim.attestations.length === 0 && <p className="subtle">No attestations yet.</p>}
            <div className="reviewFacts">
              {claim.attestations.map((attestation) => (
                <div key={attestation.id}>
                  <Status
                    kind={
                      attestation.status === "CONFIRMED" && attestation.onchain
                        ? "verified"
                        : "pending"
                    }
                  >
                    {attestation.onchain ? "Confirmed onchain" : "Recorded"}
                  </Status>
                  <span>
                    {attestation.issuer}
                    <small>
                      {attestation.type === "INDEPENDENT_VERIFIER"
                        ? "Independent verifier"
                        : "Operator"}
                      {attestation.wallet
                        ? ` · ${attestation.wallet}`
                        : " · asserted in ImpactGraph, not signed on a chain"}
                    </small>
                  </span>
                </div>
              ))}
            </div>
          </section>
        </div>

        <aside>
          <section className="score panel">
            <span className="eyebrow">EVIDENCE CONFIDENCE</span>
            {verification ? (
              <>
                {/* Plain type, not a dial. A ring implies a measurement; this is a
                    weighted opinion, and the breakdown beneath is the honest artefact. */}
                <p className="scoreTotal">
                  <strong>{verification.evidenceScore.total}</strong>
                  <span>out of 100</span>
                </p>
                <ScoreGap components={verification.evidenceScore.components} />
                {verification.evidenceScore.components.map((component) => (
                  <div className="scoreRow" key={component.component}>
                    <span>{SCORE_LABELS[component.component] ?? component.component}</span>
                    <b>
                      {component.score}/{component.maximum}
                    </b>
                  </div>
                ))}
              </>
            ) : (
              <p className="subtle">Score unavailable.</p>
            )}
          </section>

          <section className="panel">
            <span className="eyebrow">VERIFICATION POLICY</span>
            <h3>
              {standing.challenged
                ? "Why this claim is no longer verified"
                : `Why this claim is ${verified ? "verified" : "not yet verified"}`}
            </h3>
            <ul className="checks">
              {verification?.requirements.map((requirement) => (
                <li
                  key={requirement.requirement}
                  className={REQUIREMENT_TONE[requirement.status] ?? "fail"}
                >
                  <b aria-hidden>{REQUIREMENT_MARK[requirement.status] ?? "×"}</b>
                  <span>
                    {REQUIREMENT_LABELS[requirement.requirement] ?? requirement.requirement}
                    <small>{requirement.reason}</small>
                  </span>
                </li>
              ))}
            </ul>
          </section>

          <section className="panel proof">
            <span className="eyebrow">BLOCKCHAIN PROOF</span>
            <h3>
              {verified && network.isPublicEthereum
                ? "Verified on Ethereum"
                : `Committed on ${network.name}`}
            </h3>
            <dl>
              <dt>Network</dt>
              <dd>{network.name}</dd>
              <dt>Claim ID</dt>
              <dd>{claim.id}</dd>
              <dt>Contract</dt>
              <dd className="hash">
                {network.registryAddress ? (
                  <ExplorerLink
                    href={explorerLink(network.explorerBase, "address", network.registryAddress)}
                    label={network.registryAddress}
                  />
                ) : (
                  "Not configured"
                )}
              </dd>
              <dt>Verification bundle</dt>
              <dd className="hash">{claim.verificationBundleHash}</dd>
              {proofTx && (
                <>
                  <dt>Attestation transaction</dt>
                  <dd className="hash">
                    <ExplorerLink
                      href={explorerLink(network.explorerBase, "tx", proofTx)}
                      label={proofTx}
                    />
                  </dd>
                </>
              )}
            </dl>
            <p className="note">
              {network.isPublicEthereum
                ? "“Verified on Ethereum” means a verifier attestation and its evidence bundle are confirmed there — not that Ethereum observed the outcome."
                : "This is not a public Ethereum network, so nothing here is Ethereum verification."}
            </p>
          </section>
        </aside>
      </div>
    </div>
  );
}

function ExplorerLink({ href, label }: { href: string | null; label: string }) {
  return href ? (
    <a href={href} target="_blank" rel="noreferrer">
      {label}
    </a>
  ) : (
    <>{label}</>
  );
}

/** Why the score is what it is, named rather than left for the reader to work out. */
function ScoreGap({ components }: { components: Verification["evidenceScore"]["components"] }) {
  const worst = largestScoreGap(components);
  if (!worst) return <p className="scoreWhy">Every component is complete.</p>;
  const label = SCORE_LABELS[worst.component] ?? worst.component;
  return (
    <p className="scoreWhy">
      {worst.score} of {worst.maximum} for {label.toLowerCase()} — the largest single gap.
    </p>
  );
}
