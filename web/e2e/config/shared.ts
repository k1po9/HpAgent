/// <reference types="node" />
import { defineConfig, devices, type PlaywrightTestConfig } from "@playwright/test";
import process from "node:process";

type Environment = Record<string, string | undefined>;
const fixtureSpecs = [
  "run-frame.spec.ts",
  "run-overview.spec.ts",
  "run-diagnostics.spec.ts",
  "ui-7-self-review.spec.ts",
];

export function port(env: Environment, name: string, fallback: number): number {
  const value = Number(env[name] ?? fallback);
  if (!Number.isInteger(value) || value < 1 || value > 65535)
    throw new Error(`${name} must be an integer in 1..65535`);
  return value;
}

export function origin(value: string): string {
  const url = new URL(value);
  if (
    !["http:", "https:"].includes(url.protocol) ||
    url.username ||
    url.password ||
    url.pathname !== "/" ||
    url.search ||
    url.hash
  )
    throw new Error(
      "Browser targets must be HTTP(S) origins without credentials, paths or queries",
    );
  return url.origin;
}

function launchOptions(env: Environment) {
  return env.HPAGENT_BROWSER_EXECUTABLE ? { executablePath: env.HPAGENT_BROWSER_EXECUTABLE } : {};
}

function frontend(
  env: Environment,
  webPort: number,
  baseURL: string,
  mode: "dev" | "preview" | "production",
  apiTarget: string,
  checkApi = false,
) {
  return {
    command: "node scripts/e2e-frontend.mjs",
    url: baseURL,
    reuseExistingServer: false,
    timeout: 120_000,
    gracefulShutdown: { signal: "SIGTERM" as const, timeout: 5_000 },
    stdout: "pipe" as const,
    stderr: "pipe" as const,
    env: {
      WEB_DEV_PORT: String(webPort),
      HPAGENT_E2E_FRONTEND_MODE: mode,
      HPAGENT_API_TARGET: apiTarget,
      ...(checkApi ? { HPAGENT_E2E_API_READY_URL: `${apiTarget}/health/ready` } : {}),
      ...(env.HPAGENT_E2E_BUILD_DIR ? { HPAGENT_E2E_BUILD_DIR: env.HPAGENT_E2E_BUILD_DIR } : {}),
    },
  };
}

/** Real API tests own their servers unless an explicitly isolated API is supplied. */
export function apiConfig(
  options: {
    production?: boolean;
    canonical?: boolean;
    env?: Environment;
  } = {},
): PlaywrightTestConfig {
  const env = options.env ?? process.env;
  const apiPort = port(env, "WEB_API_PORT", 8180);
  const webPort = port(env, "WEB_DEV_PORT", 5273);
  if (apiPort === webPort) throw new Error("API and frontend ports must differ");
  const provided = env.HPAGENT_E2E_API_URL;
  if (provided && env.E2E_ISOLATED !== "1")
    throw new Error("A provided API requires E2E_ISOLATED=1 and dedicated test resources");
  const apiTarget = provided ? origin(provided) : `http://127.0.0.1:${apiPort}`;
  const baseURL = `http://localhost:${webPort}`;
  return defineConfig({
    testDir: "./e2e",
    testIgnore: ["**/entry/**", "**/visual/**", ...fixtureSpecs],
    ...(options.canonical
      ? {
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
        }
      : {}),
    timeout: 60_000,
    expect: { timeout: 15_000 },
    fullyParallel: false,
    workers: 1,
    retries: env.CI ? 1 : 0,
    reporter: "list",
    outputDir: options.canonical ? "./test-results/canonical" : "./test-results/api",
    use: { baseURL, trace: "retain-on-failure", launchOptions: launchOptions(env) },
    webServer: [
      ...(provided
        ? []
        : [
            {
              command: "bash scripts/e2e-backend.sh",
              url: `${apiTarget}/health/ready`,
              reuseExistingServer: false,
              timeout: 120_000,
              stdout: "pipe" as const,
              stderr: "pipe" as const,
            },
          ]),
      frontend(
        env,
        webPort,
        baseURL,
        options.production ? "production" : "dev",
        apiTarget,
        !!provided,
      ),
    ],
    projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  });
}

/** Browser-intercepted contracts and local harnesses have no reachable API by default. */
export function fixtureConfig(
  options: {
    built?: boolean;
    testMatch?: PlaywrightTestConfig["testMatch"];
    env?: Environment;
  } = {},
): PlaywrightTestConfig {
  const env = options.env ?? process.env;
  const webPort = port(
    env,
    options.built ? "HPAGENT_ENTRY_PORT" : "HPAGENT_FIXTURE_PORT",
    options.built ? 5311 : 5312,
  );
  const existing = options.built ? env.HPAGENT_ENTRY_BASE_URL : undefined;
  const baseURL = existing ? origin(existing) : `http://127.0.0.1:${webPort}`;
  return defineConfig({
    testDir: options.built ? "./e2e/entry" : "./e2e",
    testMatch: options.testMatch ?? (options.built ? "entry.spec.ts" : fixtureSpecs),
    workers: 1,
    timeout: 45_000,
    expect: { timeout: 7_000 },
    reporter: "list",
    outputDir: options.built ? "./test-results/contract" : "./test-results/fixtures",
    use: {
      baseURL,
      viewport: { width: 1536, height: 1000 },
      contextOptions: { reducedMotion: "reduce" },
      launchOptions: launchOptions(env),
      trace: "retain-on-failure",
    },
    webServer: existing
      ? undefined
      : frontend(env, webPort, baseURL, options.built ? "preview" : "dev", "http://127.0.0.1:9"),
  });
}

/** Visual checks use a separately provisioned, explicitly isolated real API. */
export function visualConfig(env: Environment = process.env): PlaywrightTestConfig {
  if (env.HPAGENT_VISUAL_ISOLATED !== "1" || !env.HPAGENT_API_TARGET)
    throw new Error(
      "Visual checks require HPAGENT_VISUAL_ISOLATED=1 and an explicit isolated HPAGENT_API_TARGET",
    );
  const webPort = port(env, "HPAGENT_VISUAL_PORT", 5290);
  const baseURL = `http://127.0.0.1:${webPort}`;
  return defineConfig({
    testDir: "./e2e/visual",
    testMatch: "frontend-visual.spec.ts",
    workers: 1,
    timeout: 60_000,
    expect: { timeout: 15_000 },
    reporter: "list",
    outputDir: "./test-results/visual",
    use: {
      baseURL,
      viewport: { width: 1536, height: 1024 },
      locale: "zh-CN",
      timezoneId: "Asia/Shanghai",
      deviceScaleFactor: 1,
      serviceWorkers: "block",
      trace: "off",
      launchOptions: launchOptions(env),
    },
    webServer: frontend(env, webPort, baseURL, "dev", origin(env.HPAGENT_API_TARGET)),
    projects: [{ name: "chromium", use: { browserName: "chromium" } }],
  });
}
