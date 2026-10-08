import { defineConfig, devices } from "@playwright/test";
export default defineConfig({
  testDir: "./e2e",
  testMatch: "ui-7-self-review.spec.ts",
  workers: 1,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  use: { baseURL: "http://localhost:5288", trace: "retain-on-failure" },
  webServer: {
    command: "npm run dev -- --port 5288",
    url: "http://localhost:5288",
    reuseExistingServer: false,
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
