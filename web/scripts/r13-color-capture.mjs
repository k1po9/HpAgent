/* global document, getComputedStyle, innerWidth, HTMLElement */
import "./r1-isolation.mjs";
import console from "node:console";
import process from "node:process";
import { chromium, expect } from "@playwright/test";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { auditContrast } from "./phase1-contrast.mjs";
const out = resolve("artifacts/product-acceptance/ui-iteration/R1.3/attempt-03");
const routes = JSON.parse(
  await readFile(resolve("artifacts/product-acceptance/ui-iteration/R1/attempt-01/routes.json")),
);
routes.user = "#/ai/01a1318f-e479-7d69-bdb5-ab921b23f696";
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
  await p.goto("http://127.0.0.1:5294/");
  await p.getByLabel("用户名").fill("r1_review");
  await p.getByLabel("密码", { exact: true }).fill("r1-review-isolated-password");
  await p.getByRole("button", { name: "登录", exact: true }).click();
  await settled();
  for (const width of [1680, 390])
    for (const scene of ["ST02", "ST03", "ST04", "ST07", "artifact", "user"])
      for (const variant of [
        "before",
        "after",
        ...(scene === "ST02" && width === 1680 ? ["r12"] : []),
      ]) {
        await p.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
        const base =
          variant === "before"
            ? "http://127.0.0.1:5300/"
            : variant === "r12"
              ? "http://127.0.0.1:5297/?r1=r12"
              : "http://127.0.0.1:5294/";
        await p.goto(base + routes[scene]);
        await settled();
        await expect(p.locator(".hp-msg[data-role=assistant]").first()).toBeVisible();
        if (scene === "ST07") await expect(p.locator(".hp-inspector-card")).toBeVisible();
        if (scene === "artifact")
          await expect(p.frameLocator("iframe").locator("body")).not.toBeEmpty();
        await p.locator(".hp-thread__viewport").evaluate((e) => (e.scrollTop = 0));
        if (width === 1680) await p.locator(".hp-conv--active").scrollIntoViewIfNeeded();
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
          anchor:
            "thread scrollTop=0; desktop selected conversation scrolled into view identically",
          content: await p.locator(".hp-msg .hp-text-part").allTextContents(),
          objects: await p.locator(".hp-artifact-message-copy strong").allTextContents(),
          styles: await p.evaluate(() => {
            const result = {};
            for (const sel of [
              ".hp-brand",
              ".hp-brand-symbol",
              ".hp-brand-accent",
              ".hp-canvas-header",
              ".hp-composer",
              ".hp-thread__viewport",
              ".hp-msg[data-role=assistant] .hp-text-part",
              ".hp-msg__marker--assistant svg",
              ".hp-conv--active",
              ".hp-run-inspector .hp-inspector-card",
              ".hp-artifact-message-card",
              ".hp-composer__input",
              ".hp-sidebar__header",
            ]) {
              const el = [...document.querySelectorAll(sel)].find(
                (node) => node.getBoundingClientRect().width > 0,
              );
              if (!el) continue;
              const cs = getComputedStyle(el),
                r = el.getBoundingClientRect();
              const v = { width: r.width, height: r.height };
              for (const prop of [
                "padding",
                "margin",
                "fontSize",
                "fontFamily",
                "lineHeight",
                "backgroundColor",
                "color",
                "borderColor",
                "fontWeight",
                "borderRadius",
                "boxShadow",
                "display",
              ])
                v[prop] = cs[prop];
              result[sel] = v;
            }
            return result;
          }),
          geometry,
          contrast: await auditContrast(p),
        });
        if (width === 1680 && scene === "ST02" && variant !== "r12") {
          await p.locator(".hp-brand").screenshot({ path: resolve(dir, `logo-${variant}.png`) });
          await p
            .locator(".hp-conv--active")
            .screenshot({ path: resolve(dir, `selected-${variant}.png`) });
        }
        if (scene === "ST02" && variant !== "r12") {
          const input = p.getByLabel("消息输入");
          await input.fill("继续整理交付清单");
          await p.evaluate(() => document.activeElement?.blur());
          await p.screenshot({ path: resolve(dir, `${scene}-${width}-${variant}-ready.png`) });
          metrics.push({
            name: `${scene}-${width}-${variant}-ready`,
            variant,
            scene,
            data: "same UI draft; enabled send; no send performed",
            contrast: await auditContrast(p),
          });
          await input.focus();
          await p.screenshot({ path: resolve(dir, `${scene}-${width}-${variant}-focus.png`) });
          metrics.push({
            name: `${scene}-${width}-${variant}-focus`,
            variant,
            scene,
            data: "same focused UI draft; no send performed",
            contrast: await auditContrast(p),
          });
          await input.fill("");
          await p.evaluate(() => document.activeElement?.blur());
          if (width === 1680) {
            await p.locator(".hp-artifact-message-card").hover();
            await p
              .locator(".hp-artifact-message-card")
              .screenshot({ path: resolve(dir, `detail-object-hover-${variant}.png`) });
            metrics.push({
              name: `${scene}-${width}-${variant}-hover`,
              variant,
              scene,
              data: "real object hover",
              contrast: await auditContrast(p),
            });
            await p.mouse.move(0, 0);
          }
        }
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
