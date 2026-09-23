import Link from "next/link";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "No such record — ImpactGraph",
  robots: { index: false },
};

export default function NotFound() {
  return (
    <div className="workspace">
      <section className="panel">
        <span className="eyebrow">NOT FOUND</span>
        <h1>There is no record at this address</h1>
        <p className="subtle">
          Either the identifier is wrong, or it names something this organisation has not
          published. Nothing has been withdrawn — a claim that was published stays
          published, so a missing page is a bad link rather than a removed record.
        </p>
        <div className="riskActions">
          <Link className="button" href="/">
            Back to the record
          </Link>
          <Link className="secondary" href="/about">
            How this works
          </Link>
        </div>
      </section>
    </div>
  );
}
