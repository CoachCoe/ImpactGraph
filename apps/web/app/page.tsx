import Link from "next/link";
import { Status } from "@/components/Status";
import { readFromApi } from "@/lib/api";
import type { Claim, Program, Verification } from "@/lib/types";
import { attestationCount, claimStanding, programSentence } from "@/lib/sentences";

const PROGRAM_ID = "program-clean-water-kenya-2026";

const money = (minor: number, currency: string) =>
  new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    maximumFractionDigits: 0,
  }).format(minor / 100);

export default async function DonorDashboard() {
  const program = await readFromApi<Program>(`/programs/${PROGRAM_ID}`);
  if (!program) return <ApiUnavailable />;

  const claim = await readFromApi<Claim>(`/claims/${program.featuredClaimId}`);
  const verification = claim
    ? await readFromApi<Verification>(`/claims/${claim.id}/verification`)
    : null;
  const standing = claimStanding(claim?.status ?? "");
  const verified = standing.verified;
  // The read model exposes one featured claim per program, so this counts what is
  // actually known rather than implying a wider set that is not fetched here.
  const totalClaims = claim ? 1 : 0;
  const verifiedClaims = verified ? 1 : 0;

  return (
    <>
      <section className="hero">
        <div className="eyebrow">THE RECORD SO FAR</div>
        <h1>
          {programSentence(program).map((segment, index) =>
            segment.link ? (
              <Link
                key={index}
                href={segment.link === "money" ? "/financial" : `/claims/${program.featuredClaimId}`}
              >
                {segment.text}
              </Link>
            ) : (
              <span key={index}>{segment.text}</span>
            ),
          )}
        </h1>
        <p>
          Good intentions deserve visible proof. Follow the money to what it bought, and
          check the record yourself &mdash; every figure above leads to its own records.
          {verified
            ? ""
            : " Independent verification of the featured claim is still pending."}
        </p>
      </section>

      <section className="metrics" aria-label="Program summary">
        <article>
          <span>Funded</span>
          <strong>{money(program.funding.amountMinor, program.funding.currency)}</strong>
          <small>Recorded funding</small>
        </article>
        <article>
          <span>Deployed</span>
          <strong>{money(program.deployed.amountMinor, program.deployed.currency)}</strong>
          <small>Observed spend</small>
        </article>
        <article>
          <span>People served</span>
          <strong>{program.peopleServed.toLocaleString()}</strong>
          <small>Across {program.filtrationSystems} systems</small>
        </article>
        <article>
          <span>Independently verified</span>
          <strong>
            {verifiedClaims} of {totalClaims}
          </strong>
          <small>{totalClaims === 1 ? "Featured claim" : "Claims in this program"}</small>
        </article>
      </section>

      <section className="section">
        <div className="sectionHead">
          <div>
            <span className="eyebrow">FEATURED PROGRAM</span>
            <h2>{program.name}</h2>
            <p>
              {program.operator} · {program.region}
            </p>
          </div>
          <Status kind={standing.tone}>{standing.badge}</Status>
        </div>

        <div className="programCard">
          <div className="programVisual">
            <div className="waterIcon" aria-hidden>
              ⌁
            </div>
            <span>{program.region.toUpperCase()}</span>
          </div>
          <div className="programBody">
            <div className="miniMetrics">
              <span>
                <b>{money(program.funding.amountMinor, program.funding.currency)}</b>funded
              </span>
              <span>
                <b>{money(program.deployed.amountMinor, program.deployed.currency)}</b>deployed
              </span>
              <span>
                <b>{program.filtrationSystems}</b>filtration systems
              </span>
              <span>
                <b>{program.peopleServed.toLocaleString()}</b>people served
              </span>
            </div>
            {claim && (
              <>
                <blockquote>“{claim.statement}”</blockquote>
                <p className="trustLine">
                  {attestationCount(
                    claim.attestations.filter((item) => item.status === "CONFIRMED").length,
                  )}{" "}
                  <i>•</i>{" "}
                  {verified ? "independently verified" : "independent verification pending"}
                </p>
                <div className="uploadActions">
                  <Link className="button" href={`/claims/${claim.id}`}>
                    Follow the evidence <span aria-hidden>→</span>
                  </Link>
                  <Link className="secondary" href="/financial">
                    See where the money went
                  </Link>
                </div>
              </>
            )}
          </div>
        </div>
      </section>
    </>
  );
}

function ApiUnavailable() {
  return (
    <section className="section">
      <div className="panel">
        <h2>The transparency API is unavailable</h2>
        <p className="subtle">
          This page reads live data rather than rendering a fixed copy, so there is nothing
          to show until the API is reachable. Start it with <code>make api</code> after
          <code>make db-up &amp;&amp; make migrate &amp;&amp; make seed</code>.
        </p>
      </div>
    </section>
  );
}
