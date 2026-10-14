/* global document, getComputedStyle, innerWidth, HTMLElement */
import "./r1-isolation.mjs";
import console from "node:console";
import process from "node:process";
import { chromium, expect } from "@playwright/test";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { auditContrast } from "./phase1-contrast.mjs";
const out = resolve("artifacts/product-acceptance/ui-iteration/R1.2/attempt-01");
const routes = JSON.parse(
  await readFile(resolve("artifacts/product-acceptance/ui-iteration/R1/attempt-01/routes.json")),
);
const stage = process.argv[2] ?? "initial";
const dir = resolve(out, "screenshots", stage);
await mkdir(dir, { recursive: true });
const b = await chromium.launch({
  executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
});
const p = await b.newPage({
  viewport: { width: 1680, height: 1000 },
  reducedMotion: "reduce",
  locale: "zh-CN",
  timezoneId: "Asia/Shanghai",
});
const metrics = [],
  errors = [];
p.on("pageerror", (e) => errors.push(e.message));
async function settled() {
  await expect(p.locator(".hp-entry-origin")).toHaveCount(0);
  await expect(p.locator(".hp-entry-destination")).not.toHaveAttribute("inert", "");
  await p.evaluate(() => document.fonts.ready);
}
try {
  await p.goto("http://127.0.0.1:5294/?r1=r12");
  await p.getByLabel("用户名").fill("r1_review");
  await p.getByLabel("密码", { exact: true }).fill("r1-review-isolated-password");
  await p.getByRole("button", { name: "登录", exact: true }).click();
  await settled();
  for (const width of [1680, 390])
    for (const scene of ["ST02", "ST03", "ST04", "ST07", "artifact"])
      for (const variant of ["old", "refined"]) {
        await p.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
        const base =
          variant === "old" ? "http://127.0.0.1:5296/?r1=s1" : "http://127.0.0.1:5294/?r1=r12";
        await p.goto(base + routes[scene]);
        await settled();
        await expect(p.locator(".hp-msg[data-role=assistant]").first()).toBeVisible();
        if (scene === "ST07") await expect(p.locator(".hp-inspector-card")).toBeVisible();
        if (scene === "artifact")
          await expect(p.frameLocator("iframe").locator("body")).not.toBeEmpty();
        await p.locator(".hp-thread__viewport").evaluate((e) => (e.scrollTop = 0));
        await p.evaluate(() => {
          if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
        });
        const name = `${scene}-${width}-${variant}`;
        await p.screenshot({ path: resolve(dir, name + ".png") });
        const geometry = await p.evaluate(() => {
          const regions = {};
          for (const sel of [
            ".hp-rail",
            ".hp-context-sidebar",
            ".hp-canvas-header",
            ".hp-main-canvas",
            ".hp-composer",
            ".hp-inspector",
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
                background: s.backgroundColor,
              };
            }
          }
          return { width: innerWidth, doc: document.documentElement.scrollWidth, regions };
        });
        expect(geometry.doc).toBeLessThanOrEqual(width + 1);
        metrics.push({
          name,
          scene,
          variant,
          viewport: { width, height: width === 390 ? 844 : 1000 },
          data: "same real isolated API + persisted controlled content / Fake executors",
          anchor: "thread scrollTop=0",
          content: await p.locator(".hp-msg .hp-text-part").allTextContents(),
          objects: await p.locator(".hp-artifact-message-title").allTextContents(),
          geometry,
          contrast: await auditContrast(p),
        });
        // Native resolution local evidence, saved after whole-page comparison.
        if (width === 1680 && scene === "ST02")
          for (const [part, selector] of [
            ["header", ".hp-canvas-header"],
            ["composer", ".hp-composer"],
            ["object", ".hp-artifact-message-card"],
          ])
            await p
              .locator(selector)
              .first()
              .screenshot({ path: resolve(dir, `detail-${part}-${variant}.png`) });
        if (width === 390 && scene === "ST07")
          await p
            .locator(".hp-inspector-card")
            .screenshot({ path: resolve(dir, `detail-run-${variant}.png`) });
      }
  await writeFile(resolve(out, "routes.json"), JSON.stringify(routes, null, 2));
  console.log(
    stage,
    metrics.length,
    "core images; errors:",
    errors.length,
    "contrast violations:",
    metrics.reduce((sum, m) => sum + m.contrast.violations.length, 0),
  );
} finally {
  await writeFile(
    resolve(out, `logs/capture-${stage}.json`),
    JSON.stringify({ browser: await b.version(), errors, metrics }, null, 2),
  );
  await b.close();
}
