import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./e2e",
  testMatch: "run-frame.spec.ts",
  workers: 1,
  timeout: 45_000,
  use: {
    baseURL: "http://127.0.0.1:5291",
    viewport: { width: 1536, height: 1000 },
    reducedMotion: "reduce",
    trace: "retain-on-failure",
    video: "on",
  },
  webServer: {
    command: "node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5291 --strictPort",
    url: "http://127.0.0.1:5291",
    reuseExistingServer: true,
  },
});
