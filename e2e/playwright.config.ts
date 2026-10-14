import { defineConfig } from "@playwright/test";

/**
 * End-to-end configuration.
 *
 * The suite normally runs inside the compose `e2e` service, where the
 * app is reachable as http://app:8000 over the compose network (see
 * `make e2e`). Override E2E_BASE_URL to point at any deployment —
 * e.g. the live Render instance:
 *
 *   E2E_BASE_URL=https://sql-server-n68y.onrender.com npx playwright test
 */
export default defineConfig({
  testDir: "./tests",
  // Ceiling sized to the SLOWEST supported target, not localhost: the
  // journey finishes in ~4s against the compose network, but against a
  // public free-tier deployment every navigation and action pays TLS +
  // internet RTT and runs on a throttled CPU — measured ~145s end to
  // end (traced: network itself was only ~16s of it). 240s leaves
  // margin there while still bounding a truly hung journey.
  timeout: 240_000,
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
