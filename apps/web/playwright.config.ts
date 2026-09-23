import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 45_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "desktop-chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile-chromium", use: { ...devices["Pixel 7"] } },
  ],
  webServer: [
    {
      command: "../api/.venv/bin/uvicorn impactgraph.main:app --app-dir ../api --host 127.0.0.1 --port 8000",
      // Liveness: this waits for the server to start, not for its dependencies. Readiness
      // answers 503 until a chain is reachable, which a running server legitimately can,
      // and Playwright would sit here until it timed out instead of running the specs
      // that report the real problem.
      url: "http://127.0.0.1:8000/health/live",
      reuseExistingServer: true,
      timeout: 30_000,
    },
    {
      command: "npm run dev -- --hostname 127.0.0.1 --port 3000",
      url: "http://127.0.0.1:3000",
      reuseExistingServer: true,
      timeout: 30_000,
    },
  ],
});
