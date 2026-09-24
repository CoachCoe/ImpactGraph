"use client";

import Link from "next/link";

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div className="workspace">
      <section className="panel">
        <span className="eyebrow">SOMETHING BROKE</span>
        <h1>This page did not load</h1>
        <p className="subtle">
          The record itself is unaffected: nothing here writes on read, so a failure to
          display something has not changed it. Try again, and if it keeps happening the
          reference below identifies what went wrong.
        </p>
        {error.digest ? (
          <p className="note">
            Reference <span className="hash">{error.digest}</span>
          </p>
        ) : null}
        <div className="actionRow">
          <button className="button" onClick={reset}>
            Try again
          </button>
          <Link className="secondary" href="/">
            Back to the record
          </Link>
        </div>
      </section>
    </div>
  );
}
