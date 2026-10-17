import { defineConfig } from "@playwright/test";

// Build first (npm run build). Production frontend against contract fixtures; no database or user account mutations.
const existingBaseURL = process.env.HPAGENT_ENTRY_BASE_URL;

export default defineConfig({
  testDir: "./e2e/entry",
  workers: 1,
  timeout: 30000,
  expect: { timeout: 7000 },
  reporter: "list",
  outputDir: "./test-results/contract",
  use: {
    launchOptions: process.env.HPAGENT_BROWSER_EXECUTABLE
      ? { executablePath: process.env.HPAGENT_BROWSER_EXECUTABLE }
      : {},
    baseURL: existingBaseURL ?? "http://127.0.0.1:5311",
    viewport: { width: 1440, height: 1000 },
    trace: "retain-on-failure",
  },
  webServer: existingBaseURL
    ? undefined
    : {
        command: "npm run preview -- --host 127.0.0.1 --port 5311 --strictPort",
        url: "http://127.0.0.1:5311",
        reuseExistingServer: false,
      },
});
