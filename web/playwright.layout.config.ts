import { defineConfig } from "@playwright/test";
import phase1 from "./playwright.phase1.config";

// Scoped visual composition regressions; isolated API is provisioned separately.
export default defineConfig({
  ...phase1,
  testMatch: [
    "ui-7-unification.spec.ts",
    "ui-7-self-review.spec.ts",
    "ui-2-ai.spec.ts",
    "entry/entry.spec.ts",
  ],
});
