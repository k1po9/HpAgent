/**
 * Playwright browser E2E (phase-e E-07).
 *
 * Two servers are started for every run: the real HpAgent web API (Fake Run
 * Executor + PostgreSQL + Redis, see scripts/e2e-backend.sh) on :8080, and the
 * vite dev server on :5173 proxying same-origin /api and /auth to it. Each spec
 * drives real browsers against real infrastructure — no mocks, no shared local
 * state, and different accounts are isolated by separate browser contexts.
 */
import { defineConfig, devices } from "@playwright/test";

const BACKEND_PORT = 8080;
const WEB_PORT = 5173;
const WEB_BASE = `http://localhost:${WEB_PORT}`;

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  // The specs share real infrastructure (one API/PostgreSQL/Redis, the same
  // `alice` account). They must never overlap, or concurrent logins invalidate
  // each other's sessions and runs stall — same-account tests run serial.
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"]] : [["list"]],
  use: {
    baseURL: WEB_BASE,
    trace: "retain-on-failure",
  },
  webServer: [
    {
      // Real API: migrations + argon2 credentials + Fake Executor with online
      // SSE events. Playwright waits for /health/ready before any test runs.
      command: "bash scripts/e2e-backend.sh",
      url: `http://127.0.0.1:${BACKEND_PORT}/health/ready`,
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: "npm run dev",
      url: WEB_BASE,
      reuseExistingServer: false,
      timeout: 60_000,
      env: { HPAGENT_API_TARGET: `http://127.0.0.1:${BACKEND_PORT}` },
    },
  ],
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
