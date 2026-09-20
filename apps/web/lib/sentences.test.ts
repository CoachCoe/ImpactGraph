import { describe, expect, it } from "vitest";
import {
  attestationCount,
  claimStanding,
  count,
  crossCheckSentence,
  integritySentence,
  largestScoreGap,
  programSentence,
  supportingEvidenceSentence,
} from "./sentences";

describe("count", () => {
  it("spells small numbers and leaves large ones as numerals", () => {
    expect(count(0)).toBe("No");
    expect(count(1)).toBe("One");
    expect(count(10)).toBe("Ten");
    expect(count(11)).toBe("11");
  });
});

describe("crossCheckSentence", () => {
  const of = (...results: string[]) => results.map((result) => ({ result }));

  it("says nothing when there are no checks", () => {
    expect(crossCheckSentence([])).toBe("");
  });

  it("reports full agreement", () => {
    expect(crossCheckSentence(of("PASS", "PASS", "PASS"))).toBe("All three cross-checks agree.");
  });

  it("matches the seeded showcase: eight agree, one could not be completed", () => {
    // The seeded invoice reconciles 8 PASS and 1 WARNING (a photograph with no GPS).
    const checks = of(...Array(8).fill("PASS"), "WARNING");
    expect(crossCheckSentence(checks)).toBe(
      "Eight cross-checks agree. One couldn't be completed.",
    );
  });

  it("does not describe a warning as a disagreement", () => {
    // A check that could not run is not a check that contradicts the document.
    const sentence = crossCheckSentence(of("PASS", "WARNING"));
    expect(sentence).toContain("couldn't be completed");
    expect(sentence).not.toContain("doesn't");
  });

  it("reports a real disagreement as one", () => {
    expect(crossCheckSentence(of("PASS", "FAIL"))).toBe(
      "One cross-check agrees. One doesn't.",
    );
  });

  it("keeps disagreements and incomplete checks distinct", () => {
    expect(crossCheckSentence(of("PASS", "PASS", "FAIL", "WARNING"))).toBe(
      "Two cross-checks agree. One doesn't. One couldn't be completed.",
    );
  });

  it("counts an unknown result as neither agreement nor disagreement", () => {
    const sentence = crossCheckSentence(of("PASS", "SOMETHING_NEW"));
    expect(sentence).toContain("One cross-check agrees.");
    expect(sentence).toContain("couldn't be completed");
  });
});

describe("supportingEvidenceSentence", () => {
  it("does not render a parenthesised plural", () => {
    expect(supportingEvidenceSentence(1)).toBe("One piece of evidence supports this claim.");
    expect(supportingEvidenceSentence(3)).toBe("Three pieces of evidence support this claim.");
    expect(supportingEvidenceSentence(0)).toBe("Nothing supports this claim yet.");
    expect(supportingEvidenceSentence(1)).not.toContain("(s)");
  });
});

describe("attestationCount", () => {
  it("agrees in number", () => {
    expect(attestationCount(1)).toBe("One confirmed attestation");
    expect(attestationCount(2)).toBe("Two confirmed attestations");
  });
});

describe("programSentence", () => {
  const usd = (amountMinor: number) => ({ amountMinor, currency: "USD" });
  const seeded = {
    name: "Clean Water Kenya 2026",
    region: "Kisumu County, Kenya",
    funding: usd(10000000),
    deployed: usd(8742000),
    filtrationSystems: 12,
    peopleServed: 2840,
  };
  const say = (facts: Parameters<typeof programSentence>[0]) =>
    programSentence(facts)
      .map((segment) => segment.text)
      .join("");

  it("composes the seeded program from its own records", () => {
    expect(say(seeded)).toBe(
      "$87,420 of $100,000 has been spent on 12 filtration systems, " +
        "serving 2,840 people in Kisumu County, Kenya.",
    );
  });

  it("never says \"your\": the page is public and the reader may not be the funder", () => {
    expect(say(seeded).toLowerCase()).not.toContain("your");
  });

  it("says what is missing rather than reaching for the nearest positive number", () => {
    expect(say({ ...seeded, deployed: usd(0) })).toBe(
      "$100,000 is committed to Clean Water Kenya 2026. Nothing has been spent yet.",
    );
    expect(say({ ...seeded, filtrationSystems: 0 })).toBe(
      "$87,420 of $100,000 has been spent. No delivery has been recorded yet.",
    );
    expect(say({ ...seeded, peopleServed: 0 })).toBe(
      "$87,420 of $100,000 has been spent on 12 filtration systems in " +
        "Kisumu County, Kenya. No outcome has been recorded yet.",
    );
    expect(say({ ...seeded, funding: usd(0), deployed: usd(0) })).toBe(
      "Nothing has been recorded for Clean Water Kenya 2026 yet.",
    );
  });

  it("does not claim an outcome an empty program has not produced", () => {
    const empty = say({ ...seeded, funding: usd(0), deployed: usd(0) });
    expect(empty).not.toContain("2,840");
    expect(empty).not.toContain("filtration");
  });

  it("agrees in number with a single system", () => {
    expect(say({ ...seeded, filtrationSystems: 1 })).toContain("1 filtration system,");
  });

  it("makes every figure auditable by linking it to its records", () => {
    // A slogan cannot be audited and this sentence can -- but only if the numbers lead
    // somewhere. Each linked segment must name a route.
    const linked = programSentence(seeded).filter((segment) => segment.link);
    expect(linked.length).toBeGreaterThanOrEqual(3);
    expect(linked.every((segment) => segment.link === "money" || segment.link === "claim")).toBe(
      true,
    );
  });
});

