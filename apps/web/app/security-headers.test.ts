/**
 * The headers are configuration, and configuration rots quietly: nothing fails when one
 * is dropped, the page still renders, and the protection is simply gone. This asserts the
 * ones that were missing entirely until they were added.
 */
import { describe, expect, it } from "vitest";
import nextConfig from "../next.config";

async function headerMap(): Promise<Map<string, string>> {
  const groups = await nextConfig.headers!();
  const all = groups.flatMap((group) => group.headers);
  return new Map(all.map((header) => [header.key, header.value]));
}

describe("security headers", () => {
  it("applies to every path rather than a subset", async () => {
    const groups = await nextConfig.headers!();
    expect(groups).toHaveLength(1);
    expect(groups[0].source).toBe("/:path*");
  });

  it.each([
    ["X-Content-Type-Options", "nosniff"],
    ["X-Frame-Options", "DENY"],
    ["Referrer-Policy", "strict-origin-when-cross-origin"],
  ])("sets %s", async (key, value) => {
    expect((await headerMap()).get(key)).toBe(value);
  });

  it("refuses framing in the CSP as well as the legacy header", async () => {
    // The operator and verifier workspaces carry irreversible single-click actions.
    // X-Frame-Options alone leaves browsers that only honour frame-ancestors unprotected.
    expect((await headerMap()).get("Content-Security-Policy")).toContain(
      "frame-ancestors 'none'",
    );
  });

  it.each(["default-src 'self'", "object-src 'none'", "base-uri 'self'", "form-action 'self'"])(
    "keeps %s in the policy",
    async (directive) => {
      expect((await headerMap()).get("Content-Security-Policy")).toContain(directive);
    },
  );

  it("does not widen the script policy beyond what Next actually needs", async () => {
    /**
     * 'unsafe-inline' and 'unsafe-eval' are conceded for hydration and chunk evaluation
     * and are documented as the ceiling. Anything further -- a wildcard host, or data:
     * as a script source -- would be a real loosening rather than the same concession.
     */
    const policy = (await headerMap()).get("Content-Security-Policy")!;
    const scriptSrc = policy.split(";").find((part) => part.trim().startsWith("script-src"))!;

    expect(scriptSrc).not.toContain("*");
    expect(scriptSrc).not.toContain("data:");
    expect(scriptSrc).not.toContain("http:");
  });

  it("does not hand the camera or microphone to an embedded third party", async () => {
    const policy = (await headerMap()).get("Permissions-Policy")!;
    expect(policy).toContain("geolocation=()");
    expect(policy).toContain("microphone=()");
    // Field capture uses the native picker on this origin.
    expect(policy).toContain("camera=(self)");
  });
});
