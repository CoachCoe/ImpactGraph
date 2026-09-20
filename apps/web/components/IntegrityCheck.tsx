"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { IntegrityResult } from "@/lib/types";
import { Status } from "@/components/Status";
import { integritySentence } from "@/lib/sentences";

/** The review's ceiling. Past this the comparison stops reading as work. */
const SWEEP_MS = 480;

const reducedMotion = () =>
  typeof window !== "undefined" &&
  typeof window.matchMedia === "function" &&
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/**
 * Runs the integrity check against the API.
 *
 * The result is whatever the backend computes by reading the stored object back and
 * rehashing it. Nothing here decides the outcome; a badge asserting "integrity confirmed"
 * without asking would prove nothing.
 *
 * On a match the two hashes are compared character by character in the open. The sweep is
 * the one animated thing in this product, and it animates a comparison that has already
 * happened: it is started by the resolved response, never by a timer, so a check that
 * returns in four milliseconds still drives it. It never runs on the failure path -- a
 * mismatch is not a moment to draw out.
 */
export function IntegrityCheck({
  evidenceId,
  documentType,
}: {
  evidenceId: string;
  documentType?: string;
}) {
  const [result, setResult] = useState<IntegrityResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [swept, setSwept] = useState(0);
  const frame = useRef<number | null>(null);

  useEffect(
    () => () => {
      if (frame.current !== null) cancelAnimationFrame(frame.current);
    },
    [],
  );

  const sweep = (total: number) => {
    const began = performance.now();
    const step = (now: number) => {
      const progress = Math.min(1, (now - began) / SWEEP_MS);
      setSwept(Math.round(progress * total));
      frame.current = progress < 1 ? requestAnimationFrame(step) : null;
    };
    frame.current = requestAnimationFrame(step);
  };

  const run = async () => {
    setBusy(true);
    setError(null);
    setResult(null);
    setSwept(0);
    try {
      const next = await api<IntegrityResult>(`/evidence/${evidenceId}/verify-integrity`, {
        method: "POST",
      });
      setResult(next);
      // Only a match settles. Marking the characters a mismatch happens to share would
      // paint most of a failed check green, and a failed check is a smoke alarm.
      if (next.status !== "MATCH") setSwept(0);
      else if (reducedMotion()) setSwept(next.expected.length);
      else sweep(next.expected.length);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The integrity check could not run.");
    } finally {
      setBusy(false);
    }
  };

  const matched = result?.status === "MATCH";
  const settled = result !== null && swept >= result.expected.length;

  return (
    <div className="integrityCheck">
      <button className="button" onClick={run} disabled={busy}>
        {busy ? "Re-reading the stored object…" : "Check this yourself"}
      </button>
      {error && (
        <div className="errorMessage" role="alert">
          <b>Could not verify</b>
          <span>{error}</span>
        </div>
      )}
      {result && (
        <div className="integrityResult" role="status">
          <div className="integrityHead">
            {/* The seal, not a pill: it means one specific thing because it appears in
                exactly one place. A mismatch keeps the pill -- it needs to be loud. */}
            {matched && <Seal shown={settled} />}
            <h3 className="integrityHeadline">
              {integritySentence(matched, documentType, result.registeredAt)}
            </h3>
          </div>
          {!matched && <Status kind="failed">Mismatch</Status>}
          {result.byteCount !== undefined && (
            <p className="checkWork">
              Read {result.byteCount.toLocaleString()} bytes back from storage and hashed
              them again.
            </p>
          )}
          <div className="hashPair" data-settled={settled}>
            <div>
              <span>Registered commitment</span>
              <Hash value={result.expected} against={result.current} swept={swept} />
              <span>Hash of the stored object now</span>
              <Hash value={result.current} against={result.expected} swept={swept} />
            </div>
            {settled && matched && (
              <p className="hashVerdict">
                <span className="hashBracket" aria-hidden />
                identical
              </p>
            )}
          </div>
          <p className="note">{result.explanation}</p>
        </div>
      )}
    </div>
  );
}

/**
 * The hash, with the characters the comparison has passed marked as agreeing.
 *
 * A character settles only where the two strings actually agree, so the sweep can never
 * colour a difference as a match. Read as one string by assistive technology.
 */
function Hash({
  value,
  against,
  swept,
}: {
  value: string;
  against: string;
  swept: number;
}) {
  return (
    <code aria-label={value}>
      {value.split("").map((character, index) => (
        <span
          aria-hidden
          key={index}
          className={index < swept && character === against[index] ? "agrees" : undefined}
        >
          {character}
        </span>
      ))}
    </code>
  );
}

/**
 * The one mark in this product, and the rule that keeps it meaning something.
 *
 * It renders only here, only on a completed, passing, first-party hash comparison. The
 * moment it also appears on a pending state, on the operator's attestation or on a claim
 * summary it means nothing, and it becomes the iconography of every worthless trust badge
 * on the web. Scarcity is the whole design.
 *
 * Its space is reserved before it is shown so the sentence beside it does not shift.
 */
function Seal({ shown }: { shown: boolean }) {
  return (
    <span
      className="seal"
      data-shown={shown}
      role={shown ? "img" : undefined}
      aria-label={shown ? "Byte-for-byte match, checked by you just now" : undefined}
      aria-hidden={shown ? undefined : true}
    >
      <i aria-hidden>I</i>
    </span>
  );
}
