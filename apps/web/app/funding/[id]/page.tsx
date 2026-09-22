import Link from "next/link";
import { notFound } from "next/navigation";
import { Status } from "@/components/Status";
import { readFromApi } from "@/lib/api";
import { formatMoney } from "@/lib/money";
import type { FundingAttribution } from "@/lib/types";

const CLAIM_TONE: Record<string, "verified" | "warning" | "failed" | "pending"> = {
  VERIFIED: "verified",
  CHALLENGED: "warning",
  REJECTED: "failed",
  REVOKED: "failed",
};

export default async function FundingAttributionPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const trail = await readFromApi<FundingAttribution>(`/financial/funding/${id}/attribution`);

  // readFromApi returns null for a 404 and for an unreachable API alike, and answering
  // 200 to a request for a record that does not exist is its own small dishonesty.
  if (!trail) notFound();

  const idle = trail.uncommitted.amountMinor;

  return (
    <div className="inspector">
      <div className="breadcrumbs">
        <Link href="/financial">Money trail</Link>
        <span>›</span>
        {trail.funder}
      </div>

      <div className="claimHero">
        <div>
          <span className="eyebrow">WHAT THIS CONTRIBUTION REACHED</span>
          <h1>
            {trail.funder} gave {formatMoney(trail.received)}
          </h1>
          <p>
            Received {trail.receivedOn}. Every figure below is this contribution&rsquo;s own,
            not a share of the program.
          </p>
        </div>
      </div>

      <section className="metrics compact" aria-label="Contribution summary">
        <article>
          <span>Received</span>
          <strong>{formatMoney(trail.received)}</strong>
        </article>
        <article>
          <span>Committed</span>
          <strong>{formatMoney(trail.committed)}</strong>
        </article>
        <article>
          <span>Spent</span>
          <strong>{formatMoney(trail.spent)}</strong>
        </article>
        <article>
          <span>{idle > 0 ? "Not yet committed" : "Committed beyond funding"}</span>
          <strong>
            {formatMoney(idle > 0 ? trail.uncommitted : trail.overcommitted)}
          </strong>
        </article>
      </section>

      {trail.allocations.length === 0 ? (
        <section className="panel">
          <h2>None of this money has been committed yet</h2>
          <p className="subtle">
            The contribution is recorded and nothing has been allocated against it. There is
            nothing further to show, which is itself the honest answer.
          </p>
        </section>
      ) : null}

      {trail.allocations.map((allocation) => (
        <section className="panel" key={allocation.id}>
          <div className="panelHead">
            <div>
              <span className="eyebrow">ALLOCATION</span>
              <h2>{allocation.purpose}</h2>
            </div>
            <Status kind={allocation.spent.amountMinor > 0 ? "verified" : "pending"}>
              {formatMoney(allocation.spent)} of {formatMoney(allocation.amount)} spent
            </Status>
          </div>
          <div className="evidenceSummary">
            <div>
              <small>SPENT</small>
              <b>{formatMoney(allocation.spent)}</b>
            </div>
            <div>
              <small>{allocation.overspent.amountMinor > 0 ? "OVERSPENT" : "STILL UNSPENT"}</small>
              <b>
                {formatMoney(
                  allocation.overspent.amountMinor > 0
                    ? allocation.overspent
                    : allocation.unspent,
                )}
              </b>
            </div>
          </div>

          {allocation.transactions.length === 0 ? (
            <p className="subtle">Nothing has been paid out of this allocation yet.</p>
          ) : null}

          {allocation.transactions.map((transaction) => (
            <details key={transaction.id} open>
              <summary>
                {formatMoney(transaction.amount)} to {transaction.payee} on{" "}
                {transaction.occurredOn}
              </summary>
              {transaction.deliveries.length === 0 ? (
                <p className="subtle">
                  No delivery is recorded against this payment yet.
                </p>
              ) : null}
              {transaction.deliveries.map((delivery) => (
                <div className="reviewFacts" key={delivery.id}>
                  <div>
                    <span>
                      {delivery.quantity} × {delivery.item}
                      <small>Delivered {delivery.deliveredOn}</small>
                    </span>
                  </div>
                  {delivery.outcomes.map((outcome) => (
                    <div key={outcome.id}>
                      <span>
                        {outcome.value.toLocaleString()} {outcome.unit} — {outcome.metric}
                        <small>{outcome.region}</small>
                      </span>
                      <span>
                        {outcome.claims.map((claim) => (
                          <Link href={`/claims/${claim.id}`} key={claim.id}>
                            <Status kind={CLAIM_TONE[claim.status] ?? "pending"}>
                              {claim.status}
                            </Status>
                          </Link>
                        ))}
                      </span>
                    </div>
                  ))}
                </div>
              ))}
            </details>
          ))}
        </section>
      ))}

      <section className="panel">
        <span className="eyebrow">HOW THIS WAS WORKED OUT</span>
        <h2>What this figure does and does not mean</h2>
        <p className="subtle">{trail.method.explanation}</p>
        <p className="subtle">
          Money is fungible once it is in a bank account. This page does not claim a
          particular pound bought a particular thing; it shows the allocations that draw on
          this contribution, and everything recorded downstream of them.
        </p>
      </section>
    </div>
  );
}
