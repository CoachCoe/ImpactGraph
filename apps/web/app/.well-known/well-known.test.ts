// @vitest-environment node
import { afterEach, expect, it, vi } from "vitest";
import links from "../../../../contracts/mobile/links.json";
import { GET as appleAssociation } from "./apple-app-site-association/route";
import { GET as androidAssociation } from "./assetlinks.json/route";

const signingKey = Array.from({ length: 32 }, (_, i) => i.toString(16).padStart(2, "0")).join(":");

afterEach(() => vi.unstubAllEnvs());

it("names no app until one is configured", async () => {
  vi.stubEnv("DONOR_IOS_APP_ID", "");
  vi.stubEnv("DONOR_ANDROID_APP_PACKAGE", "");
  vi.stubEnv("DONOR_ANDROID_APP_CERT_SHA256", "");
  for (const response of [appleAssociation(), androidAssociation()]) {
    expect(response.status).toBe(404);
    expect(await response.json()).toEqual({ detail: "Not found" });
  }
});

it("associates the iOS app with the account and partner pages and its passkeys", async () => {
  vi.stubEnv("DONOR_IOS_APP_ID", "ABCDE12345.org.dynamicaid.donor");
  const response = appleAssociation();
  expect(response.status).toBe(200);
  expect(response.headers.get("Content-Type")).toBe("application/json");
  expect(response.headers.get("Cache-Control")).toBe("public, max-age=3600");
  expect(await response.json()).toEqual({
    applinks: {
      details: [
        {
          appIDs: ["ABCDE12345.org.dynamicaid.donor"],
          components: [
            { "/": "/r/*" },
            { "/": "/c/*" },
            { "/": "/partner" },
            { "/": "/partner/accept" },
            { "/": "/account/claim" },
            { "/": "/account/email-change" },
            { "/": "/account/payment-method" },
            { "/": "/account/dashboard" },
            { "/": "/account/profile" },
            { "/": "/account/gifts" },
          ],
        },
      ],
    },
    webcredentials: { apps: ["ABCDE12345.org.dynamicaid.donor"] },
  });
});

it("keeps Apple association paths aligned with the shared mobile link policy", async () => {
  vi.stubEnv("DONOR_IOS_APP_ID", "ABCDE12345.org.dynamicaid.donor");
  const association = await appleAssociation().json();
  const paths = association.applinks.details[0].components.map((entry: { "/": string }) => entry["/"]);
  expect(paths.toSorted()).toEqual(
    [
      `${links.referral_prefix}*`,
      `${links.company_prefix}*`,
      ...links.partner_paths,
      ...links.partner_proof_paths,
      ...links.proof_paths,
      ...links.account_paths,
    ].toSorted(),
  );
  expect(links.referrals_open_web).toBe(false);
});

it("associates the Android app with its links and passkeys for every signing key", async () => {
  vi.stubEnv("DONOR_ANDROID_APP_PACKAGE", "org.dynamicaid.donor");
  vi.stubEnv("DONOR_ANDROID_APP_CERT_SHA256", ` ${signingKey}, ${signingKey.replace(/^00/, "FF").toUpperCase()} `);
  const response = androidAssociation();
  expect(response.status).toBe(200);
  expect(response.headers.get("Content-Type")).toBe("application/json");
  expect(await response.json()).toEqual([
    {
      relation: ["delegate_permission/common.handle_all_urls", "delegate_permission/common.get_login_creds"],
      target: {
        namespace: "android_app",
        package_name: "org.dynamicaid.donor",
        sha256_cert_fingerprints: [signingKey.toUpperCase(), signingKey.replace(/^00/, "FF").toUpperCase()],
      },
    },
  ]);
});

it("fails the build on a malformed association rather than publish it", () => {
  for (const appID of ["XXXXXXXXXX", "abcde12345.org.dynamicaid.donor", "ABCDE12345.donor"]) {
    vi.stubEnv("DONOR_IOS_APP_ID", appID);
    expect(appleAssociation, appID).toThrow(/DONOR_IOS_APP_ID/);
  }
  for (const [packageName, fingerprints, setting] of [
    ["org.dynamicaid.donor", "", "DONOR_ANDROID_APP_CERT_SHA256"],
    ["", signingKey, "DONOR_ANDROID_APP_PACKAGE"],
    ["donor", signingKey, "DONOR_ANDROID_APP_PACKAGE"],
    ["org.dynamicaid.donor", signingKey.slice(3), "DONOR_ANDROID_APP_CERT_SHA256"],
  ]) {
    vi.stubEnv("DONOR_ANDROID_APP_PACKAGE", packageName);
    vi.stubEnv("DONOR_ANDROID_APP_CERT_SHA256", fingerprints);
    expect(androidAssociation, `${packageName} ${fingerprints}`).toThrow(setting);
  }
});
