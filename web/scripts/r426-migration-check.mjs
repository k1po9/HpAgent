/* global document, window, getComputedStyle, innerWidth, console */
import { chromium, expect } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
const out = resolve("artifacts/product-acceptance/ui-iteration/R4/attempt-10-r426-migration");
await mkdir(out + "/frames", { recursive: true });
const browser = await chromium.launch({ args: ["--no-sandbox"] });
const errors = [],
  reports = [];
async function page(file = "lab.html", motion = "no-preference", width = 1680, scene = "normal") {
  const p = await browser.newPage({
    viewport: { width, height: 1000 },
    locale: "zh-CN",
    timezoneId: "Asia/Shanghai",
    reducedMotion: motion,
  });
  p.on("pageerror", (e) => errors.push(e.message));
  await p.goto(`http://127.0.0.1:5327/${file}?case=${scene}`);
  if (file === "reference.html")
    await p.locator("#standalone-review").evaluate((n) => (n.style.display = "none"));
  await expect(p.locator(".hp-run-detail")).toHaveCount(3);
  await expect(p.locator(".hp-entry-origin")).toHaveCount(0);
  await p.waitForTimeout(300);
  return p;
}
async function optical(p) {
  return await p.evaluate(() => {
    const selectors = [
      ".hp-rail",
      ".hp-sidebar",
      ".hp-conversation-header",
      ".hp-run-detail",
      ".hp-run-card-status",
      ".hp-run-card-dot",
      ".hp-composer",
      ".hp-inspector-receiver",
      ".hp-inspector-tabs",
    ];
    return Object.fromEntries(
      selectors.map((s) => {
        const n = document.querySelector(s);
        if (!n) return [s, null];
        const c = getComputedStyle(n),
          r = n.getBoundingClientRect();
        return [
          s,
          {
            background: c.background,
            border: c.border,
            color: c.color,
            radius: c.borderRadius,
            font: c.font,
            shadow: c.boxShadow,
            width: r.width,
            height: r.height,
          },
        ];
      }),
    );
  });
}
try {
  const reference = await page("reference.html");
  const migrated = await page();
  for (const [name, p] of [
    ["reference", reference],
    ["migrated", migrated],
  ]) {
    await p.screenshot({ path: out + `/frames/${name}-cards.png` });
    await p.locator(".hp-run-detail").nth(1).click();
    await p.waitForTimeout(300);
    await expect(p.locator("[data-inspector-gate]")).toHaveAttribute("hidden");
    const travel = await p.locator(".hp-inspector-relay").evaluate((n) => {
      const glint = n.querySelector(".hp-filament-living-glint");
      return {
        dataset: { ...n.dataset },
        masked: !!glint?.parentElement.getAttribute("mask"),
        alpha: glint ? getComputedStyle(glint).opacity : null,
        units: n.querySelector('linearGradient[id$="glint"]')?.getAttribute("gradientUnits"),
      };
    });
    expect(travel.masked).toBe(true);
    expect(travel.units).toBe("userSpaceOnUse");
    expect(Number(travel.alpha)).toBeGreaterThan(0.08);
    await p.screenshot({ path: out + `/frames/${name}-travel.png` });
    await expect(p.locator("[data-inspector-gate]")).not.toHaveAttribute("hidden");
    await expect(p.locator(".hp-title-capture")).toHaveAttribute("data-active", "true");
    await p.waitForTimeout(750);
    await p.screenshot({ path: out + `/frames/${name}-received.png` });
    const geometry = await p.evaluate(() => {
      const title = document.getElementById("inspector-run-title"),
        svg = document.querySelector(".hp-title-capture"),
        path = svg.querySelector(".capture-frame"),
        r = title.getBoundingClientRect(),
        s = svg.getBoundingClientRect(),
        pt = path.getPointAtLength(0);
      return {
        text: title.textContent.trim(),
        insetX: r.left - s.left,
        insetY: r.top - s.top,
        dx: s.left + pt.x - (r.left + Math.min(24, r.width / 3)),
        dy: s.top + pt.y - (r.top + r.height / 2),
        h2Opacity: getComputedStyle(document.getElementById("inspector-title")).opacity,
      };
    });
    expect(geometry.text).toBe("执行详情");
    expect(geometry.insetX).toBeCloseTo(11, 1);
    expect(geometry.insetY).toBeCloseTo(7, 1);
    expect(geometry.dx).toBeCloseTo(0, 2);
    expect(geometry.dy).toBeCloseTo(0, 2);
    expect(geometry.h2Opacity).toBe("0");
    const frames = await p.evaluate(() => window.__r4log.filter((n) => n.type === "frame"));
    const arrival = frames.find((n) => n.titleArrival),
      details = frames.find((n) => n.detailsVisible);
    expect(arrival).toBeTruthy();
    expect(details.t - arrival.t).toBeGreaterThan(150);
    const request = await p.evaluate(() =>
      window.__r4log.find((n) => n.type === "request" && n.path.endsWith("/runs/r4-B")),
    );
    expect(request.t).toBeLessThan(arrival.t);
    reports.push({
      name,
      travel,
      geometry,
      optical: await optical(p),
      titleToDetails: details.t - arrival.t,
    });
  }
  const diffs = [];
  for (const [selector, expected] of Object.entries(reports[0].optical)) {
    const actual = reports[1].optical[selector];
    for (const prop of Object.keys(expected || {}))
      if (expected[prop] !== actual?.[prop])
        diffs.push({ selector, prop, reference: expected[prop], migrated: actual?.[prop] });
  }
  await writeFile(out + "/optical-diff.json", JSON.stringify(diffs, null, 2));
  console.log("optical diffs", JSON.stringify(diffs));
  await migrated.locator('.hp-inspector button[title="关闭"]').click();
  expect(await migrated.locator("[data-inspector-gate]").count()).toBe(0);
  await expect(migrated.locator(".hp-inspector-exit")).toHaveCount(0);
  await migrated.locator(".hp-run-detail").first().click();
  await migrated.waitForTimeout(100);
  await migrated.locator('.hp-inspector button[title="关闭"]').click();
  await migrated.locator(".hp-run-detail").nth(2).click();
  await expect(migrated.locator(".hp-run-result")).toHaveText("执行 C：最后选择的当前结果。");
  await expect(migrated.locator(".hp-title-capture")).toHaveCount(1);
  const fast = await page("lab.html", "no-preference", 1680, "fast-switch");
  await fast.locator(".hp-run-detail").first().click();
  await fast.waitForTimeout(100);
  await fast.locator(".hp-run-detail").nth(1).click();
  await fast.waitForTimeout(100);
  await fast.locator(".hp-run-detail").nth(2).click();
  await expect(fast.locator(".hp-run-result")).toHaveText("执行 C：最后选择的当前结果。");
  await fast.waitForTimeout(1600);
  await expect(fast.locator(".hp-run-result")).toHaveText("执行 C：最后选择的当前结果。");
  const reduced = await page("lab.html", "reduce");
  await reduced.locator(".hp-run-detail").first().click();
  await expect(reduced.locator(".hp-run-result")).toBeVisible();
  await expect(reduced.locator(".hp-inspector-relay")).toHaveCount(0);
  await expect(reduced.locator(".hp-title-capture")).toHaveAttribute("data-active", "true");
  expect(
    await reduced
      .locator(".hp-title-capture")
      .evaluate((n) => n.getAnimations({ subtree: true }).length),
  ).toBe(0);
  const narrow = await page("lab.html", "no-preference", 390);
  await narrow.locator(".hp-run-detail").first().click();
  await expect(narrow.locator(".hp-run-result")).toBeVisible();
  await narrow.screenshot({ path: out + "/frames/mobile.png" });
  expect(await narrow.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
    true,
  );
  await narrow.locator('.hp-inspector button[title="关闭"]').click();
  await expect(narrow.locator(".hp-inspector")).toHaveCount(0);
  const denied = await page("lab.html", "no-preference", 1680, "denied");
  await denied.locator(".hp-run-detail").first().click();
  await expect(denied.getByText("对象不可用。", { exact: true })).toBeVisible();
  const live = await page();
  await live.locator(".hp-composer__input").fill("迁移回归验证");
  await live.locator(".hp-composer__send").click();
  await expect(live.locator(".hp-composer__cancel")).toBeVisible();
  expect(await live.locator('.hp-execution-block [data-testid="stop-run"]').count()).toBe(0);
  await live.locator(".hp-composer__cancel").click();
  await expect(live.locator('.hp-runstrip[data-status="cancelled"]')).toBeVisible();
  expect(errors).toEqual([]);
  await writeFile(
    out + "/browser-check.json",
    JSON.stringify(
      {
        reports,
        diffs,
        errors,
        rapidSwitch: "pass",
        closeReopen: "pass",
        reducedMotion: "pass",
        mobile: "pass",
        unavailable: "pass",
        composerStop: "pass",
      },
      null,
      2,
    ),
  );
  console.log("browser checks passed");
} finally {
  await browser.close();
}
