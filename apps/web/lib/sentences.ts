/**
 * Sentences composed from counts.
 *
 * These read as copy but are facts, so they are computed rather than written: a literal
 * "Eight cross-checks agree" goes stale the first time someone adds a check, and a
 * dashboard that overstates a count is the one thing this product cannot afford.
 */

import type { Money } from "@/lib/types";
import { formatMoney } from "@/lib/money";

const WORDS = [
  "No",
  "One",
  "Two",
  "Three",
  "Four",
  "Five",
  "Six",
  "Seven",
  "Eight",
  "Nine",
  "Ten",
];

/** Small numbers read better spelled out; larger ones stay numerals. */
export function count(n: number): string {
  return n >= 0 && n < WORDS.length ? WORDS[n] : String(n);
}

function lower(n: number): string {
  return count(n).toLowerCase();
}

export type CheckResult = { result: string };

/**
 * Describe a set of reconciliation cross-checks.
 *
 * A WARNING is not a disagreement -- it is a check that could not be completed, usually
 * for want of metadata. Collapsing the two would report a document as contradicted when
 * nothing contradicts it.
 */
export function crossCheckSentence(checks: readonly CheckResult[]): string {
  if (checks.length === 0) return "";
  const pass = checks.filter((c) => c.result === "PASS").length;
  const fail = checks.filter((c) => c.result === "FAIL").length;
  const warn = checks.length - pass - fail;

  if (fail === 0 && warn === 0) {
    if (checks.length === 1) return "The single cross-check agrees.";
    return `All ${lower(checks.length)} cross-checks agree.`;
  }

  const parts: string[] = [];
  if (pass > 0) {
    parts.push(pass === 1 ? "One cross-check agrees." : `${count(pass)} cross-checks agree.`);
  }
  if (fail > 0) {
    parts.push(fail === 1 ? "One doesn't." : `${count(fail)} don't.`);
  }
  if (warn > 0) {
    parts.push(
      warn === 1
        ? "One couldn't be completed."
        : `${count(warn)} couldn't be completed.`,
    );
  }
  return parts.join(" ");
}

/** "One piece of evidence supports this claim." */
export function supportingEvidenceSentence(n: number): string {
  if (n === 0) return "Nothing supports this claim yet.";
  if (n === 1) return "One piece of evidence supports this claim.";
  return `${count(n)} pieces of evidence support this claim.`;
}

/** "Two confirmed attestations" -- without the "(s)". */
export function attestationCount(n: number): string {
  return n === 1 ? "One confirmed attestation" : `${count(n)} confirmed attestations`;
}

/** A run of the opening sentence; `link` names a route the figure can be audited at. */
export type Segment = { text: string; link?: "money" | "claim" };

export type ProgramFacts = {
  name: string;
  region: string;
  funding: Money;
  deployed: Money;
  filtrationSystems: number;
  peopleServed: number;
};

const cash = (money: Money) => formatMoney(money, { maximumFractionDigits: 0 });

/**
 * The donor's opening line, composed from the program's own records.
 *
 * Written empty-first on purpose. A template with cheerful defaults would be the worst
 * thing that could happen to this page, so every degraded state says what is missing
 * rather than reaching for the nearest positive number.
 *
 * It does not say "your". The dashboard is public, reports program-wide totals, and the
 * reader is not necessarily the funder -- second person here would be a pleasant lie.
 */
