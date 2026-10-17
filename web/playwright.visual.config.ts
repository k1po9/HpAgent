import { defineConfig } from "@playwright/test";

// Vite only: provision an isolated real API separately; never run migrations here.
const port = Number(process.env.HPAGENT_VISUAL_PORT ?? 5290);
export default defineConfig({
  testDir: "./e2e/visual",
  testMatch: "frontend-visual.spec.ts",
  workers: 1,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  reporter: "list",
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    viewport: { width: 1536, height: 1024 },
    locale: "zh-CN",
    timezoneId: "Asia/Shanghai",
    deviceScaleFactor: 1,
    serviceWorkers: "block",
    trace: "off",
  },
  webServer: {
    command: `npm run dev -- --host 127.0.0.1 --port ${port} --strictPort`,
    url: `http://127.0.0.1:${port}`,
    reuseExistingServer: false,
    env: { HPAGENT_API_TARGET: process.env.HPAGENT_API_TARGET ?? "http://127.0.0.1:8196" },
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
