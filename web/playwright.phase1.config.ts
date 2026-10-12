import process from "node:process";
import { defineConfig } from "@playwright/test";

if (process.env.HPAGENT_PHASE1_ISOLATED !== "1") {
  throw new Error("Phase 1 UI checks require a separately provisioned isolated API");
}

// Provision the isolated API separately. This never migrates or starts services.
export default defineConfig({
  testDir: "./e2e",
  testMatch: ["ui-7-unification.spec.ts", "ui-7-self-review.spec.ts", "entry/entry.spec.ts"],
  workers: 1,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  reporter: "list",
  use: {
    baseURL: process.env.HPAGENT_PHASE1_BASE_URL ?? "http://127.0.0.1:5290",
    viewport: { width: 1680, height: 1000 },
    locale: "zh-CN",
    timezoneId: "Asia/Shanghai",
    launchOptions: process.env.HPAGENT_BROWSER_EXECUTABLE
      ? { executablePath: process.env.HPAGENT_BROWSER_EXECUTABLE }
      : {},
    trace: "retain-on-failure",
  },
});
