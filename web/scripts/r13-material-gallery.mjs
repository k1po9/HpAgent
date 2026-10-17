import process from "node:process";
import "./review-runtime.mjs";
import { chromium, expect } from "@playwright/test";
import { writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import console from "node:console";
const b = await chromium.launch({
  executablePath: process.env.HPAGENT_BROWSER_EXECUTABLE,
});
const p = await b.newPage({ viewport: { width: 1280, height: 900 } });
const results = [];
try {
  await p.goto("http://127.0.0.1:5302/REVIEW.html");
  for (const key of ["1", "2", "3"]) {
    await p.keyboard.press(key);
    await expect(p.locator("#screen")).toHaveJSProperty("complete", true);
    await expect(p.locator("#screen")).toHaveJSProperty("naturalWidth", 1680);
    await expect(p.locator("#screen")).toHaveJSProperty("naturalHeight", 960);
    results.push({ key, src: await p.locator("#screen").getAttribute("src") });
  }
  await p.getByRole("button", { name: "1 · 基准原图" }).click();
  await expect(p.getByRole("button", { name: "1 · 基准原图" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await p.getByRole("button", { name: "3 · 本轮 React" }).click();
  await p.screenshot({
    path: resolve(
      "artifacts/product-acceptance/ui-iteration/R1.3/attempt-04/screenshots/review-gallery.png",
    ),
  });
  console.log("gallery keyboard/buttons/dimensions pass");
} finally {
  await writeFile(
    resolve("artifacts/product-acceptance/ui-iteration/R1.3/attempt-04/logs/gallery.json"),
    JSON.stringify({ results }, null, 2),
  );
  await b.close();
}
