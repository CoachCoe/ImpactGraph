import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * The motion budget, enforced.
 *
 * A rule written only as a comment gets spent by accident, one tasteful fade at a time,
 * and in six months the hash sweep is just another transition. Every animated declaration
 * in the stylesheet must be listed here, so adding one is a deliberate argument rather
 * than a drive-by.
 */
const ALLOWED = [
  // The one expressive animation: characters settling as the comparison passes them.
  "transition:color .12s linear",
  // Its own reduced-motion override.
  "transition:none",
  // A busy indicator, not expression: a wallet signature or chain submission in flight.
  "animation:spin 1s linear infinite",
];

// Comments are stripped first: prose about animation is not animation, and the rule
// stated at the top of the stylesheet would otherwise trip its own scanner.
const css = readFileSync(join(process.cwd(), "app/globals.css"), "utf8").replace(
  /\/\*[\s\S]*?\*\//g,
  " ",
);

describe("motion budget", () => {
  it("animates nothing that is not argued for here", () => {
    const declared = (css.match(/(?:transition|animation)\s*:[^;}]*/g) ?? []).map((d) =>
      d.replace(/\s*:\s*/, ":").trim(),
    );
    expect([...new Set(declared)].sort()).toEqual([...new Set(ALLOWED)].sort());
  });

  it("declares no keyframes beyond the busy indicator's", () => {
    const keyframes = (css.match(/@keyframes\s+([a-zA-Z-]+)/g) ?? []).map((k) =>
      k.split(/\s+/)[1],
    );
    expect(keyframes).toEqual(["spin"]);
  });

  it("keeps the seal to exactly one place in the product", () => {
    // The mark means one specific thing because it appears in one specific place. On a
    // pending state, an operator attestation or a claim summary it would mean nothing.
    const sources = import.meta.glob("../{app,components}/**/*.tsx", {
      eager: true,
      query: "?raw",
      import: "default",
    }) as Record<string, string>;
    const users = Object.entries(sources)
      .filter(([path, source]) => !path.endsWith(".test.tsx") && /className="seal"/.test(source))
      .map(([path]) => path.split("/").pop());
    expect(users).toEqual(["IntegrityCheck.tsx"]);
  });
});
