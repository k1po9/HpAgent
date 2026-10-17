import process from "node:process";
import "./review-runtime.mjs";
/* global window */
import "./r1-isolation.mjs";
import { chromium, expect } from "@playwright/test";
import { resolve } from "node:path";
import { writeFile } from "node:fs/promises";
const out = resolve("artifacts/product-acceptance/ui-iteration/R3/attempt-01");
const b = await chromium.launch({
  executablePath: process.env.HPAGENT_BROWSER_EXECUTABLE,
});
const c = await b.newContext();
const p = await c.newPage();
await p.goto("http://127.0.0.1:5294/");
await p.getByLabel("用户名").fill("r1_review");
await p.getByLabel("密码", { exact: true }).fill("r1-review-isolated-password");
await p.getByRole("button", { name: "登录", exact: true }).click();
await expect(p.locator(".hp-entry-origin")).toHaveCount(0, { timeout: 20000 });
const storageState = await c.storageState();
await c.close();
const d = await b.newContext({
  storageState,
  viewport: { width: 1680, height: 1000 },
  reducedMotion: "no-preference",
  recordVideo: { dir: resolve(out, "clips"), size: { width: 1680, height: 1000 } },
});
const q = await d.newPage();
const epoch = Date.now();
await q.goto("http://127.0.0.1:5317/lab.html?case=queued");
await expect(q.locator(".hp-entry-origin")).toHaveCount(0, { timeout: 20000 });
await q.getByLabel("消息输入").fill("R3确认后，光迹抵达再成环。");
await q.getByRole("button", { name: "发送", exact: true }).click();
await expect(q.locator(".hp-vitality--compact").last()).toHaveAttribute("data-vitality", "waiting");
await q.waitForTimeout(600);
const answer = await q
  .locator('[data-message-id="r3-assistant-1"] .hp-answer-surface')
  .boundingBox();
const composer = await q.locator(".hp-composer").boundingBox();
const box = {
  x: answer.x,
  y: answer.y - 32,
  width: answer.width,
  height: composer.y + composer.height - answer.y + 32,
};
const start = (Date.now() - epoch) / 1000;
await q.evaluate(() => window.__reviewStatus("running"));
await q.waitForTimeout(2000);
await expect(q.locator(".hp-action-relay")).toHaveCount(0);
const video = q.video();
await d.close();
await video.saveAs(resolve(out, "clips/capture-raw.webm"));
await b.close();
await writeFile(
  resolve(out, "logs/capture-timing.json"),
  JSON.stringify({ start, box, duration: 2.1 }, null, 2),
);
