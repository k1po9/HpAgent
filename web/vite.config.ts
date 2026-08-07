import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Dev server proxies same-origin API calls to the FastAPI backend so the
// browser talks to the frontend origin and cookies are first-party.
// `hpagent-api` exposes everything under /api and /auth on its own port.
const API_TARGET = process.env.HPAGENT_API_TARGET ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: API_TARGET, changeOrigin: true },
      "/auth": { target: API_TARGET, changeOrigin: true },
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
  },
});
