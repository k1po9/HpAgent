import process from "node:process";
import "./review-runtime.mjs";
import { chromium, expect } from "@playwright/test";
import { writeFile } from "node:fs/promises";
import { resolve } from "node:path";
const out = resolve("artifacts/product-acceptance/ui-iteration/R1.3/attempt-06");
const b = await chromium.launch({
  executablePath: process.env.HPAGENT_BROWSER_EXECUTABLE,
});
const p = await b.newPage({ viewport: { width: 1280, height: 950 } });
const results = [],
  errors = [];
p.on("pageerror", (e) => errors.push(e.message));
try {
  await p.goto("http://127.0.0.1:5308/REVIEW.html");
  for (const scene of ["tasks", "account"])
    for (const width of ["1680", "1280", "780", "390"])
      for (const [key, view] of [
        ["1", "before"],
        ["2", "after"],
      ]) {
        await p.getByLabel("对照场景").selectOption(scene);
        await p.getByLabel("视口宽度").selectOption(width);
        await p.locator("h1").click();
        await p.keyboard.press(key);
        await expect(p.locator(`[data-view=${view}]`)).toHaveAttribute("aria-pressed", "true");
        await p.locator("#screen").evaluate((el) => el.decode());
        await expect(p.locator("#screen")).toHaveJSProperty("naturalWidth", Number(width));
        results.push({ scene, width, view, src: await p.locator("#screen").getAttribute("src") });
      }
  await p.getByLabel("对照场景").selectOption("tasks");
  await p.getByLabel("视口宽度").selectOption("1680");
  await p.getByRole("button", { name: "2 · 实施后" }).click();
  await p.locator("#screen").evaluate((el) => el.decode());
  await p.locator("h1").scrollIntoViewIfNeeded();
  await p.screenshot({ path: resolve(out, "screenshots/review-gallery.png") });
  expect(errors).toEqual([]);
} finally {
  await writeFile(resolve(out, "logs/gallery.json"), JSON.stringify({ results, errors }, null, 2));
  await b.close();
}
