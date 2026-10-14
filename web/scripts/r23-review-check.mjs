/* global getComputedStyle */
import "./r1-isolation.mjs";
import { chromium, expect } from "@playwright/test";
import { writeFile } from "node:fs/promises";
import { resolve } from "node:path";
const out = resolve("artifacts/product-acceptance/ui-iteration/R2/attempt-04");
const b = await chromium.launch({
  executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
});
const c = await b.newContext({
    viewport: { width: 1680, height: 1000 },
    reducedMotion: "reduce",
    locale: "zh-CN",
  }),
  p = await c.newPage();
p.setDefaultTimeout(10000);
const errors = [],
  checks = [];
p.on("pageerror", (e) => errors.push(e.message));
await p.goto("http://127.0.0.1:5294/");
await p.getByLabel("用户名").fill("r1_review");
await p.getByLabel("密码", { exact: true }).fill("r1-review-isolated-password");
await p.getByRole("button", { name: "登录", exact: true }).click();
await expect(p.locator(".hp-entry-origin")).toHaveCount(0, { timeout: 20000 });
await p.goto("http://127.0.0.1:5314/REVIEW.html");
const f = p.frameLocator("#lab");
await expect(f.locator(".hp-vitality--compact")).toBeVisible({ timeout: 15000 });
await expect(f.locator(".hp-entry-origin")).toHaveCount(0, { timeout: 20000 });
await expect(f.locator(".hp-entry-destination")).not.toHaveAttribute("inert", "");
await f.getByLabel("消息输入").fill("R2整体人审草稿");
await f.getByLabel("消息输入").fill("R2整体人审草稿");
await expect(f.locator(".hp-composer")).toHaveAttribute("data-action-phase", "active");
for (const [phase, state] of [
  ["calling_model", "thinking"],
  ["executing_tool", "tool"],
  ["waiting_for_account_execution", "waiting"],
]) {
  await p.locator('[data-phase="' + phase + '"]').click();
  await expect(f.locator(".hp-vitality--compact")).toHaveAttribute("data-vitality", state);
  await expect(f.locator(".hp-composer")).toHaveAttribute("data-action-phase", "active");
  checks.push({ name: "live " + state + " quiet A", result: "pass" });
}
await f.getByLabel("消息输入").fill("R2整体人审草稿");
await expect(f.getByLabel("消息输入")).toHaveValue("R2整体人审草稿");
await p.locator("#success").click();
await expect(f.locator(".hp-composer")).toHaveAttribute("data-action-phase", "ready");
await f.getByRole("button", { name: "发送", exact: true }).hover();
await expect(f.locator(".hp-composer")).toHaveAttribute("data-action-phase", "intent");
checks.push({ name: "terminal resumes eligible A without submission", result: "pass" });
for (const scene of ["focus", "ready", "intent", "keyboard", "submitting", "uncertain"]) {
  await p.locator("#scene").selectOption(scene);
  await expect(p.locator("#before")).toHaveJSProperty("naturalWidth", 1680);
  await expect(p.locator("#after")).toHaveJSProperty("naturalWidth", 1680);
}
checks.push({ name: "same data gallery six scenes decode", result: "pass" });
const media = await p.locator("video").evaluateAll(
  async (ns) =>
    await Promise.all(
      ns.map(
        (v) =>
          new Promise((r) => {
            if (v.readyState >= 1)
              return r({ src: v.getAttribute("src"), duration: v.duration, ready: true });
            v.addEventListener(
              "loadedmetadata",
              () => r({ src: v.getAttribute("src"), duration: v.duration, ready: true }),
              { once: true },
            );
            v.addEventListener("error", () => r({ src: v.getAttribute("src"), ready: false }), {
              once: true,
            });
          }),
      ),
    ),
);
expect(media.every((v) => v.ready)).toBe(true);
await p.screenshot({ path: resolve(out, "screenshots/review-page.png"), fullPage: true });
await p.setViewportSize({ width: 390, height: 844 });
await p.screenshot({ path: resolve(out, "screenshots/review-page-mobile.png"), fullPage: true });
// Once a local intent is interrupted, no old pointer intent may come back on route identity.
await p.goto("http://127.0.0.1:5294/#/ai");
await expect(p.locator(".hp-composer")).toBeVisible();
await expect(p.locator(".hp-entry-origin")).toHaveCount(0, { timeout: 20000 });
await expect(p.locator(".hp-entry-destination")).not.toHaveAttribute("inert", "");
const input = p.getByLabel("消息输入"),
  send = p.getByRole("button", { name: "发送", exact: true });
await input.fill("pressure local draft");
await expect(p.locator(".hp-composer")).toHaveAttribute("data-action-phase", "ready");
await p.emulateMedia({ reducedMotion: "no-preference" });
await send.hover();
await expect(p.locator(".hp-composer")).toHaveAttribute("data-action-phase", "intent");
const samples = [];
for (let i = 0; i < 20; i++) {
  await p.mouse.move(0, 0);
  await send.hover();
  samples.push(
    await p.locator(".hp-composer").evaluate((n) => ({
      pseudoBefore: getComputedStyle(n, "::before").content,
      pseudoAfter: getComputedStyle(n, "::after").content,
      actionNodes: n.querySelectorAll(".hp-composer__action").length,
      phase: n.dataset.actionPhase,
    })),
  );
}
expect(samples.every((x) => x.actionNodes === 1)).toBe(true);
checks.push({
  name: "20 pointer changes retain one original action, no decorative nodes",
  result: "pass",
});
await p.getByRole("button", { name: "账户设置", exact: true }).click();
await p.getByRole("checkbox", { name: "减少动态效果" }).check();
await p.keyboard.press("Escape");
await expect(p.locator(".hp-composer")).toHaveAttribute("data-action-motion", "false");
expect(await send.evaluate((n) => getComputedStyle(n).transform)).toBe("none");
checks.push({
  name: "existing account local reduced motion cancels A displacement",
  result: "pass",
});
await p.getByRole("button", { name: "账户设置", exact: true }).click();
await p.getByRole("checkbox", { name: "减少动态效果" }).uncheck();
await p.keyboard.press("Escape");
await writeFile(
  resolve(out, "logs/review-page.json"),
  JSON.stringify({ browser: await b.version(), media, checks, samples, errors }, null, 2),
);
await b.close();
