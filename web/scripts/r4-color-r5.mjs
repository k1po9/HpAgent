import process from "node:process";
import "./review-runtime.mjs";
import console from "node:console";
/* global document, getComputedStyle */
import { chromium, expect } from "@playwright/test";
import { resolve } from "node:path";
import { writeFile } from "node:fs/promises";
const out = resolve("artifacts/product-acceptance/ui-iteration/R4/attempt-05");
const b = await chromium.launch({
  executablePath: process.env.HPAGENT_BROWSER_EXECUTABLE,
});
const reports = [],
  errors = [];
const luma = (rgb) =>
  rgb
    .slice(0, 3)
    .map((v) => {
      const c = v / 255;
      return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
    })
    .reduce((v, c, i) => v + c * [0.2126, 0.7152, 0.0722][i], 0);
const contrast = (a, b) => {
  const x = luma(a),
    y = luma(b);
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
};
try {
  for (const width of [1680, 3840]) {
    const p = await b.newPage({
      viewport: { width, height: width === 3840 ? 2160 : 1000 },
      reducedMotion: "no-preference",
    });
    p.on("pageerror", (e) => errors.push(e.message));
    await p.goto("http://127.0.0.1:5322/lab.html");
    await expect(p.locator(".hp-run-detail")).toHaveCount(3);
    const source = p.locator(".hp-run-detail").nth(1);
    await source.hover();
    await expect
      .poll(() => source.evaluate((n) => getComputedStyle(n).color))
      .toBe("rgb(185, 64, 37)");
    const hover = await source.evaluate((n) => {
      const rgba = (color) => {
        const c = document.createElement("canvas");
        c.width = c.height = 1;
        const x = c.getContext("2d");
        x.fillStyle = color;
        x.fillRect(0, 0, 1, 1);
        return [...x.getImageData(0, 0, 1, 1).data];
      };
      const copy = n.querySelector(".hp-run-reference-copy"),
        id = n.querySelector(".hp-run-reference-id");
      return {
        foreground: rgba(getComputedStyle(copy).color),
        background: rgba(getComputedStyle(copy).backgroundColor),
        id: rgba(getComputedStyle(id).color),
        arrow: rgba(getComputedStyle(n.querySelector("svg")).color),
        rowBackground: rgba(getComputedStyle(n).backgroundColor),
        sourceRect: n.getBoundingClientRect().toJSON(),
        copyRect: copy.getBoundingClientRect().toJSON(),
      };
    });
    expect(hover.foreground.slice(0, 3)).toEqual([185, 64, 37]);
    expect(hover.background.slice(0, 3)).toEqual([253, 237, 232]);
    expect(hover.rowBackground[3]).toBe(0);
    expect(hover.arrow.slice(0, 3)).toEqual([185, 64, 37]);
    expect(hover.id.slice(0, 3)).toEqual([83, 101, 112]);
    const textContrast = contrast(hover.foreground, hover.background),
      idContrast = contrast(hover.id, hover.background),
      arrowContrast = contrast(hover.arrow, [231, 233, 236]);
    expect(textContrast).toBeGreaterThanOrEqual(4.5);
    expect(idContrast).toBeGreaterThanOrEqual(4.5);
    expect(arrowContrast).toBeGreaterThanOrEqual(3);
    await p.screenshot({ path: resolve(out, `frames/color-hover-${width}.png`) });
    await source.click();
    await p.waitForTimeout(250);
    const optical = await p.locator(".hp-inspector-relay").evaluate((n) => ({
      stops: [...n.querySelectorAll("linearGradient stop")].map(
        (s) => getComputedStyle(s).stopColor,
      ),
      spark: [...n.querySelectorAll("radialGradient stop")].map(
        (s) => getComputedStyle(s).stopColor,
      ),
      widths: [...n.querySelectorAll(".hp-filament-shell,.hp-filament-echo,.hp-filament-core")].map(
        (s) => getComputedStyle(s).strokeWidth,
      ),
      points: { ...n.dataset },
    }));
    expect(optical.stops).toEqual([
      "rgb(123, 137, 148)",
      "rgb(197, 208, 216)",
      "rgb(255, 255, 255)",
      "rgb(197, 208, 216)",
      "rgb(123, 137, 148)",
    ]);
    expect(optical.widths).toEqual(["11px", "1.25px", "2.2px"]);
    await p.screenshot({ path: resolve(out, `frames/color-travel-${width}.png`) });
    await expect(p.locator("#inspector-title.hp-inspector-ready")).toBeVisible();
    const peak = await p.locator("#inspector-title").evaluate((n) => {
      // Explicit frame inspection only: pause the existing CSS color animation at its peak.
      const a = n.getAnimations().find((a) => a.animationName === "hp-inspector-ready");
      a.pause();
      a.currentTime = 650 * 0.35;
      return {
        color: getComputedStyle(n).color,
        shadow: getComputedStyle(n).textShadow,
        title: n.textContent,
      };
    });
    expect(peak.color).toBe("rgb(185, 64, 37)");
    const titleContrast = contrast([185, 64, 37], [244, 246, 248]);
    expect(titleContrast).toBeGreaterThanOrEqual(4.5);
    await p.screenshot({ path: resolve(out, `frames/color-title-peak-${width}.png`) });
    await p.locator('.hp-inspector button[title="关闭"]').click();
    await expect(source).toBeFocused();
    await expect(p.locator(".hp-inspector-exit")).toHaveCount(0);
    reports.push({
      width,
      hover,
      optical,
      peak,
      textContrast,
      idContrast,
      arrowContrast,
      titleContrast,
      peakInspection: "CSS animation paused at its existing 35% peak in the browser test only",
    });
    await p.close();
    console.log("color", width, "pass");
  }
  expect(errors).toEqual([]);
  await writeFile(resolve(out, "logs/color.json"), JSON.stringify({ reports, errors }, null, 2));
} finally {
  await b.close();
}
