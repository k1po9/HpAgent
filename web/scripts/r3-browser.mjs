import process from "node:process";
/* global window,document,getComputedStyle,requestAnimationFrame,performance,Event */
import "./r1-isolation.mjs";
import { chromium, expect } from "@playwright/test";
import { resolve } from "node:path";
import { writeFile } from "node:fs/promises";
const out = resolve("artifacts/product-acceptance/ui-iteration/R3/attempt-01");
const b = await chromium.launch({
  executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
});
const c = await b.newContext({
  viewport: { width: 1680, height: 1000 },
  reducedMotion: "reduce",
  recordVideo: { dir: resolve(out, "clips"), size: { width: 1680, height: 1000 } },
});
const p = await c.newPage();
p.setDefaultTimeout(10000);
p.on("console", (m) => {
  if (m.text().startsWith("R3")) process.stdout.write(m.text() + "\n");
});
const errors = [],
  checks = [];
p.on("pageerror", (e) => errors.push(e.message));
try {
  await p.goto("http://127.0.0.1:5294/");
  await p.getByLabel("用户名").fill("r1_review");
  await p.getByLabel("密码", { exact: true }).fill("r1-review-isolated-password");
  await p.getByRole("button", { name: "登录", exact: true }).click();
  await expect(p.locator(".hp-entry-origin")).toHaveCount(0, { timeout: 20000 });
  async function scene(name, motion = "no-preference", width = 1680) {
    await p.emulateMedia({ reducedMotion: motion });
    await p.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    await p.goto("http://127.0.0.1:5317/lab.html?case=" + (name === "running" ? "queued" : name));
    await expect(p.locator(".hp-entry-origin")).toHaveCount(0, { timeout: 20000 });
    await expect(p.locator(".hp-entry-destination")).not.toHaveAttribute("inert", "");
    await p.getByLabel("消息输入").fill("R3明确夹具：原请求接力");
    await p.getByRole("button", { name: "发送", exact: true }).click();
    if (name === "running") {
      await expect(p.locator(".hp-vitality--compact").last()).toHaveAttribute(
        "data-vitality",
        "waiting",
      );
      await p.waitForTimeout(400);
      await p.evaluate(() => window.__reviewStatus("running"));
    }
  }
  const relay = p.locator(".hp-action-relay");
  await scene("queued");
  await expect(p.locator(".hp-vitality--compact").last()).toHaveAttribute(
    "data-vitality",
    "waiting",
  );
  await expect(relay).toHaveCount(0);
  checks.push({ case: "confirmed queued stays Waiting", pass: true });
  await p.waitForTimeout(400);
  await p.evaluate(() => {
    window.__frames = [];
    const began = performance.now();
    const tick = () => {
      const n = document.querySelector(".hp-action-relay"),
        ring = document.querySelector(
          '.hp-vitality--compact[data-handoff-run="r3-review-1"] .hv-rotor',
        );
      window.__frames.push({
        t: performance.now() - began,
        phase: n?.dataset.phase,
        head: Number(n?.dataset.head),
        travel: Number(n?.dataset.travelLength),
        arc: Number(n?.dataset.arcFraction),
        count: document.querySelectorAll(".hp-action-relay").length,
        rotation: ring && getComputedStyle(ring).animationName,
        opacity: ring && getComputedStyle(ring).opacity,
      });
      if (performance.now() - began < 2200) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
    window.__reviewStatus("running");
  });
  await expect(relay).toHaveAttribute("data-phase", "travelling");
  await p.waitForTimeout(220);
  await p.screenshot({ path: resolve(out, "screenshots/travel.png") });
  await expect
    .poll(() => relay.getAttribute("data-phase"), { intervals: [20], timeout: 5000 })
    .toBe("forming");
  await p.screenshot({ path: resolve(out, "screenshots/forming.png") });
  await expect(relay).toHaveCount(0);
  await p.waitForTimeout(150);
  await p.screenshot({ path: resolve(out, "screenshots/captured.png") });
  await p.waitForTimeout(850);
  const frames = await p.evaluate(() => window.__frames);
  const traveling = frames.filter((f) => f.phase === "travelling"),
    forming = frames.filter((f) => f.phase === "forming");
  expect(traveling.length).toBeGreaterThan(0);
  expect(forming.length).toBeGreaterThan(0);
  expect(
    traveling.every(
      (f) =>
        f.arc === 0 &&
        f.head < f.travel &&
        f.rotation === "none" &&
        f.opacity === "0" &&
        f.count === 1,
    ),
  ).toBe(true);
  expect(forming.every((f) => f.head >= f.travel && f.rotation === "none")).toBe(true);
  checks.push({ case: "travel before arc before C rotation", pass: true, frames });
  expect(
    await p
      .locator(".hp-vitality--compact")
      .last()
      .locator(".hv-rotor")
      .evaluate((n) => getComputedStyle(n).animationName),
  ).not.toBe("none");
  expect(await p.evaluate(() => window.__reviewRequests.length)).toBe(1);
  await p.getByLabel("消息输入").fill("保留后续草稿");
  await p.evaluate(() => window.__reviewStatus("succeeded"));
  await p.mouse.move(0, 0);
  await expect(p.locator(".hp-composer")).toHaveAttribute("data-action-phase", /^(ready|intent)$/);
  await expect(p.getByLabel("消息输入")).toHaveValue("保留后续草稿");
  checks.push({ case: "terminal retains newer draft", pass: true });
  await scene("rejected");
  await expect(p.getByLabel("消息输入")).toHaveValue("R3明确夹具：原请求接力");
  await expect(relay).toHaveCount(0);
  checks.push({ case: "rejected retains draft no relay", pass: true });
  await scene("unknown");
  await expect(p.getByRole("button", { name: "用原提交确认上次发送", exact: true })).toBeVisible();
  await expect(relay).toHaveCount(0);
  await expect(p.getByLabel("消息输入")).toHaveValue("R3明确夹具：原请求接力");
  await p.getByLabel("消息输入").fill("新的草稿，不覆盖未知请求");
  await p.getByRole("button", { name: "发送", exact: true }).click();
  expect(await p.evaluate(() => window.__reviewRequests.length)).toBe(1);
  await p.getByRole("button", { name: "用原提交确认上次发送", exact: true }).click();
  await expect(p.locator(".hp-vitality--compact").last()).toHaveAttribute(
    "data-vitality",
    "waiting",
  );
  await p.waitForTimeout(400);
  await p.evaluate(() => window.__reviewStatus("running"));
  await expect(relay).toHaveAttribute("data-phase", "travelling");
  const requests = await p.evaluate(() => window.__reviewRequests);
  expect(requests).toHaveLength(2);
  expect(requests[0].key).toBe(requests[1].key);
  await expect(p.getByLabel("消息输入")).toHaveValue("新的草稿，不覆盖未知请求");
  await expect(relay).toHaveCount(0);
  checks.push({ case: "unknown explicit same-key confirmation only", pass: true });
  for (const interruption of [
    "stop",
    "terminal",
    "thinking",
    "resize",
    "scroll",
    "hidden",
    "reduce",
    "surface",
    "route",
  ]) {
    await scene("running");
    await expect(relay).toHaveAttribute("data-phase", "travelling");
    if (interruption === "stop")
      await p.locator(".hp-composer").getByRole("button", { name: "停止", exact: true }).click();
    if (interruption === "terminal") {
      await expect
        .poll(() => relay.getAttribute("data-phase"), { intervals: [20], timeout: 5000 })
        .toBe("forming");
      await p.evaluate(() => window.__reviewStatus("failed"));
    }
    if (interruption === "thinking")
      await p.evaluate(() =>
        window.__reviewEmit("run.progress", { phase: "calling_model", summary: "真实阶段优先" }),
      );
    if (interruption === "resize") await p.setViewportSize({ width: 1280, height: 1000 });
    if (interruption === "scroll")
      await p.evaluate(() => window.dispatchEvent(new Event("scroll")));
    if (interruption === "hidden")
      await p.evaluate(() => {
        Object.defineProperty(document, "hidden", { configurable: true, value: true });
        document.dispatchEvent(new Event("visibilitychange"));
      });
    if (interruption === "reduce") await p.emulateMedia({ reducedMotion: "reduce" });
    if (interruption === "surface")
      await p.getByRole("button", { name: "账户设置", exact: true }).click();
    if (interruption === "route")
      await p.locator(".hp-rail").getByRole("button", { name: "任务", exact: true }).click();
    await expect(relay).toHaveCount(0);
    await p.waitForTimeout(400);
    await expect(relay).toHaveCount(0);
    if (interruption === "hidden")
      await p.evaluate(() => {
        Object.defineProperty(document, "hidden", { configurable: true, value: false });
        document.dispatchEvent(new Event("visibilitychange"));
      });
    checks.push({ case: interruption + " cancels permanently", pass: true });
  }
  await scene("running");
  await expect(relay).toHaveAttribute("data-phase", "travelling");
  await p.evaluate(() => (window.__reviewRejectCancel = true));
  await p.locator(".hp-composer").getByRole("button", { name: "停止", exact: true }).click();
  await expect(relay).toHaveCount(0);
  await expect(p.locator(".hp-vitality--compact").last()).toHaveAttribute(
    "data-vitality",
    "running",
  );
  await p.waitForTimeout(500);
  await expect(relay).toHaveCount(0);
  checks.push({ case: "rejected stop retains actual running without replay", pass: true });
  await scene("queued");
  await p.evaluate(() => window.__reviewStatus("succeeded"));
  await expect(p.locator(".hp-vitality--compact").last()).toHaveAttribute(
    "data-vitality",
    "succeeded",
  );
  await expect(relay).toHaveCount(0);
  checks.push({ case: "queued direct terminal never starts capture", pass: true });
  await scene("thinking");
  await p.waitForTimeout(400);
  await expect(p.locator(".hp-vitality--compact").last()).toHaveAttribute(
    "data-vitality",
    "thinking",
  );
  await expect(relay).toHaveCount(0);
  checks.push({ case: "early Thinking takes C ownership", pass: true });
  await scene("running", "reduce");
  await p.waitForTimeout(400);
  await expect(relay).toHaveCount(0);
  expect(
    await p
      .locator(".hp-vitality--compact")
      .last()
      .locator(".hv-rotor")
      .evaluate((n) => getComputedStyle(n).animationName),
  ).toBe("none");
  checks.push({ case: "system reduced direct state", pass: true });
  await scene("queued", "no-preference", 390);
  await p.evaluate(() => window.__reviewStatus("running"));
  await p.waitForTimeout(1600);
  await expect(relay).toHaveCount(0);
  await p.screenshot({ path: resolve(out, "screenshots/mobile.png") });
  checks.push({ case: "mobile correct fallback/capture", pass: true });
  expect(errors).toEqual([]);
  await writeFile(
    resolve(out, "logs/browser.json"),
    JSON.stringify({ checks, errors, browser: await b.version() }, null, 2),
  );
  const video = p.video();
  await c.close();
  await video.saveAs(resolve(out, "clips/fixture.webm"));
  await b.close();
} catch (error) {
  await writeFile(
    resolve(out, "logs/browser-failure.json"),
    JSON.stringify({ checks, errors, failure: String(error) }, null, 2),
  );
  await b.close();
  throw error;
}
