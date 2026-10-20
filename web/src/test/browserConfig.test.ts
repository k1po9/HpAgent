import { describe, expect, it } from "vitest";
import { apiConfig, fixtureConfig, origin, port, visualConfig } from "../../e2e/config/shared";

describe("isolated browser environments", () => {
  it.each(["0", "-1", "65536", "1.5", "no-port"])("rejects invalid ports: %s", (value) => {
    expect(() => port({ WEB_DEV_PORT: value }, "WEB_DEV_PORT", 5273)).toThrow();
  });
  it("rejects overlapping API/frontend ports and implicit provided APIs", () => {
    expect(() => apiConfig({ env: { WEB_API_PORT: "8180", WEB_DEV_PORT: "8180" } })).toThrow();
    expect(() => apiConfig({ env: { HPAGENT_E2E_API_URL: "http://127.0.0.1:8180" } })).toThrow();
  });
  it("owns API and frontend servers without reusing services", () => {
    const config = apiConfig({ production: true, canonical: true, env: {} });
    expect(config.testMatch).toContain("artifact.spec.ts");
    const servers = Array.isArray(config.webServer) ? config.webServer : [];
    expect(servers).toHaveLength(2);
    expect(servers.every((server) => server.reuseExistingServer === false)).toBe(true);
    expect(servers[1]?.env?.HPAGENT_E2E_FRONTEND_MODE).toBe("production");
  });
  it("accepts only an explicit isolated provided API and starts its own frontend", () => {
    const config = apiConfig({
      env: { E2E_ISOLATED: "1", HPAGENT_E2E_API_URL: "http://127.0.0.1:41888" },
    });
    const servers = Array.isArray(config.webServer) ? config.webServer : [];
    expect(servers).toHaveLength(1);
    expect(servers[0]?.env?.HPAGENT_API_TARGET).toBe("http://127.0.0.1:41888");
    expect(servers[0]?.env?.HPAGENT_E2E_API_READY_URL).toBe("http://127.0.0.1:41888/health/ready");
    expect(servers[0]?.reuseExistingServer).toBe(false);
  });
  it("fixture servers have no reachable default API and use configurable ports", () => {
    const config = fixtureConfig({ env: { HPAGENT_FIXTURE_PORT: "5346" } });
    expect(config.use?.baseURL).toBe("http://127.0.0.1:5346");
    const server = Array.isArray(config.webServer) ? config.webServer[0] : config.webServer;
    expect(server?.env?.HPAGENT_API_TARGET).toBe("http://127.0.0.1:9");
    expect(server?.reuseExistingServer).toBe(false);
  });
  it("visual checks require both isolation and an explicit API target", () => {
    expect(() => visualConfig({})).toThrow();
    expect(() => visualConfig({ HPAGENT_VISUAL_ISOLATED: "1" })).toThrow();
    expect(
      visualConfig({ HPAGENT_VISUAL_ISOLATED: "1", HPAGENT_API_TARGET: "http://127.0.0.1:41888" })
        .use?.baseURL,
    ).toBe("http://127.0.0.1:5290");
  });
  it.each([
    "file:///tmp/a",
    "https://user:secret@example.test",
    "https://example.test/path",
    "https://example.test/?query=1",
  ])("rejects targets that are not plain HTTP origins: %s", (value) => {
    expect(() => origin(value)).toThrow();
  });
});
