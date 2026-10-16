import { defineConfig, devices } from "@playwright/test";
export default defineConfig({
  testDir: "./r416-e2e",
  timeout: 90000,
  expect: { timeout: 15000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [
    ["list"],
    [
      "json",
      {
        outputFile:
          process.env.R416_REPORT ??
          "../artifacts/product-acceptance/ui-iteration/R4/attempt-16-four-kind-closure/evidence/canonical-e2e.json",
      },
    ],
  ],
  outputDir:
    "../artifacts/product-acceptance/ui-iteration/R4/attempt-16-four-kind-closure/evidence/playwright",
  use: {
    ...devices["Desktop Chrome"],
    baseURL: process.env.R416_BASE ?? "http://127.0.0.1:5334",
    trace: "retain-on-failure",
  },
});
