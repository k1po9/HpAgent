/* global window, document, EventTarget, requestAnimationFrame, performance */
import { chromium, expect } from "@playwright/test";
import { writeFile } from "node:fs/promises";
import { resolve } from "node:path";
const out = resolve("artifacts/product-acceptance/ui-iteration/R2/attempt-01");
const b = await chromium.launch({
    executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
  }),
  c = await b.newContext({ viewport: { width: 1680, height: 1000 }, reducedMotion: "reduce" });
await c.addInitScript(() => {
  const tracked = new Map();
  const add = EventTarget.prototype.addEventListener,
    remove = EventTarget.prototype.removeEventListener;
  EventTarget.prototype.addEventListener = function (type, fn, opts) {
    if (
      (this === window || this === document) &&
      ["resize", "visibilitychange", "storage", "hpagent-motion-change"].includes(type)
    ) {
      let s = tracked.get(type);
      if (!s) tracked.set(type, (s = new Map()));
      s.set(fn, this);
    }
    return add.call(this, type, fn, opts);
  };
  EventTarget.prototype.removeEventListener = function (type, fn, opts) {
    tracked.get(type)?.delete(fn);
    return remove.call(this, type, fn, opts);
  };
  window.r2Counts = () => Object.fromEntries([...tracked].map(([k, v]) => [k, v.size]));
});
const p = await c.newPage();
await p.goto("http://127.0.0.1:5294/");
await p.getByLabel("用户名").fill("r1_review");
await p.getByLabel("密码", { exact: true }).fill("r1-review-isolated-password");
await p.getByRole("button", { name: "登录", exact: true }).click();
await expect(p.locator(".hp-entry-origin")).toHaveCount(0, { timeout: 20000 });
await p.emulateMedia({ reducedMotion: "no-preference" });
await p.waitForTimeout(700);
const before = await p.evaluate(() => window.r2Counts());
await p.evaluate(() => {
  window.r2Frames = [];
  let prev = performance.now();
  window.r2FrameStop = false;
  const loop = (t) => {
    window.r2Frames.push(t - prev);
    prev = t;
    if (!window.r2FrameStop) requestAnimationFrame(loop);
  };
  requestAnimationFrame(loop);
});
const nav = p.getByRole("navigation", { name: "主导航" });
for (let i = 0; i < 20; i++) {
  await nav.getByRole("button", { name: ["空间", "任务", "AI"][i % 3], exact: true }).click();
  await p.waitForTimeout(50);
  await p.getByRole("button", { name: "账户设置", exact: true }).click();
  await p.waitForTimeout(40);
  await p.keyboard.press("Escape");
}
await nav.getByRole("button", { name: "AI", exact: true }).click();
await p.waitForTimeout(600);
const after = await p.evaluate(() => {
  window.r2FrameStop = true;
  return {
    listeners: window.r2Counts(),
    frames: window.r2Frames,
    decorations: document.querySelectorAll(".hp-surface-retreat").length,
    animations: document.querySelector(".hp-rail-track").getAnimations().length,
  };
});
expect(after.listeners).toEqual(before);
expect(after.decorations).toBe(0);
expect(after.animations).toBe(0);
await p.getByRole("button", { name: "账户设置", exact: true }).click();
await p.waitForTimeout(30);
await p.getByRole("button", { name: "退出登录", exact: true }).click();
await expect(p.locator(".hp-shell")).toHaveCount(0);
await p.waitForTimeout(400);
const signedOut = await p.evaluate(() => ({
  listeners: window.r2Counts(),
  decorations: document.querySelectorAll(".hp-surface-retreat").length,
}));
expect(signedOut.decorations).toBe(0);
await writeFile(
  resolve(out, "logs/lifecycle.json"),
  JSON.stringify(
    {
      before,
      after,
      signedOut,
      browser: await b.version(),
      environment:
        "Windows Chrome headless; 1680x1000; no throttling; rAF intervals diagnostic, includes automation, not a paint/fps guarantee",
    },
    null,
    2,
  ),
);
await c.close();
await b.close();
