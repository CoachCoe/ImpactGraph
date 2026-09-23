import Link from "next/link";
import { ImportStatement } from "@/components/ImportStatement";
import { Status } from "@/components/Status";
import { readFromApi } from "@/lib/api";
import { formatMoney } from "@/lib/money";
import type { FinancialSummary } from "@/lib/types";

const PROGRAM_ID = "program-clean-water-kenya-2026";

const MATCH_LABEL: Record<string, string> = {
  MATCHED: "Evidenced",
  PARTIAL_MATCH: "Evidenced — with one thing to look at",
  UNMATCHED: "Nothing filed for this payment yet",
  CONFLICT: "Evidence disagrees",
};

function matchKind(status: string) {
  if (status === "MATCHED") return "verified" as const;
  if (status === "PARTIAL_MATCH") return "warning" as const;
  if (status === "CONFLICT") return "failed" as const;
  return "pending" as const;
}

export default async function FinancialPage() {
  const summary = await readFromApi<FinancialSummary>(`/financial/programs/${PROGRAM_ID}`);
  if (!summary) {
    return (
      <div className="inspector">
        <section className="panel">
          <h2>The financial ledger is unavailable</h2>
          <p className="subtle">
            This page reads live records. Start the API with <code>make api</code> after
            <code>make db-up &amp;&amp; make migrate &amp;&amp; make seed</code>.
          </p>
        </section>
      </div>
    );
  }

  return (
    <div className="inspector">
      <section className="claimHero">
        <div>
          <span className="eyebrow">MONEY TRAIL</span>
          <h1>Where the money went</h1>
          <p>
            Payments are observed, not initiated: ImpactGraph imports what the financial
            data provider reports and checks it against what was allocated.
          </p>
        </div>
      </section>

      <section className="metrics financialMetrics" aria-label="Financial summary">
        <article>
          <span>Received</span>
          <strong>{formatMoney(summary.received, { maximumFractionDigits: 0 })}</strong>
          <small>From funders</small>
        </article>
        <article>
          <span>Committed</span>
          <strong>{formatMoney(summary.committed, { maximumFractionDigits: 0 })}</strong>
          <small>{formatMoney(summary.uncommitted, { maximumFractionDigits: 0 })} uncommitted</small>
        </article>
        <article>
          <span>Spent</span>
          <strong>{formatMoney(summary.spent, { maximumFractionDigits: 0 })}</strong>
          <small>{formatMoney(summary.unspent, { maximumFractionDigits: 0 })} unspent</small>
        </article>
        <article>
          <span>Evidenced spend</span>
          <strong>
            {(summary.matchCounts.MATCHED ?? 0) + (summary.matchCounts.PARTIAL_MATCH ?? 0)}/
            {summary.transactions.length}
          </strong>
          <small>Payments with matching evidence</small>
        </article>
      </section>

      <div className="contentGrid">
        <div>
          <section className="panel">
            <div className="panelHead">
              <div>
                <span className="eyebrow">OBSERVED PAYMENTS</span>
                <h2>Imported from {summary.transactions[0]?.provider ?? "the provider"}</h2>
              </div>
            </div>
            <div className="ledger">
              {summary.transactions.map((transaction) => (
                <article key={transaction.id}>
                  <div className="ledgerHead">
                    <span>
                      <b>{transaction.payee}</b>
                      <small>
                        {transaction.occurredOn} · {transaction.id} · ref{" "}
                        {transaction.sourceRef}
                      </small>
                    </span>
                    <b className="ledgerAmount">{formatMoney(transaction.amount)}</b>
                  </div>
                  <div className="ledgerFoot">
                    <Status kind={matchKind(transaction.matchStatus)}>
                      {MATCH_LABEL[transaction.matchStatus] ?? transaction.matchStatus}
                    </Status>
                    <span className="subtle">{transaction.memo}</span>
                  </div>
                </article>
              ))}
            </div>
            <p className="note">
              A payment with no supporting evidence is shown rather than omitted. Reported
              spend that is not evidenced is the thing a reader most needs to see.
            </p>
          </section>

          <ImportStatement programId={PROGRAM_ID} />
        </div>

        <aside>
          <section className="panel">
            <span className="eyebrow">FUNDING</span>
            {summary.funding.map((item) => (
              <Link className="scoreRow" href={`/funding/${item.id}`} key={item.id}>
                <span>
                  {item.funder}
                  <small>{item.receivedOn} · follow this contribution →</small>
                </span>
                <b>{formatMoney(item.amount, { maximumFractionDigits: 0 })}</b>
              </Link>
            ))}
          </section>

          <section className="panel">
            <span className="eyebrow">ALLOCATIONS</span>
            <h3>Budget against spend</h3>
            {summary.allocations.map((allocation) => {
              const used = allocation.amount.amountMinor
                ? Math.min(
                    100,
                    Math.round(
                      (allocation.spent.amountMinor / allocation.amount.amountMinor) * 100,
                    ),
                  )
                : 0;
              return (
                <div key={allocation.id} className="allocation">
                  <div className="scoreRow">
                    <span>{allocation.purpose}</span>
                    <b>{formatMoney(allocation.amount, { maximumFractionDigits: 0 })}</b>
                  </div>
                  <div
                    className="meter"
                    role="meter"
                    aria-valuenow={used}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-label={`${allocation.purpose} budget used`}
                  >
                    <span style={{ width: `${used}%` }} />
                  </div>
                  <p className="note">
                    {formatMoney(allocation.spent)} spent · {formatMoney(allocation.remaining)}{" "}
                    remaining. Imports beyond the remaining balance are rejected.
                  </p>
                </div>
              );
            })}
          </section>

          <section className="panel">
            <span className="eyebrow">PROVENANCE</span>
            <p className="subtle">
              Each payment supports a delivery, which supports an outcome, which supports
              the claim.
            </p>
            <Link className="button full" href="/claims/claim-water-12-200">
              Follow it to the claim <span aria-hidden>→</span>
            </Link>
          </section>

          <section className="panel">
            <span className="eyebrow">TAKE IT WITH YOU</span>
            <h2>Check it somewhere else</h2>
            <p className="subtle">
              Everything above can be read in your own tools. Rows carry the hashes needed
              to check them against the registry, so nothing here has to be taken on our
              word.
            </p>
            {/* Plain links rather than fetches: the browser saves the file, and the
                endpoints are public, so no session is involved. */}
            <a className="secondary full" href={`/api/export/programs/${PROGRAM_ID}/money-trail.csv`}>
              Money trail (CSV)
            </a>
            <a className="secondary full" href={`/api/export/programs/${PROGRAM_ID}/outcomes.csv`}>
              Outcomes and their methods (CSV)
            </a>
          </section>
        </aside>
      </div>
    </div>
  );
}
