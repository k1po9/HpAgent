import { chromium, expect } from "@playwright/test";
import { writeFile } from "node:fs/promises";
import { resolve } from "node:path";
const out = resolve("artifacts/product-acceptance/ui-iteration/R1.3/attempt-05");
const browser = await chromium.launch({
  executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
});
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const results = [],
  errors = [];
page.on("pageerror", (e) => errors.push(e.message));
try {
  await page.goto("http://127.0.0.1:5304/REVIEW.html");
  for (const scene of ["ST07", "ST02", "ST03", "ST04", "user"]) {
    await page.getByLabel("对照场景").selectOption(scene);
    for (const [key, view] of [
      ["1", "baseline"],
      ["2", "before"],
      ["3", "after"],
    ]) {
      await page.locator("h1").click();
      await page.keyboard.press(key);
      await expect(page.locator(`[data-view=${view}]`)).toHaveAttribute("aria-pressed", "true");
      await page.locator("#screen").evaluate((el) => el.decode());
      await expect(page.locator("#screen")).toHaveJSProperty("naturalWidth", 1680);
      await expect(page.locator("#screen")).toHaveJSProperty("naturalHeight", 960);
      results.push({ scene, view, src: await page.locator("#screen").getAttribute("src") });
    }
  }
  await page.getByLabel("对照场景").selectOption("ST07");
  await page.getByRole("button", { name: "2 · 修复前" }).click();
  await page.getByRole("button", { name: "3 · 修复后" }).click();
  await page.locator("#screen").evaluate((el) => el.decode());
  await page.locator("summary").click();
  for (const img of await page.locator(".pairs img").all()) await img.evaluate((el) => el.decode());
  await page.locator("summary").click();
  await page.screenshot({ path: resolve(out, "screenshots/review-gallery.png") });
  expect(errors).toEqual([]);
} finally {
  await writeFile(resolve(out, "logs/gallery.json"), JSON.stringify({ results, errors }, null, 2));
  await browser.close();
}
