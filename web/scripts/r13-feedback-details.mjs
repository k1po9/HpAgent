import process from "node:process";
import "./review-runtime.mjs";
/* global document, getComputedStyle */
import "./r1-isolation.mjs";
import { chromium, expect } from "@playwright/test";
import { readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
const out = resolve("artifacts/product-acceptance/ui-iteration/R1.3/attempt-05");
const routes = JSON.parse(await readFile(resolve(out, "routes.json")));
const captures = JSON.parse(await readFile(resolve(out, "logs/capture-initial.json")));
const pairs = [];
for (const before of captures.metrics.filter((m) => m.variant === "before" && m.content)) {
  const after = captures.metrics.find((m) => m.name === before.name.replace("-before", "-after"));
  expect(after.content).toEqual(before.content);
  expect(after.objects).toEqual(before.objects);
  pairs.push({ before: before.name, after: after.name, sameMessages: true, sameObjects: true });
}
const browser = await chromium.launch({
  executablePath: process.env.HPAGENT_BROWSER_EXECUTABLE,
});
const page = await browser.newPage({
  viewport: { width: 1680, height: 960 },
  locale: "zh-CN",
  timezoneId: "Asia/Shanghai",
  reducedMotion: "reduce",
});
const proof = [];
try {
  for (const [variant, port] of [
    ["before", 5303],
    ["after", 5294],
  ]) {
    await page.goto(`http://127.0.0.1:${port}/`);
    await expect(page.locator(".hp-shell").or(page.getByLabel("用户名"))).toBeVisible();
    if (await page.getByLabel("用户名").isVisible()) {
      await page.getByLabel("用户名").fill("r1_review");
      await page.getByLabel("密码", { exact: true }).fill("r1-review-isolated-password");
      await page.getByRole("button", { name: "登录", exact: true }).click();
    }
    await expect(page.locator(".hp-entry-origin")).toHaveCount(0);
    await page.goto(`http://127.0.0.1:${port}/` + routes.ST07);
    await expect(page.locator(".hp-inspector-card")).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    await page.locator(".hp-conv--active").scrollIntoViewIfNeeded();
    await page.locator(".hp-thread__viewport").evaluate((el) => (el.scrollTop = 0));
    for (const [name, selector] of [
      ["sidebar", ".hp-context-sidebar"],
      ["inspector", ".hp-inspector"],
      ["reading", ".hp-answer-surface"],
    ])
      await page
        .locator(selector)
        .first()
        .screenshot({ path: resolve(out, "screenshots/initial", `detail-${name}-${variant}.png`) });
    if (variant === "after") {
      const material = await page.evaluate(() => {
        const css = (selector) => getComputedStyle(document.querySelector(selector));
        return {
          canvasGradient: css(".hp-main-canvas").backgroundImage,
          headerBackground: css(".hp-canvas-header").backgroundColor,
          inputBorder: css(".hp-composer__input").borderWidth,
          toolbarDivider: css(".hp-composer__file-actions").borderRightWidth,
        };
      });
      expect(material.headerBackground).toBe("rgba(0, 0, 0, 0)");
      expect(material.inputBorder).toBe("0px");
      expect(material.toolbarDivider).toBe("0px");
      await page.getByLabel("消息输入").focus();
      await page.keyboard.type("R1 review draft");
      const focus = await page.locator(".hp-composer").evaluate((el) => ({
        outline: getComputedStyle(el).outlineStyle,
        width: getComputedStyle(el).outlineWidth,
        color: getComputedStyle(el).outlineColor,
      }));
      expect(focus.outline).toBe("solid");
      await page
        .locator(".hp-composer")
        .screenshot({ path: resolve(out, "screenshots/initial/detail-composer-focus-after.png") });
      await page.getByLabel("消息输入").fill("");
      proof.push({ material, focus });
    }
  }
} finally {
  await writeFile(
    resolve(out, "logs/comparison-proof.json"),
    JSON.stringify({ pairs, proof }, null, 2),
  );
  await browser.close();
}
