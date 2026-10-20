import { defineConfig } from "@playwright/test";
import { fixtureConfig } from "./e2e/config/shared";
const base = fixtureConfig({ testMatch: "run-frame.spec.ts" });
export default defineConfig({
  ...base,
  use: {
    ...base.use,
    viewport: { width: 1536, height: 1000 },
    contextOptions: { reducedMotion: "reduce" },
  },
});
