import { describe, expect, it } from "vitest";
import { formatMoney } from "./money";

describe("formatMoney", () => {
  it("treats the amount as integer minor units", () => {
    expect(formatMoney({ amountMinor: 420000, currency: "USD" })).toBe("$4,200.00");
    // A cent is a cent, not a rounding artefact.
    expect(formatMoney({ amountMinor: 1, currency: "USD" })).toBe("$0.01");
    expect(formatMoney({ amountMinor: 0, currency: "USD" })).toBe("$0.00");
  });

  it("respects the currency it is given", () => {
    expect(formatMoney({ amountMinor: 250000, currency: "EUR" })).toContain("2,500.00");
  });
});