describe("largestScoreGap", () => {
  const seeded = [
    { component: "financialReconciliation", score: 25, maximum: 25 },
    { component: "evidenceIntegrity", score: 20, maximum: 20 },
    { component: "operatorAttestation", score: 15, maximum: 15 },
    { component: "independentVerification", score: 0, maximum: 25 },
    { component: "locationCorroboration", score: 7, maximum: 10 },
    { component: "evidenceConsistency", score: 5, maximum: 5 },
  ];

  it("names what is actually costing the score", () => {
    expect(largestScoreGap(seeded)?.component).toBe("independentVerification");
  });

  it("says nothing when nothing is missing", () => {
    expect(largestScoreGap(seeded.map((c) => ({ ...c, score: c.maximum })))).toBeNull();
  });

  it("ignores components that are already full", () => {
    expect(largestScoreGap([{ component: "a", score: 5, maximum: 5 }])).toBeNull();
  });

  it("breaks a tie toward the component with more at stake", () => {
    const tied = [
      { component: "small", score: 0, maximum: 5 },
      { component: "large", score: 20, maximum: 25 },
    ];
    expect(largestScoreGap(tied)?.component).toBe("large");
  });

  it("handles an empty breakdown", () => {
    expect(largestScoreGap([])).toBeNull();
  });
});
describe("integritySentence", () => {
  const mined = "2026-09-20T15:37:25Z";

  it("carries the chain's date when there is one", () => {
    expect(integritySentence(true, "invoice", mined)).toBe(
      "Nothing in this invoice has changed since it was registered on 20 September 2026.",
    );
    expect(integritySentence(false, "invoice", mined)).toBe(
      "This invoice is not the one that was registered on 20 September 2026.",
    );
  });

  it("omits the date rather than guessing one", () => {
    // The obvious date to hand is the document's own, and an invoice dated the 17th says
    // nothing about when its commitment was registered.
    expect(integritySentence(true, "invoice", null)).toBe(
      "Nothing in this invoice has changed since it was registered.",
    );
    expect(integritySentence(true, "invoice", undefined)).toBe(
      "Nothing in this invoice has changed since it was registered.",
    );
  });

  it("omits an unparseable date instead of rendering it", () => {
    expect(integritySentence(true, "invoice", "not a date")).not.toContain("Invalid");
    expect(integritySentence(true, "invoice", "not a date")).toBe(
      "Nothing in this invoice has changed since it was registered.",
    );
  });

  it("falls back to \"document\" when the type is unknown", () => {
    expect(integritySentence(true)).toContain("this document");
    expect(integritySentence(true, "")).toContain("this document");
  });

  it("never calls the document genuine, authentic or true", () => {
    // The check establishes that bytes have not changed. Nothing more.
    for (const matched of [true, false]) {
      expect(integritySentence(matched, "invoice", mined)).not.toMatch(
        /genuine|authentic|\btrue\b|verified|valid/i,
      );
    }
  });

  it("reads the date in UTC, so the chain's day does not shift with the reader", () => {
    // 00:30 UTC is still the 20th; a local-time render could show the 19th.
    expect(integritySentence(true, "invoice", "2026-09-20T00:30:00Z")).toContain(
      "20 September 2026",
    );
  });
});

describe("claimStanding", () => {
  it("reads a verified claim as verified", () => {
    const standing = claimStanding("VERIFIED");
    expect(standing.verified).toBe(true);
    expect(standing.badge).toBe("Independently verified");
  });

  it("never reads a challenged claim as verified", () => {
    // A claim whose evidence no longer matches its commitment kept the verified badge
    // above a requirement list showing the integrity check failing. That is the single
    // worst thing this product can display.
    const standing = claimStanding("CHALLENGED");
    expect(standing.verified).toBe(false);
    expect(standing.challenged).toBe(true);
    expect(standing.badge).toMatch(/withdrawn|evidence changed/i);
  });

  it("does not read a challenged claim as merely pending either", () => {
    // Nothing is being awaited; something has gone wrong. Pending would understate it.
    expect(claimStanding("CHALLENGED").tone).not.toBe("pending");
    expect(claimStanding("CHALLENGED").badge).not.toMatch(/pending/i);
  });

  it("treats an unknown or absent status as not verified", () => {
    for (const status of ["", "DRAFT", "EVIDENCE_PENDING", "SOMETHING_NEW"]) {
      expect(claimStanding(status).verified).toBe(false);
    }
  });

  it("distinguishes a rejection from a pending claim", () => {
    expect(claimStanding("REJECTED").badge).toMatch(/rejected/i);
  });
});
