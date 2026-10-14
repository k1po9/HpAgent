/* global window */
import "./r1-isolation.mjs";
import { chromium, expect } from "@playwright/test";
import { resolve } from "node:path";
import { writeFile } from "node:fs/promises";
const out = resolve("artifacts/product-acceptance/ui-iteration/R2/attempt-05");
const b = await chromium.launch({
  executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
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
await q.goto("http://127.0.0.1:5315/lab.html?option=2");
await expect(q.locator(".hp-composer")).toHaveAttribute("data-action-phase", "active");
await expect(q.locator(".hp-entry-origin")).toHaveCount(0, { timeout: 20000 });
await q.evaluate(() => {
  const s = window.__reviewSeed;
  window.__reviewEmit("run.succeeded", {
    snapshot: {
      ...s,
      run: { ...s.run, status: "succeeded", failure: null },
      assistant_message: { ...s.assistant_message, status: "completed" },
    },
  });
});
await q.getByLabel("消息输入").fill("准备好了，可以发送这条消息。");
await expect(q.locator(".hp-composer")).toHaveAttribute("data-action-phase", "ready");
await q.waitForTimeout(1400);
await q.getByLabel("消息输入").fill("");
await q.waitForTimeout(350);
const start = (Date.now() - epoch) / 1000;
const box = await q.locator(".hp-composer").boundingBox();
await q.getByLabel("消息输入").fill("准备好了，可以发送这条消息。");
await q.waitForTimeout(2100);
const video = q.video();
await d.close();
await video.saveAs(resolve(out, "clips/fluid-raw.webm"));
await b.close();
await writeFile(
  resolve(out, "logs/fluid-timing.json"),
  JSON.stringify({ start, box, duration: 2.1 }, null, 2),
);
