import "./review-runtime.mjs";
/* global window, document, getComputedStyle, requestAnimationFrame, performance */
import "./r1-isolation.mjs";
import process from "node:process";
import { chromium, expect } from "@playwright/test";
import { resolve } from "node:path";
import { writeFile } from "node:fs/promises";
const out = resolve("artifacts/product-acceptance/ui-iteration/R3/attempt-03");
const browser = await chromium.launch({
  executablePath: process.env.HPAGENT_BROWSER_EXECUTABLE,
});
const context = await browser.newContext({
  viewport: { width: 1680, height: 1000 },
  reducedMotion: "no-preference",
});
const page = await context.newPage(),
  errors = [],
  checks = [];
page.on("pageerror", (e) => errors.push(e.message));
page.setDefaultTimeout(10000);
await page.goto("http://127.0.0.1:5317/focus.html");
const f = page.frameLocator("#lab");
await f.getByLabel("用户名").fill("r1_review");
await f.getByLabel("密码", { exact: true }).fill("r1-review-isolated-password");
await f.getByRole("button", { name: "登录", exact: true }).click();
await expect(f.getByLabel("消息输入")).toBeVisible({ timeout: 20000 });
checks.push("fresh direct review login");
async function scene(pace = "manual", kind = "queued") {
  process.stdout.write(`scene ${pace}/${kind}\n`);
  await page
    .frames()
    .find((frame) => frame.url().includes("/lab.html"))
    .goto("http://127.0.0.1:5317/lab.html?case=" + kind + "&pace=" + pace);
  await expect(f.getByLabel("消息输入")).toBeVisible();
  await expect(f.locator(".hp-entry-destination")).not.toHaveAttribute("inert", "");
  await page.waitForTimeout(350);
  await f.getByLabel("消息输入").fill("R3真实接受时接力，不等待运行窗口");
  await f.locator(".hp-composer").evaluate(() => {
    window.__samples = [];
    window.__started = performance.now();
    const sample = () => {
      const n = document.querySelector(".hp-action-relay"),
        v = document.querySelector('.hp-vitality--compact[data-handoff-run="r3-review-1"]'),
        r = v?.querySelector(".hv-rotor");
      window.__samples.push({
        t: performance.now() - window.__started,
        phase: n?.dataset.phase,
        head: n?.dataset.head,
        travel: n?.dataset.travelLength,
        arc: n?.dataset.arcFraction,
        status: v?.dataset.vitality,
        rotor: r ? getComputedStyle(r).animationName : null,
        transform: r ? getComputedStyle(r).transform : null,
        count: document.querySelectorAll(".hp-action-relay").length,
      });
      if (performance.now() - window.__started < 3000) requestAnimationFrame(sample);
    };
    requestAnimationFrame(sample);
  });
  await f.getByRole("button", { name: "发送", exact: true }).click();
}
const relay = f.locator(".hp-action-relay"),
  signal = f.locator(".hp-vitality--compact").last();
