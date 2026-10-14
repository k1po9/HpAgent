/* global document, innerWidth, getComputedStyle */
import { chromium, expect } from "@playwright/test";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { auditContrast } from "./r13-material-contrast.mjs";
const out = resolve("artifacts/product-acceptance/ui-iteration/R2/attempt-01");
await mkdir(resolve(out, "comparison"), { recursive: true });
const routes = JSON.parse(await readFile(resolve(out, "routes.json"))),
  b = await chromium.launch({
    executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
  }),
  c = await b.newContext({ locale: "zh-CN", timezoneId: "Asia/Shanghai", reducedMotion: "reduce" }),
  p = await c.newPage();
const results = [];
await p.goto("http://127.0.0.1:5294/");
await p.getByLabel("用户名").fill("r1_review");
await p.getByLabel("密码", { exact: true }).fill("r1-review-isolated-password");
await p.getByRole("button", { name: "登录", exact: true }).click();
await expect(p.locator(".hp-entry-origin")).toHaveCount(0, { timeout: 20000 });
for (const width of [1680, 1280, 780, 390])
  for (const [version, port] of [
    ["before", 5310],
    ["after", 5294],
  ]) {
    await p.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    for (const scene of ["ai", "tasks"]) {
      await p.goto(
        `http://127.0.0.1:${port}/` +
          (scene === "ai" ? routes.user : "#/tasks?bucket=attention&type=all"),
      );
      await expect(p.locator(".hp-entry-origin")).toHaveCount(0);
      await p.waitForTimeout(500);
      await p.evaluate(() => document.activeElement?.blur());
      await p.screenshot({ path: resolve(out, `comparison/${scene}-${width}-${version}.png`) });
      const metrics = await p.evaluate(() => ({
        width: innerWidth,
        doc: document.documentElement.scrollWidth,
        font: getComputedStyle(document.body).fontFamily,
        lanes: document.querySelectorAll(".hp-task-lane").length,
        answer: document.querySelector(".hp-answer-surface")
          ? getComputedStyle(document.querySelector(".hp-answer-surface")).backgroundColor
          : null,
      }));
      expect(metrics.doc).toBeLessThanOrEqual(width);
      const content =
        scene === "ai"
          ? await p.locator("[data-message-id]").allTextContents()
          : await p.locator(".hp-task-title").allTextContents();
      results.push({ width, version, scene, metrics, content, contrast: await auditContrast(p) });
    }
    await p.getByRole("button", { name: "账户设置", exact: true }).click();
    await p.waitForTimeout(350);
    await p.screenshot({ path: resolve(out, `comparison/account-${width}-${version}.png`) });
    results.push({ width, version, scene: "account", contrast: await auditContrast(p) });
    await p.keyboard.press("Escape");
  }
for (const width of [1680, 1280, 780, 390])
  for (const scene of ["ai", "tasks"]) {
    const pair = results.filter((r) => r.width === width && r.scene === scene);
    expect(pair[0].content).toEqual(pair[1].content);
  }
await writeFile(
  resolve(out, "logs/comparison.json"),
  JSON.stringify(
    {
      browser: await b.version(),
      data: "same isolated API, real persisted records, no writes during pair capture",
      results,
    },
    null,
    2,
  ),
);
await c.close();
await b.close();
