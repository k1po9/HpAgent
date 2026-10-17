import process from "node:process";
import "./review-runtime.mjs";
/* global window, getComputedStyle */
import "./r1-isolation.mjs";
import { chromium, expect } from "@playwright/test";
import { resolve } from "node:path";
import { writeFile } from "node:fs/promises";
const out = resolve("artifacts/product-acceptance/ui-iteration/R2/attempt-06");
const b = await chromium.launch({
  executablePath: process.env.HPAGENT_BROWSER_EXECUTABLE,
});
const c = await b.newContext({ viewport: { width: 1680, height: 1000 }, reducedMotion: "reduce" });
const p = await c.newPage();
const errors = [],
  checks = [];
p.on("pageerror", (e) => errors.push(e.message));
await p.goto("http://127.0.0.1:5294/");
await p.getByLabel("用户名").fill("r1_review");
await p.getByLabel("密码", { exact: true }).fill("r1-review-isolated-password");
await p.getByRole("button", { name: "登录", exact: true }).click();
await expect(p.locator(".hp-entry-origin")).toHaveCount(0, { timeout: 20000 });
await p.goto("http://127.0.0.1:5316/lab.html");
await expect(p.locator(".hp-entry-origin")).toHaveCount(0, { timeout: 20000 });
await expect(p.locator(".hp-composer")).toHaveAttribute("data-action-phase", "active");
const stop = p.locator(".hp-composer").getByRole("button", { name: "停止", exact: true });
expect(await stop.innerText()).toBe("");
await p.getByLabel("消息输入").fill("已选定粗版，准备继续。");
expect(
  await p.locator(".hp-composer").evaluate((n) => getComputedStyle(n, "::after").opacity),
).toBe("0");
await p.evaluate(() => {
  const s = window.__reviewSeed;
  window.__reviewEmit("run.succeeded", {
    snapshot: {
      ...s,
      run: { ...s.run, status: "succeeded", failure: null },
      assistant_message: { ...s.assistant_message, status: "completed" },
    },
  });
});
await expect(p.locator(".hp-composer")).toHaveAttribute("data-action-phase", "ready");
const style = await p.locator(".hp-composer").evaluate((n) => {
  const s = getComputedStyle(n, "::after");
  return { height: s.height, width: s.width, opacity: s.opacity };
});
expect(style).toEqual({ height: "3px", width: "268px", opacity: "0.84" });
await p.locator(".hp-composer").screenshot({ path: resolve(out, "screenshots/ready.png") });
checks.push({ productionCoarse: style, activeSeamHidden: true, stopIconOnly: true });
await p.getByRole("button", { name: "发送", exact: true }).hover();
await expect(p.locator(".hp-composer")).toHaveAttribute("data-action-phase", "intent");
expect(
  await p.locator(".hp-composer").evaluate((n) => getComputedStyle(n, "::after").opacity),
).toBe("0.14");
await p.mouse.move(0, 0);
await p.setViewportSize({ width: 390, height: 844 });
await expect(p.locator(".hp-composer")).toHaveAttribute("data-action-phase", "ready");
await p.locator(".hp-composer").screenshot({ path: resolve(out, "screenshots/mobile.png") });
expect(await p.locator(".hp-composer").evaluate((n) => getComputedStyle(n, "::after").height)).toBe(
  "3px",
);
expect(
  await p.locator(".hp-composer").evaluate((n) => getComputedStyle(n, "::after").animationName),
).toBe("none");
expect(errors).toEqual([]);
checks.push({ intentOpacity: ".14", mobile: "pass", reducedMotion: "pass", errors });
await writeFile(resolve(out, "logs/browser.json"), JSON.stringify(checks, null, 2));
await b.close();