export function programSentence(facts: ProgramFacts): Segment[] {
  const funded = facts.funding.amountMinor;
  const spent = facts.deployed.amountMinor;

  if (funded <= 0 && spent <= 0) {
    return [{ text: `Nothing has been recorded for ${facts.name} yet.` }];
  }
  if (spent <= 0) {
    return [
      { text: cash(facts.funding), link: "money" },
      { text: ` is committed to ${facts.name}. Nothing has been spent yet.` },
    ];
  }

  const spend: Segment[] = [
    { text: cash(facts.deployed), link: "money" },
    { text: ` of ${cash(facts.funding)} has been spent` },
  ];

  if (facts.filtrationSystems <= 0) {
    return [...spend, { text: ". No delivery has been recorded yet." }];
  }
  const delivery: Segment[] = [
    ...spend,
    { text: " on " },
    {
      text: `${facts.filtrationSystems.toLocaleString()} filtration ${
        facts.filtrationSystems === 1 ? "system" : "systems"
      }`,
      link: "claim",
    },
  ];
  if (facts.peopleServed <= 0) {
    return [...delivery, { text: ` in ${facts.region}. No outcome has been recorded yet.` }];
  }
  return [
    ...delivery,
    { text: ", serving " },
    { text: `${facts.peopleServed.toLocaleString()} people`, link: "claim" },
    { text: ` in ${facts.region}.` },
  ];
}

export type Scored = { component: string; score: number; maximum: number };

/**
 * The component costing the score the most, or null when nothing is missing.
 *
 * A donor told why 72 is 72 is being treated as a participant rather than handed a grade.
 * Ties resolve to the component with the larger maximum, which is the one worth naming.
 */
export function largestScoreGap(components: readonly Scored[]): Scored | null {
  let worst: Scored | null = null;
  for (const item of components) {
    const gap = item.maximum - item.score;
    if (gap <= 0) continue;
    const worstGap = worst ? worst.maximum - worst.score : 0;
    if (gap > worstGap || (gap === worstGap && worst !== null && item.maximum > worst.maximum)) {
      worst = item;
    }
  }
  return worst;
}

/**
 * What the integrity check establishes, in one sentence.
 *
 * The date is the chain's, not ours, and it is omitted rather than guessed. The obvious
 * date to hand elsewhere is the document's own -- an invoice dated the 17th says nothing
 * about when its commitment was registered -- and using it would turn a statement about
 * bytes into a claim about the document.
 *
 * It may talk about change, dates and documents. It may never call a document genuine,
 * authentic or true: that is what the caveat beneath it exists to prevent.
 */
export function integritySentence(
  matched: boolean,
  documentType?: string,
  registeredAt?: string | null,
): string {
  const noun = documentType?.toLowerCase() || "document";
  const when = registeredAt ? formatRegistrationDate(registeredAt) : null;
  if (matched) {
    return when
      ? `Nothing in this ${noun} has changed since it was registered on ${when}.`
      : `Nothing in this ${noun} has changed since it was registered.`;
  }
  return when
    ? `This ${noun} is not the one that was registered on ${when}.`
    : `This ${noun} is not the one that was registered.`;
}

function formatRegistrationDate(value: string): string | null {
  const when = new Date(value);
  if (Number.isNaN(when.getTime())) return null;
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  }).format(when);
}

export type ClaimStanding = {
  /** Only true when the policy currently holds. Never true for a challenged claim. */
  verified: boolean;
  challenged: boolean;
  badge: string;
  tone: "verified" | "pending" | "warning";
};

/**
 * How a claim's status should read.
 *
 * CHALLENGED exists because a claim that was verified and whose evidence no longer
 * matches its commitment is not "pending" -- nothing is being awaited, something has gone
 * wrong -- and it is certainly not verified. Treating it as either would restate the bug
 * this state was added to fix.
 */
export function claimStanding(status: string): ClaimStanding {
  if (status === "VERIFIED") {
    return { verified: true, challenged: false, badge: "Independently verified", tone: "verified" };
  }
  if (status === "CHALLENGED") {
    return {
      verified: false,
      challenged: true,
      badge: "Verification withdrawn — evidence changed",
      tone: "warning",
    };
  }
  if (status === "REJECTED") {
    return { verified: false, challenged: false, badge: "Rejected by the verifier", tone: "warning" };
  }
  return {
    verified: false,
    challenged: false,
    badge: "Independent verification pending",
    tone: "pending",
  };
}
