import type { NextConfig } from "next";
import { loadEnvConfig } from "@next/env";
import path from "node:path";

loadEnvConfig(path.resolve(process.cwd(), "../.."));

/**
 * Sent on every response. None of these were set before, so a reflected script would
 * have run with the page's full authority and any page could be framed.
 *
 * `frame-ancestors 'none'` matters more here than on a typical site: the operator and
 * verifier workspaces carry irreversible single-click actions -- attest to this bundle,
 * confirm this finding -- and a framed page is how those get clicked by someone who
 * thought they were clicking something else.
 *
 * `Referrer-Policy` is not routine either. The notification links carry their token in
 * the query string, and the default policy hands a full URL to whatever a reader clicks
 * through to next.
 */
const securityHeaders = [
  {
    key: "Content-Security-Policy",
    value: [
      "default-src 'self'",
      // Next hydration inlines a bootstrap script and its runtime evaluates chunks, so
      // a nonce-only policy would need every page to be dynamic. This is the honest
      // ceiling for an app rendered this way rather than a policy that looks stricter
      // than it is.
      "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
      // styled-jsx and the font loader both emit inline styles.
      "style-src 'self' 'unsafe-inline'",
      "img-src 'self' data: blob:",
      "font-src 'self' data:",
      // The wallet talks to whatever EVM node is configured, which is not known here.
      "connect-src 'self' http: https: ws: wss:",
      "frame-ancestors 'none'",
      "base-uri 'self'",
      "form-action 'self'",
      "object-src 'none'",
    ].join("; "),
  },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(self), geolocation=(), microphone=()" },
  // Only meaningful over TLS; harmless on the plain-HTTP local demo, and forgetting it
  // at deploy time is how a first request stays downgradeable.
  { key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains" },
];

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  // The API is proxied by app/api/[...path]/route.ts rather than a rewrite: with
  // output: standalone a rewrite destination is baked in at build time, so a runtime
  // API_BASE_URL was silently ignored.
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default nextConfig;
