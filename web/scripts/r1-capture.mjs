import "./review-runtime.mjs";
import "./r1-isolation.mjs";
import process from "node:process";
import console from "node:console";
/* global document, getComputedStyle, innerWidth, HTMLElement */
import { chromium, expect } from "@playwright/test";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { auditContrast } from "./phase1-contrast.mjs";
const out = resolve("artifacts/product-acceptance/ui-iteration/R1/attempt-01");
const stage = process.argv[2] ?? "final";
const base = "http://127.0.0.1:5294";
const routes = JSON.parse(await readFile(resolve(out, "routes.json")));
const browser = await chromium.launch({
  executablePath: process.env.HPAGENT_BROWSER_EXECUTABLE,
  headless: true,
});
const context = await browser.newContext({
  viewport: { width: 1680, height: 1000 },
  reducedMotion: "reduce",
  locale: "zh-CN",
  timezoneId: "Asia/Shanghai",
});
const page = await context.newPage();
const errors = [];
const metrics = [];
page.on("pageerror", (e) => errors.push(e.message));
async function settled() {
  await expect(page.locator(".hp-entry")).toHaveAttribute("data-entering", "false");
  await expect(page.locator(".hp-entry-origin")).toHaveCount(0);
  await expect(page.locator(".hp-entry-destination")).not.toHaveAttribute("inert", "");
  await page.evaluate(() => document.fonts.ready);
}
async function shot(scene, width, variant) {
  await page.goto(base + "/?r1=" + variant + routes[scene]);
  await settled();
  await expect(page.locator('.hp-msg[data-role="assistant"]')).toBeVisible();
  if (scene === "ST07")
    await expect(page.locator(".hp-run-inspector .hp-inspector-card")).toBeVisible();
  await page.locator(".hp-thread__viewport").evaluate((e) => (e.scrollTop = 0));
  await page.evaluate(() => {
    if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
  });
  await page.screenshot({
    path: resolve(out, "screenshots", stage, `${scene}-${width}-${variant}.png`),
  });
  const geometry = await page.evaluate(() => {
    const regions = {};
    for (const sel of [
      ".hp-rail",
      ".hp-context-sidebar",
      ".hp-main-canvas",
      ".hp-inspector",
      ".hp-composer",
      ".hp-text-part",
    ]) {
      const el = document.querySelector(sel);
      if (el && !el.hidden) {
        const r = el.getBoundingClientRect();
        const s = getComputedStyle(el);
        regions[sel] = {
          x: r.x,
          y: r.y,
          width: r.width,
          height: r.height,
          font: s.fontFamily,
          fontSize: s.fontSize,
          lineHeight: s.lineHeight,
          background: s.backgroundColor,
        };
      }
    }
    return { width: innerWidth, document: document.documentElement.scrollWidth, regions };
  });
  expect(geometry.document).toBeLessThanOrEqual(width + 1);
  const contrast = await auditContrast(page);
  metrics.push({
    scene,
    width,
    variant,
    data: "isolated API with persisted controlled content",
    geometry,
    contrast,
  });
}
try {
  await mkdir(resolve(out, "screenshots", stage), { recursive: true });
  await page.goto(base + "/?r1=baseline");
  await page.getByLabel("用户名").fill("r1_review");
  await page.getByLabel("密码", { exact: true }).fill("r1-review-isolated-password");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await settled();
  for (const width of [1680, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    for (const scene of ["ST02", "ST03", "ST07"])
      for (const variant of ["baseline", "s1", "s2"]) await shot(scene, width, variant);
  }
  await writeFile(
    resolve(out, `logs/${stage}-metrics.json`),
    JSON.stringify({ browser: await browser.version(), errors, metrics }, null, 2),
  );
  console.log(
    `${stage}: ${metrics.length} core images, ${errors.length} page errors, ${metrics.reduce((a, m) => a + m.contrast.violations.length, 0)} sampled contrast violations`,
  );
} catch (e) {
  await page.screenshot({ path: resolve(out, `screenshots/${stage}/failure.png`) });
  throw e;
} finally {
  await browser.close();
}
