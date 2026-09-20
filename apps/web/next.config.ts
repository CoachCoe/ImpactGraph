import type { NextConfig } from "next";
import { loadEnvConfig } from "@next/env";
import path from "node:path";

loadEnvConfig(path.resolve(process.cwd(), "../.."));

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  // The API is proxied by app/api/[...path]/route.ts rather than a rewrite: with
  // output: standalone a rewrite destination is baked in at build time, so a runtime
  // API_BASE_URL was silently ignored.
};

export default nextConfig;
