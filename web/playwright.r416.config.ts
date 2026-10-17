// Compatibility name: canonical regressions now use the formal E2E sources and environment.
import { defineConfig } from "@playwright/test";
import base from "./playwright.config";

export default defineConfig({
  ...base,
  testMatch: [
    "artifact.spec.ts",
    "auth.spec.ts",
    "conversation.spec.ts",
    "manual-repair.spec.ts",
    "ui-2-ai.spec.ts",
    "ui-3-execution.spec.ts",
    "ui-8-acceptance.spec.ts",
    "workspace-p1.spec.ts",
    "workspace-p3.spec.ts",
  ],
  // Canonical runs exercise production assets. The general config retains dev-server coverage.
  webServer: (Array.isArray(base.webServer) ? base.webServer : []).map((server, index) =>
    index === 1
      ? {
          ...server,
          command: "node scripts/e2e-preview.mjs",
          timeout: 120_000,
          gracefulShutdown: { signal: "SIGTERM", timeout: 5_000 },
          stdout: "pipe",
          stderr: "pipe",
        }
      : { ...server, stdout: "pipe", stderr: "pipe" },
  ),
  outputDir: "./test-results/canonical",
});
