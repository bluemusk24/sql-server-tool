import { defineConfig } from "@playwright/test";

/**
 * End-to-end configuration.
 *
 * The suite normally runs inside the compose `e2e` service, where the
 * app is reachable as http://app:8000 over the compose network (see
 * `make e2e`). Override E2E_BASE_URL to point at any deployment —
 * e.g. a Render URL:
 *
 *   E2E_BASE_URL=https://sql-server.onrender.com npx playwright test
 */
export default defineConfig({
  testDir: "./tests",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  // The journey exercises one shared backend; keep it strictly serial.
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    // WebKit deliberately: recent Chromium and Firefox builds probe an
    // HTTPS upgrade for single-label intranet hostnames (http://app:8000
    // inside the compose network) and fail the navigation with an SSL
    // error when the port speaks plain HTTP. WebKit navigates it
    // directly. The suite uses only engine-agnostic APIs.
    browserName: "webkit",
    baseURL: process.env.E2E_BASE_URL ?? "http://app:8000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
