import { Buffer } from "node:buffer";
/* global window,document,innerWidth,innerHeight,devicePixelRatio */
import "./r1-isolation.mjs";
import { chromium, expect } from "@playwright/test";
import { resolve } from "node:path";
import { mkdir, writeFile, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
const out = resolve("artifacts/product-acceptance/ui-iteration/R3/attempt-01");
const b = await chromium.launch({
  executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
});
const c = await b.newContext({ viewport: { width: 1680, height: 1000 }, reducedMotion: "reduce" });
const p = await c.newPage();
const checks = [];
await p.goto("http://127.0.0.1:5294/");
await p.getByLabel("用户名").fill("r1_review");
await p.getByLabel("密码", { exact: true }).fill("r1-review-isolated-password");
await p.getByRole("button", { name: "登录", exact: true }).click();
await expect(p.locator(".hp-entry-origin")).toHaveCount(0, { timeout: 20000 });
async function check(page, name) {
  await page.goto("http://127.0.0.1:5317/lab.html?case=queued");
  await expect(page.locator(".hp-entry-origin")).toHaveCount(0, { timeout: 20000 });
  await page.getByLabel("消息输入").fill("R3响应式几何验证");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.locator(".hp-vitality--compact").last()).toHaveAttribute(
    "data-vitality",
    "waiting",
  );
  await page.waitForTimeout(450);
  await page.evaluate(() => window.__reviewStatus("running"));
  await page.waitForTimeout(100);
  const m = await page.evaluate(() => {
    const n = document.querySelector(".hp-action-relay");
    return {
      width: innerWidth,
      height: innerHeight,
      dpr: devicePixelRatio,
      documentWidth: document.documentElement.scrollWidth,
      viewBox: n?.getAttribute("viewBox"),
      phase: n?.dataset.phase,
      nodes: document.querySelectorAll(".hp-action-relay").length,
      stop: document.querySelector(".hp-composer__cancel").getBoundingClientRect().toJSON(),
    };
  });
  expect(m.documentWidth).toBeLessThanOrEqual(m.width + 1);
  expect(m.nodes).toBeLessThanOrEqual(1);
  if (m.viewBox) expect(m.viewBox).toBe(`0 0 ${m.width} ${m.height}`);
  expect(m.stop.bottom).toBeLessThanOrEqual(m.height + 1);
  await page.waitForTimeout(1500);
  await expect(page.locator(".hp-action-relay")).toHaveCount(0);
  const capture = await page.context().newCDPSession(page);
  const image = await capture.send("Page.captureScreenshot", {
    format: "png",
    captureBeyondViewport: false,
    fromSurface: true,
  });
  await writeFile(resolve(out, `screenshots/${name}.png`), Buffer.from(image.data, "base64"));
  await capture.detach();
  checks.push({ name, ...m });
}
await p.emulateMedia({ reducedMotion: "no-preference" });
for (const [w, h] of [
  [1680, 1000],
  [1280, 900],
  [1279, 900],
  [780, 900],
  [390, 844],
  [390, 650],
]) {
  await p.setViewportSize({ width: w, height: h });
  await check(p, `viewport-${w}-${h}`);
}
const state = await c.storageState();
await b.close();
const profile = await mkdtemp(resolve(tmpdir(), "hpagent-r3-zoom-"));
await mkdir(resolve(profile, "Default"), { recursive: true });
await writeFile(
  resolve(profile, "Default/Preferences"),
  JSON.stringify({ partition: { default_zoom_level: { x: Math.log(2) / Math.log(1.2) } } }),
);
const z = await chromium.launchPersistentContext(profile, {
  executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
  headless: true,
  viewport: null,
  args: ["--window-size=1680,1000"],
  reducedMotion: "no-preference",
});
await z.addCookies(state.cookies);
const q = z.pages()[0];
await check(q, "browser200");
expect(checks.at(-1).dpr).toBe(2);
await z.close();
await rm(profile, { recursive: true, force: true });
await writeFile(resolve(out, "logs/viewport.json"), JSON.stringify(checks, null, 2));
