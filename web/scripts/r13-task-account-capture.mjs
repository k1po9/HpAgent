/* global document, innerWidth */
import process from "node:process";
import "./r1-isolation.mjs";
import { chromium, expect } from "@playwright/test";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { auditContrast } from "./r13-material-contrast.mjs";
const out = resolve("artifacts/product-acceptance/ui-iteration/R1.3/attempt-06");
const stage = process.argv[2] ?? "initial";
const dir = resolve(out, "screenshots", stage);
await mkdir(dir, { recursive: true });
const b = await chromium.launch({
  executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
});
const p = await b.newPage({
  viewport: { width: 1680, height: 1000 },
  locale: "zh-CN",
  timezoneId: "Asia/Shanghai",
  reducedMotion: "reduce",
});
const errors = [],
  results = [];
p.on("pageerror", (e) => errors.push(e.message));
const routes = JSON.parse(await readFile(resolve(out, "routes.json")));
async function settled() {
  await expect(p.locator(".hp-entry-origin")).toHaveCount(0, { timeout: 20000 });
  await p.evaluate(() => document.fonts.ready);
}
try {
  await p.goto("http://127.0.0.1:5294/");
  await p.getByLabel("用户名").fill("r1_review");
  await p.getByLabel("密码", { exact: true }).fill("r1-review-isolated-password");
  await p.getByRole("button", { name: "登录", exact: true }).click();
  await settled();
  for (const width of [1680, 1280, 780, 390])
    for (const [version, port] of [
      ["before", 5307],
      ["after", 5294],
    ]) {
      await p.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
      await p.goto(`http://127.0.0.1:${port}/#/tasks?bucket=attention&type=all`);
      await settled();
      await expect(p.locator(".hp-task-screen")).toBeVisible();
      await expect(p.getByText(/本轮同步完成/).last()).toBeVisible();
      await p.evaluate(() => document.activeElement?.blur());
      await p.screenshot({ path: resolve(dir, `tasks-${width}-${version}.png`) });
      const geometry = await p.evaluate(() => ({
        width: innerWidth,
        doc: document.documentElement.scrollWidth,
        lanes: [...document.querySelectorAll(".hp-task-lane")].map((el) => ({
          bucket: el.dataset.taskLane,
          x: el.getBoundingClientRect().x,
          width: el.getBoundingClientRect().width,
        })),
      }));
      expect(geometry.doc).toBeLessThanOrEqual(width);
      results.push({
        name: `tasks-${width}-${version}`,
        geometry,
        taskTitles: await p.locator(".hp-task-title").allTextContents(),
        contrast: await auditContrast(p),
      });
      await p.getByRole("button", { name: "账户设置", exact: true }).click();
      await expect(p.getByRole("dialog", { name: "账户设置", exact: true })).toBeVisible();
      await p.evaluate(() => document.activeElement?.blur());
      await p.screenshot({ path: resolve(dir, `account-${width}-${version}.png`) });
      await p
        .getByRole("dialog", { name: "账户设置", exact: true })
        .screenshot({ path: resolve(dir, `account-local-${width}-${version}.png`) });
      results.push({ name: `account-${width}-${version}`, contrast: await auditContrast(p) });
      await p.keyboard.press("Escape");
      await expect(p.getByRole("dialog")).toHaveCount(0, { timeout: 20000 });
    }
  await p.setViewportSize({ width: 1680, height: 1000 });
  await p.goto("http://127.0.0.1:5294/" + routes.user);
  await settled();
  await p.evaluate(() => document.activeElement?.blur());
  await p.screenshot({ path: resolve(dir, "ai-neighbor.png") });
  expect(errors).toEqual([]);
} catch (error) {
  await p.screenshot({ path: resolve(dir, "failure.png") });
  results.push({ failure: error.message, pageText: await p.locator("body").innerText() });
  throw error;
} finally {
  await writeFile(
    resolve(out, `logs/capture-${stage}.json`),
    JSON.stringify({ browser: await b.version(), results, errors }, null, 2),
  );
  await b.close();
}
