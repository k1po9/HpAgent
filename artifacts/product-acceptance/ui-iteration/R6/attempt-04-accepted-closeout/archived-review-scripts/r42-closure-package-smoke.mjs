/* global window, console, process */
import { chromium, expect } from "@playwright/test";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { writeFile } from "node:fs/promises";
const out = resolve("artifacts/product-acceptance/ui-iteration/R4/attempt-16-four-kind-closure");
const browser = await chromium.launch({ args: ["--no-sandbox"] });
const results = [],
  errors = [],
  network = [];
try {
  for (const mode of ["http", "offline"]) {
    const page = await browser.newPage({
      viewport: { width: 1680, height: 1000 },
      reducedMotion: "reduce",
    });
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("request", (r) => {
      if (/^https?:/.test(r.url()) && !r.url().startsWith("http://127.0.0.1:5333/"))
        network.push(r.url());
    });
    try {
      const url =
        mode === "http"
          ? "http://127.0.0.1:5333/review/after/index.html"
          : pathToFileURL(out + "/offline/AFTER.React.Standalone.html").href;
      await page.goto(url + "#/tasks?bucket=attention&type=all");
      await page.locator('.hp-entry[data-entering="false"]').waitFor();
      await page.locator("#task-ai-task").click();
      await expect(page.locator("[data-inspector-gate]")).not.toHaveAttribute("hidden");
      await page
        .locator(".hp-inspector")
        .getByRole("button", { name: "提高预算", exact: true })
        .click();
      await page.getByRole("spinbutton", { name: /模型 Token 上限/ }).fill("120000");
      await page.getByRole("button", { name: "确认提高预算", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: "提高任务预算" });
      await expect(dialog.getByRole("alert")).toBeVisible();
      await expect(dialog.getByRole("alert")).not.toContainText("undefined");
      const writes = await page.evaluate(() =>
        window.__taskRequests.filter((r) => r.method !== "GET"),
      );
      expect(writes).toHaveLength(1);
      expect(writes[0].status).toBe(409);
      await page.keyboard.press("Escape");
      await page.locator('.hp-inspector button[title="关闭"]').click();
      await expect(page.locator(".hp-inspector")).toHaveCount(0);
      results.push({ mode, result: "pass", writeStatus: writes[0].status });
    } finally {
      await page.close();
    }
  }
} finally {
  await browser.close();
  await writeFile(
    out + "/evidence/package-smoke.json",
    JSON.stringify({ results, errors, network }, null, 2),
  );
}
if (errors.length || network.length) process.exitCode = 1;
console.log(JSON.stringify({ results, errors, network }));
