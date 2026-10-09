import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  workers: 1,
  timeout: 30000,
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:5310",
    viewport: { width: 1440, height: 1100 },
    browserName: "chromium",
  },
  webServer: {
    command: "npm run dev -- --strictPort",
    url: "http://127.0.0.1:5310",
    reuseExistingServer: !process.env.CI,
  },
});
