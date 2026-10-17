import process from "node:process";
import "./review-runtime.mjs";
/* global getComputedStyle */
import "./r1-isolation.mjs";
import { chromium, expect } from "@playwright/test";
import { resolve } from "node:path";
import { writeFile } from "node:fs/promises";
const out = resolve("artifacts/product-acceptance/ui-iteration/R2/attempt-05");
const browser = await chromium.launch({
  executablePath: process.env.HPAGENT_BROWSER_EXECUTABLE,
});
const context = await browser.newContext({
  viewport: { width: 1680, height: 1000 },
  reducedMotion: "no-preference",
  recordVideo: { dir: resolve(out, "clips"), size: { width: 1680, height: 1000 } },
});
const p = await context.newPage();
const errors = [],
  checks = [];
p.on("pageerror", (e) => errors.push(e.message));
await p.goto("http://127.0.0.1:5294/");
await p.getByLabel("用户名").fill("r1_review");
await p.getByLabel("密码", { exact: true }).fill("r1-review-isolated-password");
await p.getByRole("button", { name: "登录", exact: true }).click();
await expect(p.locator(".hp-entry-origin")).toHaveCount(0, { timeout: 20000 });
await p.goto("http://127.0.0.1:5315/REVIEW.html");
const f = p.frameLocator("#lab");
for (const n of [1, 2, 3]) {
  await p
    .getByRole("button", { name: ["① 明显", "② 流动性增强", "③ 粗"][n - 1], exact: true })
    .click();
  await expect(f.locator(".hp-composer")).toHaveAttribute("data-action-phase", "active", {
    timeout: 20000,
  });
  await expect(f.locator(".hp-entry-origin")).toHaveCount(0, { timeout: 20000 });
  const stop = f.locator(".hp-composer").getByRole("button", { name: "停止", exact: true });
  await expect(stop).toBeVisible();
  expect(await stop.innerText()).toBe("");
  expect(await stop.evaluate((n) => n.getBoundingClientRect().width)).toBe(38);
  if (n === 1) await stop.screenshot({ path: resolve(out, "screenshots/stop-icon.png") });
  await p.getByRole("button", { name: "展示真实就绪", exact: true }).click();
  await expect(f.locator(".hp-composer")).toHaveAttribute("data-action-phase", "ready");
  await p.waitForTimeout(1300);
  await f.locator(".hp-composer").screenshot({ path: resolve(out, `screenshots/option-${n}.png`) });
  const style = await f.locator(".hp-composer").evaluate((el) => {
    const s = getComputedStyle(el, "::after");
    return {
      height: s.height,
      width: s.width,
      opacity: s.opacity,
      animation: s.animationName,
      iterations: s.animationIterationCount,
    };
  });
  checks.push({ option: n, style, iconOnly: true });
  await f.getByRole("button", { name: "发送", exact: true }).hover();
  await expect(f.locator(".hp-composer")).toHaveAttribute("data-action-phase", "intent");
  await p.waitForTimeout(400);
  expect(
    await f.locator(".hp-composer").evaluate((el) => getComputedStyle(el, "::after").opacity),
  ).toBe("0.14");
  await p.mouse.move(0, 0);
  await expect(f.locator(".hp-composer")).toHaveAttribute("data-action-phase", "ready");
  await p.emulateMedia({ reducedMotion: "reduce" });
  await expect(f.locator(".hp-composer")).toHaveAttribute("data-action-motion", "false");
  expect(
    await f.locator(".hp-composer").evaluate((el) => getComputedStyle(el, "::after").animationName),
  ).toBe("none");
  await p.emulateMedia({ reducedMotion: "no-preference" });
}
await p.setViewportSize({ width: 390, height: 844 });
await p.getByRole("button", { name: "③ 粗", exact: true }).click();
await expect(f.locator(".hp-composer")).toHaveAttribute("data-action-phase", "active", {
  timeout: 20000,
});
await expect(f.locator(".hp-entry-origin")).toHaveCount(0, { timeout: 20000 });
expect(
  await f.locator(".hp-composer").getByRole("button", { name: "停止", exact: true }).innerText(),
).toBe("");
await p.getByRole("button", { name: "展示真实就绪", exact: true }).click();
await expect(f.locator(".hp-composer")).toHaveAttribute("data-action-phase", "ready");
await p.waitForTimeout(450);
await f.locator(".hp-composer").screenshot({ path: resolve(out, "screenshots/mobile.png") });
expect(errors).toEqual([]);
checks.push({ mobile: "pass", pageErrors: errors });
const video = p.video();
await context.close();
await video.saveAs(resolve(out, "clips/fluid.webm"));
await browser.close();
await writeFile(resolve(out, "logs/browser.json"), JSON.stringify(checks, null, 2));
