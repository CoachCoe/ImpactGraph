import type { Metadata } from "next";
import Link from "next/link";
import { Status } from "@/components/Status";
import { networkInfo } from "@/lib/network";
import { readProof, requirementLabel } from "@/lib/proof";
import { claimStanding } from "@/lib/sentences";

type Params = { params: Promise<{ id: string }> };

/**
 * Social metadata from the claim itself.
 *
 * The status is read at request time like everything else on this page. A card cached by
 * a social platform saying "Independently verified" after a claim was challenged is the
 * failure this product cannot afford, so the description states the status in words
 * rather than leaving it to an image nobody regenerates.
 */
export async function generateMetadata({ params }: Params): Promise<Metadata> {
  const found = await readProof((await params).id);
  if (found.state !== "ok") {
    return { title: "Proof not found — ImpactGraph", robots: { index: false } };
  }
  const { claim, operator } = found.data;
  const standing = claimStanding(claim.status);
  const title = `${operator.name} — ${standing.badge}`;
  const description = `${claim.statement} Verified against evidence committed to a public blockchain, and checkable without an account.`;
  return {
    title,
    description,
    openGraph: { title, description, type: "article" },
    twitter: { card: "summary_large_image", title, description },
  };
}

export default async function ProofPage({ params }: Params) {
  const { id } = await params;
  const found = await readProof(id);
  if (found.state === "unavailable") return <Unavailable />;
  if (found.state === "missing") return <NotPublished />;

  const {
    claim,
    operator,
    requirements,
    attestations,
    onchain,
    proves,
    doesNotProve,
  } = found.data;
  const standing = claimStanding(claim.status);
  const network = networkInfo();

  return (
    <div className="proof">
      <section className="proofHead">
        <span className="eyebrow">{operator.program}</span>
        <h1>{claim.statement}</h1>
        <p className="proofBy">
          {operator.name}
          {operator.region ? ` · ${operator.region}` : ""}
        </p>
        <Status kind={standing.tone}>{standing.badge}</Status>
      </section>

      <section className="panel">
        <h2>What was checked</h2>
        <ul className="checks">
          {requirements.map((item) => (
            <li
              key={item.requirement}
              className={item.status === "PASS" ? "pass" : "warning"}
            >
              <b aria-hidden>{item.status === "PASS" ? "✓" : "!"}</b>
              <span>
                {requirementLabel(item.requirement)}
                <small>{item.reason}</small>
              </span>
            </li>
          ))}
        </ul>
      </section>

      <section className="panel">
        <h2>Who attested, and to what</h2>
        <dl className="proofFacts">
          <div>
            <dt>Verification bundle</dt>
            <dd className="hash">
              {claim.verificationBundleHash ?? "Not yet bundled"}
            </dd>
          </div>
          <div>
            <dt>Claim commitment</dt>
            <dd className="hash">{claim.payloadHash}</dd>
          </div>
          {attestations.map((item) => (
            <div key={item.id}>
              <dt>
                {item.type === "OPERATOR" ? "Operator" : "Independent verifier"}
              </dt>
              <dd>
                {item.issuer}
                {item.wallet ? (
                  <span className="hash">{item.wallet}</span>
                ) : null}
              </dd>
            </div>
          ))}
        </dl>
        {onchain?.transactionHash ? (
          <p className="note">
            Signed on {network.name}. The attestation transaction is{" "}
            <span className="hash">{onchain.transactionHash}</span>.
          </p>
        ) : (
          <p className="note">
            No independent attestation has been signed on a chain for this claim
            yet.
          </p>
        )}
      </section>

      <section className="contentGrid">
        <div className="panel">
          <h2>What this proves</h2>
          <ul className="plainList">
            {proves.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
        <div className="panel proofLimits">
          <h2>What it does not</h2>
          <ul className="plainList">
            {doesNotProve.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      </section>

      <section className="panel">
        <h2>Check it yourself</h2>
        <p className="subtle">
          Nothing here asks you to take our word for it. Follow the evidence to
          the documents, re-run the integrity check, and read the method.
        </p>
        <div className="uploadActions">
          <Link className="button" href={`/claims/${claim.id}`}>
            Follow the evidence <span aria-hidden>→</span>
          </Link>
          <Link className="secondary" href="/about">
            How verification works
          </Link>
        </div>
      </section>

      {/* Printed for a grant application or a board pack. The hashes and the address of
          this page go with it, so a printed copy stays checkable rather than becoming a
          claim on paper that nobody can follow back. */}
      <section className="panel printOnly">
        <h2>Checking this from a printed copy</h2>
        <dl className="proofFacts">
          <div>
            <dt>This page</dt>
            <dd className="hash">/proof/{claim.id}</dd>
          </div>
          <div>
            <dt>Claim commitment</dt>
            <dd className="hash">{claim.payloadHash}</dd>
          </div>
          <div>
            <dt>Verification bundle</dt>
            <dd className="hash">
              {claim.verificationBundleHash ?? "Not yet bundled"}
            </dd>
          </div>
          {onchain?.transactionHash ? (
            <div>
              <dt>Attestation transaction</dt>
              <dd className="hash">{onchain.transactionHash}</dd>
            </div>
          ) : null}
        </dl>
        <p className="note">
          Printed from a page that shows live status. Open the address above to
          see what it says now.
        </p>
      </section>

      <EmbedBadge claimId={claim.id} statement={claim.statement} />

      <p className="proofFooter">
        Verified on <Link href="/">ImpactGraph</Link> · this page shows the
        claim&rsquo;s current status, not the status on the day it was shared.
      </p>
    </div>
  );
}

function EmbedBadge({
  claimId,
  statement,
}: {
  claimId: string;
  statement: string;
}) {
  const base = process.env.NEXT_PUBLIC_API_URL ?? "";
  const snippet = `<a href="${base}/proof/${claimId}"><img src="${base}/api/claims/${claimId}/badge.svg" alt="${statement.replace(/"/g, "&quot;")}" height="44" /></a>`;
  return (
    <section className="panel">
      <h2>Put this on your own site</h2>
      <p className="subtle">
        One line of HTML. The badge shows this claim&rsquo;s status at the time
        someone loads your page &mdash; so if the verification is ever
        withdrawn, every copy of it says so within five minutes. That is the
        point: a badge that could not stop saying &ldquo;verified&rdquo; would
        not be worth putting up.
      </p>
      <pre className="embedSnippet">
        <code>{snippet}</code>
      </pre>
      <img
        className="embedPreview"
        src={`/api/claims/${claimId}/badge.svg`}
        alt=""
        height={44}
      />
    </section>
  );
}

function NotPublished() {
  return (
    <section className="section">
      <div className="panel">
        <h2>No public proof for this claim</h2>
        <p className="subtle">
          A claim becomes a public proof only when the organisation behind it
          chooses to publish one. There may be nothing here because that choice
          has not been made.
        </p>
      </div>
    </section>
  );
}

function Unavailable() {
  return (
    <section className="section">
      <div className="panel">
        <h2>The transparency API is unavailable</h2>
        <p className="subtle">
          This page reads live data rather than rendering a fixed copy, so there
          is nothing to show until the API is reachable.
        </p>
      </div>
    </section>
  );
}
