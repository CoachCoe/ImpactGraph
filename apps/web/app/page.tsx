import Link from "next/link";
import { Status } from "@/components/Status";
import { readFromApi } from "@/lib/api";
import { resolveProgram } from "@/lib/programs";
import type { Claim, FinancialSummary, FundingAttribution, Program } from "@/lib/types";
import { attestationCount, claimStanding, programSentence } from "@/lib/sentences";

const money = (minor: number, currency: string) =>
  new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    maximumFractionDigits: 0,
  }).format(minor / 100);

export default async function DonorDashboard({
  searchParams,
}: {
  searchParams: Promise<{ program?: string }>;
}) {
  const chosen = await resolveProgram((await searchParams).program);
  if (chosen.state === "unavailable") return <ApiUnavailable />;
  if (chosen.state === "none") return <NoPrograms />;
  if (chosen.state === "choose") return <ChooseProgram programs={chosen.programs} />;
  const program = chosen.program;

  const claim = await readFromApi<Claim>(`/claims/${program.featuredClaimId}`);
  const financial = await readFromApi<FinancialSummary>(`/financial/programs/${program.id}`);
  const contributions = financial
    ? (await Promise.all(
        financial.funding.map((item) =>
          readFromApi<FundingAttribution>(`/financial/funding/${item.id}/attribution`),
        ),
      )).filter((item): item is FundingAttribution => item !== null)
    : [];
  const contribution =
    contributions.find((item) =>
      item.allocations.some((allocation) =>
        allocation.transactions.some((transaction) =>
          transaction.deliveries.some((delivery) => delivery.outcomes.length > 0),
        ),
      ),
    ) ??
    contributions.find((item) =>
      item.allocations.some((allocation) => allocation.transactions.length > 0),
    ) ??
    contributions[0] ??
    null;
  const standing = claimStanding(claim?.status ?? "");
  const verified = standing.verified;
  // The read model exposes one featured claim per program, so this counts what is
  // actually known rather than implying a wider set that is not fetched here.
  const totalClaims = claim ? 1 : 0;
  const verifiedClaims = verified ? 1 : 0;

  return (
    <>
      {contribution ? (
        <ContributionFrontDoor contribution={contribution} claim={claim} />
      ) : (
        <ProgramFrontDoor program={program} verified={verified} />
      )}

      <section className="metrics" aria-label="Program summary">
        <article>
          <span>Program funding</span>
          <strong>{money(program.funding.amountMinor, program.funding.currency)}</strong>
          <small>Recorded funding</small>
        </article>
        <article>
          <span>Program spend</span>
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
          <strong>{verifiedClaims} of {totalClaims}</strong>
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
                  <Link className="secondary" href={`/financial?program=${program.id}`}>
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

function ContributionFrontDoor({
  contribution,
  claim,
}: {
  contribution: FundingAttribution;
  claim: Claim | null;
}) {
  const allocation = contribution.allocations[0];
  const delivery = allocation?.transactions[0]?.deliveries[0];
  const outcome = delivery?.outcomes[0];
  const attributedClaim = outcome?.claims[0];
  const status = attributedClaim?.status ?? claim?.status ?? "VERIFICATION_PENDING";
  const standing = claimStanding(status);

  return (
    <section className="hero contributionHero">
      <div className="eyebrow">FOLLOW ONE CONTRIBUTION</div>
      <h1>
        <Link href={`/funding/${contribution.fundingId}`}>
          {money(contribution.received.amountMinor, contribution.received.currency)} received
        </Link>
        <span aria-hidden> → </span>
        {allocation ? `${money(allocation.amount.amountMinor, allocation.amount.currency)} committed` : "not yet committed"}
        <span aria-hidden> → </span>
        {delivery ? `${delivery.quantity.toLocaleString()} ${delivery.item}` : "delivery not yet recorded"}
        <span aria-hidden> → </span>
        {outcome ? `${outcome.value.toLocaleString()} ${outcome.unit}` : "outcome not yet recorded"}
      </h1>
      <p>
        This is one recorded contribution, not an estimate or a share of the whole program.
        Its trail names what was allocated, paid, delivered and claimed. The current claim is{" "}
        <strong>{standing.badge.toLowerCase()}</strong>.
      </p>
      <Link className="button" href={`/funding/${contribution.fundingId}`}>
        Inspect every record <span aria-hidden>→</span>
      </Link>
    </section>
  );
}

function ProgramFrontDoor({ program, verified }: { program: Program; verified: boolean }) {
  return (
    <section className="hero">
      <div className="eyebrow">THE RECORD SO FAR</div>
      <h1>
        {programSentence(program).map((segment, index) =>
          segment.link ? (
            <Link
              key={index}
              href={segment.link === "money"
                ? `/financial?program=${program.id}`
                : `/claims/${program.featuredClaimId}`}
            >
              {segment.text}
            </Link>
          ) : (
            <span key={index}>{segment.text}</span>
          ),
        )}
      </h1>
      <p>
        No individual contribution is available to trace yet. The program-wide figures
        below remain linked to their records.
        {verified ? "" : " Independent verification of the featured claim is still pending."}
      </p>
    </section>
  );
}

function ChooseProgram({ programs }: { programs: Program[] }) {
  return (
    <section className="section">
      <div className="sectionHead">
        <div>
          <span className="eyebrow">PROGRAMS</span>
          <h2>Choose a program</h2>
          <p>Each one carries its own record, funding and evidence.</p>
        </div>
      </div>
      <div className="programList">
        {programs.map((item) => (
          <Link key={item.id} className="programRow" href={`/?program=${item.id}`}>
            <span>
              <b>{item.name}</b>
              <small>
                {item.operator} · {item.region}
              </small>
            </span>
            <Status kind={item.status === "ACTIVE" ? "verified" : "pending"}>{item.status}</Status>
          </Link>
        ))}
      </div>
    </section>
  );
}

function NoPrograms() {
  return (
    <section className="section">
      <div className="panel">
        <h2>No programs yet</h2>
        <p className="subtle">
          Nothing has been recorded to show. An operator creates the first program, and it
          becomes readable here once its registry entity is confirmed on chain.
        </p>
      </div>
    </section>
  );
}

function ApiUnavailable() {
  return (
    <section className="section">
      <div className="panel">
        <h2>The live transparency record is temporarily unavailable</h2>
        <p className="subtle">
          No cached figures are shown because this page only presents records it can read
          and check now. Please try again shortly.
        </p>
      </div>
    </section>
  );
}
