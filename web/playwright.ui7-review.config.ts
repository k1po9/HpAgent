import { defineConfig, devices } from "@playwright/test";
const port = Number(process.env.HPAGENT_UI7_PORT ?? 5288);
export default defineConfig({
  testDir: "./e2e",
  testMatch: "ui-7-self-review.spec.ts",
  workers: 1,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  use: { baseURL: `http://127.0.0.1:${port}`, trace: "retain-on-failure" },
  webServer: {
    command: `npm run dev -- --host 127.0.0.1 --port ${port} --strictPort`,
    url: `http://127.0.0.1:${port}`,
    reuseExistingServer: false,
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
