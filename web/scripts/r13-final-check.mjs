import "./review-runtime.mjs";
import "./r1-isolation.mjs";
/* global document, innerWidth */
import console from "node:console";
import process from "node:process";
import { URL } from "node:url";
import { chromium, expect } from "@playwright/test";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { auditContrast } from "./phase1-contrast.mjs";
const out = resolve("artifacts/product-acceptance/ui-iteration/R1.3/attempt-01"),
  dir = resolve(out, "screenshots", process.argv[3] ?? "supplement");
await mkdir(dir, { recursive: true });
const routes = JSON.parse(await readFile(resolve(out, "routes.json"))),
  base = "http://127.0.0.1:5294";
const b = await chromium.launch({
  executablePath: process.env.HPAGENT_BROWSER_EXECUTABLE,
});
const p = await b.newPage({
  viewport: { width: 1680, height: 1000 },
  locale: "zh-CN",
  timezoneId: "Asia/Shanghai",
  reducedMotion: "reduce",
});
const results = [],
  metrics = [];
async function settled() {
  await expect(p.locator(".hp-entry-origin")).toHaveCount(0);
  await expect(p.locator(".hp-entry-destination")).not.toHaveAttribute("inert", "");
  await p.evaluate(() => document.fonts.ready);
}
async function shot(name, data) {
  const doc = await p.evaluate(() => ({
    viewport: innerWidth,
    width: document.documentElement.scrollWidth,
  }));
  expect(doc.width).toBeLessThanOrEqual(doc.viewport + 1);
  await p.screenshot({ path: resolve(dir, name + ".png") });
  metrics.push({ name, data, doc, contrast: await auditContrast(p) });
}
try {
  await p.goto(base + "/");
  await p.getByLabel("用户名").fill("r1_review");
  await p.getByLabel("密码", { exact: true }).fill("r1-review-isolated-password");
  await p.getByRole("button", { name: "登录", exact: true }).click();
  await settled();
  await p.setViewportSize({ width: 1680, height: 1000 });
  const runId = decodeURIComponent(routes.ST07.split("inspect=")[1]).split(":")[1];
  const snap = await (await p.request.get(base + "/api/v1/runs/" + runId)).json();
  for (const [status, v] of [
    ["failed", "r12"],
    ["running", "r12"],
  ]) {
    const fixture = {
      ...snap,
      run: {
        ...snap.run,
        status,
        failure:
          status === "failed"
            ? {
                code: "tool_side_effect_uncertain",
                message: "受控故障：外部操作结果待核实。",
                retryable: false,
              }
            : null,
      },
      assistant_message: {
        ...snap.assistant_message,
        status: status === "failed" ? "failed" : "pending",
        content: null,
      },
    };
    await p.route("**/api/v1/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path === "/api/v1/conversations/" + snap.run.conversation_id) {
        const r = await route.fetch();
        const body = await r.json();
        await route.fulfill({ response: r, json: { ...body, active_run: fixture } });
      } else if (path === "/api/v1/conversations/" + snap.run.conversation_id + "/messages") {
        const r = await route.fetch();
        const body = await r.json();
        await route.fulfill({
          response: r,
          json: {
            ...body,
            items: body.items.map((m) =>
              m.message_id === snap.assistant_message.message_id ? fixture.assistant_message : m,
            ),
          },
        });
      } else if (path === "/api/v1/runs/" + runId) await route.fulfill({ json: fixture });
      else if (path === "/api/v1/runs/" + runId + "/events")
        await route.fulfill({ status: 503, body: "R1 controlled SSE unavailable" });
      else if (route.request().method() !== "GET") await route.abort();
      else await route.continue();
    });
    await p.goto("about:blank");
    await p.goto(base + "/" + routes.ST02);
    await settled();
    await expect(p.getByTestId("run-label")).toHaveText(
      status === "failed" ? "运行失败" : "运行中…",
    );
    if (status === "failed") {
      await expect(p.getByTestId("unsafe-retry-message")).toBeVisible();
      await expect(p.getByTestId("retry-run")).toHaveCount(0);
    } else {
      await expect(p.getByTestId("run-degraded")).toBeVisible();
      await expect(p.getByTestId("run-label")).toHaveText("运行中…");
    }
    await shot(
      "ST06-" + status + "-" + v,
      "CONTROLLED API DTO + SSE failure on real React; no backend status changed",
    );
    results.push({ name: "controlled " + status + " safety presentation", result: "pass" });
    await p.unroute("**/api/v1/**");
  }
  await p.goto("http://127.0.0.1:5294/?r1=r12" + routes.ST02);
  await settled();
  await expect(p.locator(".hp-r1-controls")).toHaveCount(0);
  expect(await p.evaluate(() => document.documentElement.dataset.r12)).toBeUndefined();
  expect(await p.evaluate(() => document.documentElement.dataset.r1)).toBeUndefined();
  await expect(p.locator(".hp-msg[data-role=assistant] .hp-text-part")).toHaveCSS(
    "padding",
    "2px 0px 6px",
  );
  await shot(
    "production-default-ST02",
    "production dist + real isolated API; legacy query inactive, final default active",
  );
  results.push({
    name: "production defaults to reviewed static design; developer module absent",
    result: "pass",
  });
  await p.goto("http://127.0.0.1:5294/" + routes.artifact);
  await settled();
  await expect(p.frameLocator("iframe").locator("body")).not.toBeEmpty();
  await shot("production-artifact", "production dist + real isolated API");
  results.push({ name: "production Artifact integration", result: "pass" });
  console.log(results);
} finally {
  await writeFile(
    resolve(out, "logs/" + (process.argv[3] ?? "final-check") + ".json"),
    JSON.stringify({ browser: await b.version(), results, metrics }, null, 2),
  );
  await b.close();
}