await scene();
await expect(relay).toHaveAttribute("data-phase", "travelling");
await expect(signal).toHaveAttribute("data-vitality", "waiting");
await expect(f.locator(".hp-composer__action")).toHaveAttribute("aria-label", "停止");
await expect(relay).toHaveCount(0);
await expect(signal.locator(".hv-rotor")).toHaveCSS("animation-name", "hv-spin");
await expect(signal.locator(".hp-vitality__frame")).toHaveCSS("color", "rgb(180, 111, 18)");
await page.screenshot({ path: resolve(out, "screenshots/queued.png") });
await page.waitForTimeout(300);
const queueSamples = await f.locator(".hp-composer").evaluate(() => window.__samples);
expect(queueSamples.some((s) => s.phase === "forming" && s.status === "waiting")).toBe(true);
expect(queueSamples.filter((s) => s.phase).every((s) => s.rotor === "none")).toBe(true);
expect(queueSamples.filter((s) => s.phase === "travelling").every((s) => Number(s.arc) === 0)).toBe(
  true,
);
checks.push("accepted queued travels and forms immediately; no early spin; waiting tone unchanged");
await page.getByRole("button", { name: "开始运行", exact: true }).click();
await expect(signal).toHaveAttribute("data-vitality", "running");
await expect(signal.locator(".hp-vitality__current .hv-work")).toHaveCount(1);
await expect(signal.locator(".hp-vitality__current .hv-rotor")).toHaveCount(0);
await expect(signal.locator(".hp-vitality__frame")).toHaveCSS("color", "rgb(40, 121, 191)");
await page.screenshot({ path: resolve(out, "screenshots/running.png") });
await expect(relay).toHaveCount(0);
checks.push("running diamond; blue unchanged; no second flight");
await scene("fast");
await expect(signal).toHaveAttribute("data-vitality", "thinking");
await expect(relay).toHaveCount(1);
await expect(relay).toHaveCount(0);
await page.waitForTimeout(150);
const fastSamples = await f.locator(".hp-composer").evaluate(() => window.__samples);
expect(fastSamples.some((s) => s.phase === "forming" && s.status === "thinking")).toBe(true);
expect(fastSamples.every((s) => s.count <= 1)).toBe(true);
checks.push("quick thinking preserves one relay and current phase");
await scene();
await expect(relay).toHaveCount(1);
await page.getByRole("button", { name: "失败", exact: true }).click();
await expect(relay).toHaveCount(0);
await expect(signal).toHaveAttribute("data-vitality", "failed");
checks.push("failure interrupts");
await scene();
await expect(relay).toHaveCount(1);
await f.getByRole("button", { name: "停止", exact: true }).last().click();
await expect(relay).toHaveCount(0);
checks.push("stop interrupts");
await page.emulateMedia({ reducedMotion: "reduce" });
await scene();
await expect(relay).toHaveCount(0);
await expect(signal.locator(".hv-rotor")).toHaveCSS("animation-name", "none");
checks.push("reduced motion static waiting ring");
await page.emulateMedia({ reducedMotion: "no-preference" });
await scene("manual", "rejected");
await expect(relay).toHaveCount(0);
checks.push("rejected has no relay");
await scene("manual", "unknown");
await expect(relay).toHaveCount(0);
checks.push("uncertain has no relay before confirmation");
await scene("auto");
await expect(relay).toHaveCount(1);
await expect(signal).toHaveAttribute("data-vitality", "running");
await expect(signal).toHaveAttribute("data-vitality", "thinking");
await expect(signal).toHaveAttribute("data-vitality", "succeeded", { timeout: 10000 });
checks.push("automatic queued-running-thinking-answer-terminal cycle");
await page.goto("http://127.0.0.1:5294/#/ai");
await expect(page.getByLabel("消息输入")).toBeVisible();
await expect(page.locator(".hp-entry-destination")).not.toHaveAttribute("inert", "");
await page.waitForTimeout(350);
await page.getByLabel("消息输入").fill("R3隔离实际API：发送接受后即接力至排队信号。");
await page.evaluate(() => {
  window.__actual = [];
  const began = performance.now();
  const sample = () => {
    const n = document.querySelector(".hp-action-relay"),
      v = [...document.querySelectorAll(".hp-vitality--compact")].at(-1);
    window.__actual.push({
      t: performance.now() - began,
      phase: n?.dataset.phase,
      status: v?.dataset.vitality,
      count: document.querySelectorAll(".hp-action-relay").length,
    });
    if (performance.now() - began < 5600) requestAnimationFrame(sample);
  };
  requestAnimationFrame(sample);
});
await page.getByRole("button", { name: "发送", exact: true }).click();
await page.waitForTimeout(5500);
const actual = await page.evaluate(() => window.__actual);
expect(actual.some((s) => s.phase && s.status === "waiting")).toBe(true);
expect(actual.every((s) => s.count <= 1)).toBe(true);
await expect(page.locator(".hp-action-relay")).toHaveCount(0);
checks.push("isolated actual API/Fake SSE accepted queue visibly receives relay");
expect(errors).toEqual([]);
await writeFile(
  resolve(out, "logs/browser.json"),
  JSON.stringify({ checks, queueSamples, fastSamples, actual, errors }, null, 2),
);
await browser.close();
