/* global window, console */
import { chromium, expect } from "@playwright/test";
import { writeFile } from "node:fs/promises";
const out = "artifacts/product-acceptance/ui-iteration/R4/attempt-11-artifact";
const browser = await chromium.launch({ args: ["--no-sandbox"] });
const results = [];
try {
  for (const mode of ["moving-source", "keyboard-reduced"]) {
    const page = await browser.newPage({ viewport: { width: 1680, height: 1000 } });
    await page.goto("http://127.0.0.1:5328/lab.html");
    await expect(page.locator(".hp-run-detail")).toHaveCount(3);
    await page.waitForTimeout(1000);
    await expect(page.locator("#artifact-open-r42-B")).toHaveCount(1);
    const source = page.locator("#artifact-open-r42-B");
    if (mode === "moving-source") await source.scrollIntoViewIfNeeded();
    else await source.evaluate((n) => n.scrollIntoView({ block: "center" }));
    await page.waitForTimeout(400);
    await source.evaluate((n) => (window.__artifactSource = n));
    if (mode === "moving-source") {
      await source.click();
      await expect(page.locator(".hp-inspector-relay")).toHaveCount(1);
      await page.locator(".hp-thread__viewport").evaluate((n) => (n.scrollTop -= 24));
    } else {
      await source.focus();
      await page.keyboard.press("Enter");
      await expect(page.locator(".hp-inspector-relay")).toHaveCount(1);
      await expect(page.locator("[data-inspector-gate]")).toHaveAttribute("hidden");
      await expect(page.getByRole("tab")).toHaveCount(0);
      await page.emulateMedia({ reducedMotion: "reduce" });
    }
    await expect(page.locator("[data-inspector-gate]")).not.toHaveAttribute("hidden");
    await expect(page.frameLocator('iframe[title="Artifact 预览"]').locator("#version")).toHaveText(
      "r42-B · v2",
    );
    await expect(page.locator(".hp-inspector-relay")).toHaveCount(0);
    expect(await source.evaluate((n) => n === window.__artifactSource)).toBe(true);
    const log = await page.evaluate(() => window.__artifactReviewLog);
    if (mode === "moving-source")
      expect(
        log.some(
          (e) =>
            e.direction === "arrival" &&
            e.stage === "finished" &&
            ["scroll", "geometry"].includes(e.reason),
        ),
      ).toBe(true);
    else {
      await page.getByRole("tab", { name: "预览", exact: true }).focus();
      await page.keyboard.press("ArrowRight");
      await expect(page.getByRole("tab", { name: "版本历史", exact: true })).toBeFocused();
      await expect(page.locator('iframe[title="Artifact 预览"]')).not.toBeVisible();
      await page.keyboard.press("Home");
      await expect(page.getByRole("tab", { name: "预览", exact: true })).toBeFocused();
    }
    await page.locator('.hp-inspector button[title="关闭"]').click();
    await expect(source).toBeFocused();
    results.push({
      case: mode,
      result: "pass",
      sourceIdentity: "preserved",
      events: log.filter((e) => e.direction),
      frames: log.filter((e) => e.stage === "frame"),
    });
    await page.close();
  }
  await writeFile(out + "/interrupt-check.json", JSON.stringify({ results }, null, 2));
  console.log("Artifact scroll, keyboard and reduced-motion interruption passed");
} finally {
  await browser.close();
}
