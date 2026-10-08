import { defineConfig } from "@playwright/test";
import base from "./playwright.config";

const servers = Array.isArray(base.webServer) ? base.webServer : [];
export default defineConfig({
  ...base,
  testMatch: "ui-8-acceptance.spec.ts",
  webServer: [
    servers[0]!,
    { ...servers[1]!, command: `npm run preview -- --port ${process.env.WEB_DEV_PORT ?? 5281}` },
  ],
});
