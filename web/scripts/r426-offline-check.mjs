import "./review-runtime.mjs";
/* global console */
import { chromium, expect } from "@playwright/test";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { writeFile } from "node:fs/promises";
const out = resolve("artifacts/product-acceptance/ui-iteration/R4/attempt-10-r426-migration");
const browser = await chromium.launch({ args: ["--no-sandbox"] });
const p = await browser.newPage({
  viewport: { width: 1680, height: 1000 },
  locale: "zh-CN",
  timezoneId: "Asia/Shanghai",
});
const network = [],
  errors = [];
p.on("request", (r) => {
  if (/^https?:/.test(r.url())) network.push(r.url());
});
p.on("pageerror", (e) => errors.push(e.message));
try {
  await p.goto(pathToFileURL(out + "/HpAgent-R4.2.6-React-Migrated-Standalone.html").href);
  await expect(p.locator(".hp-run-detail")).toHaveCount(3);
  await expect(p.locator(".hp-entry-origin")).toHaveCount(0);
  await p.locator(".hp-run-detail").nth(1).click();
  await expect(p.locator(".hp-run-result")).toHaveText("执行 B：第二条结果，独立来源。");
  await expect(p.locator(".hp-title-capture")).toHaveAttribute("data-active", "true");
  await p.locator('.hp-inspector button[title="关闭"]').click();
  await expect(p.locator(".hp-inspector-exit")).toHaveCount(0);
  expect(network).toEqual([]);
  expect(errors).toEqual([]);
  await writeFile(
    out + "/offline-check.json",
    JSON.stringify(
      {
        fileLaunch: "pass",
        actualReact: "pass",
        titleCapture: "pass",
        close: "pass",
        network,
        errors,
      },
      null,
      2,
    ),
  );
  console.log("offline checks passed, external requests 0, pageerrors 0");
} finally {
  await browser.close();
}
