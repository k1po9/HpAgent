/* global document, window, getComputedStyle, innerWidth, location, URLSearchParams, console */
import { chromium, expect } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
const out = resolve("artifacts/product-acceptance/ui-iteration/R4/attempt-11-artifact");
await mkdir(out + "/frames", { recursive: true });
const browser = await chromium.launch({ args: ["--no-sandbox"] });
const reports = [],
  errors = [];
async function scene(
  caseName = "normal",
  width = 1680,
  motion = "no-preference",
  file = "lab.html",
) {
  const p = await browser.newPage({
    viewport: { width, height: 1000 },
    locale: "zh-CN",
    timezoneId: "Asia/Shanghai",
    reducedMotion: motion,
  });
  p.on("pageerror", (e) => errors.push(e.message));
  await p.goto(`http://127.0.0.1:5328/${file}?case=${caseName}`);
  await expect(p.locator(".hp-run-detail")).toHaveCount(3);
  await expect(p.locator(".hp-entry-origin")).toHaveCount(0);
  // Visit both ends so ordinary visible-message preloading discovers all three sources.
  await p.locator(".hp-thread__viewport").evaluate((n) => (n.scrollTop = 0));
  await expect(p.locator("#artifact-open-r42-A")).toHaveCount(1);
  await p.locator(".hp-thread__viewport").evaluate((n) => (n.scrollTop = n.scrollHeight));
  await expect(p.locator("#artifact-open-r42-C")).toHaveCount(1);
  await p.locator("#artifact-open-r42-B").evaluate((n) => n.scrollIntoView({ block: "center" }));
  await p.waitForTimeout(400);
  return p;
}
async function open(p, id = "B") {
  await p.locator("#artifact-open-r42-" + id).click();
}
async function ready(p, id = "B", v = 2) {
  await expect(p.locator("[data-inspector-gate]")).not.toHaveAttribute("hidden");
  await expect(p.frameLocator('iframe[title="Artifact 预览"]').locator("#version")).toHaveText(
    `r42-${id} · v${v}`,
  );
}
try {
  const p = await scene();
  await open(p);
  await p.waitForTimeout(300);
  await expect(p.locator(".hp-inspector-relay")).toHaveCount(1);
  await expect(p.locator("[data-inspector-gate]")).toHaveAttribute("hidden");
  expect(
    new URLSearchParams((await p.evaluate(() => location.hash)).split("?")[1]).get("version"),
  ).toBe("r42-B-v2");
  await p.screenshot({ path: out + "/frames/artifact-travel.png" });
  await ready(p);
  await p.waitForTimeout(750);
  await expect(p.locator(".hp-title-capture")).toHaveAttribute("data-active", "true");
  const geometry = await p.evaluate(() => {
    const title = document.getElementById("inspector-artifact-title"),
      svg = document.querySelector(".hp-title-capture"),
      r = title.getBoundingClientRect(),
      s = svg.getBoundingClientRect(),
      pt = svg.querySelector(".capture-frame").getPointAtLength(0);
    return {
      text: title.textContent,
      insetX: r.left - s.left,
      insetY: r.top - s.top,
      dx: s.left + pt.x - r.left - Math.min(24, r.width / 3),
      dy: s.top + pt.y - r.top - r.height / 2,
    };
  });
  expect(geometry.insetX).toBeCloseTo(11, 1);
  expect(geometry.insetY).toBeCloseTo(7, 1);
  expect(geometry.dx).toBeCloseTo(0, 2);
  expect(geometry.dy).toBeCloseTo(0, 2);
  const log = await p.evaluate(() => window.__artifactReviewLog),
    arrival = log.find((e) => e.stage === "arrived" && e.reason === "actual-tip");
  // Relay event detail carries its own stage; use the measured frame gate for the title/content sequence.
  const title = log.find((e) => e.titleArrival),
    content = log.find((e) => e.content === "shown");
  expect(title).toBeTruthy();
  expect(content.t - title.t).toBeGreaterThan(150);
  expect(log.find((e) => e.stage === "request" && e.id === "r42-B").t).toBeLessThan(title.t);
  const opens = log.filter((e) => e.direction === "arrival" && e.reason === "eligible");
  expect(opens).toHaveLength(1);
  await p.screenshot({ path: out + "/frames/artifact-received.png" });
  const preview = p.frameLocator('iframe[title="Artifact 预览"]');
  await preview.locator("#counter").click();
  await expect(preview.locator("#counter")).toHaveText("1");
  await p.evaluate(
    () => (window.__keptPreview = document.querySelector('iframe[title="Artifact 预览"]')),
  );
  await p.getByLabel("修改指令").fill("跨页签保留的修改草稿");
  await p.getByRole("tab", { name: "详情", exact: true }).click();
  await expect(p.locator('iframe[title="Artifact 预览"]')).not.toBeVisible();
  await p.getByRole("tab", { name: "预览", exact: true }).click();
  expect(
    await p.evaluate(
      () => window.__keptPreview === document.querySelector('iframe[title="Artifact 预览"]'),
    ),
  ).toBe(true);
  await expect(preview.locator("#counter")).toHaveText("1");
  await expect(p.getByLabel("修改指令")).toHaveValue("跨页签保留的修改草稿");
  await p.getByRole("button", { name: "扩大阅读" }).click();
  await expect(preview.locator("#counter")).toHaveText("1");
  await p.getByRole("tab", { name: "版本历史", exact: true }).click();
  await p.getByRole("button", { name: "查看 v1", exact: true }).click();
  await p.getByRole("tab", { name: "预览", exact: true }).click();
  await ready(p, "B", 1);
  await expect(preview.locator("#counter")).toHaveText("0");
  expect(
    await p.evaluate(
      () =>
        window.__artifactReviewLog.filter(
          (e) => e.direction === "arrival" && e.reason === "eligible",
        ).length,
    ),
  ).toBe(1);
  await p.getByRole("tab", { name: "详情", exact: true }).click();
  await p.getByRole("button", { name: "r4-B", exact: true }).click();
  await expect(p.locator(".hp-run-result")).toHaveText("执行 B：第二条结果，独立来源。");
  await p.getByRole("button", { name: "返回", exact: true }).click();
  await ready(p, "B", 1);
  expect(
    new URLSearchParams((await p.evaluate(() => location.hash)).split("?")[1]).get("version"),
  ).toBe("r42-B-v1");
  await p.locator('.hp-inspector button[title="关闭"]').click();
  expect(await p.locator('iframe[title="Artifact 预览"]').count()).toBe(0);
  await expect(p.locator(".hp-inspector-exit")).toHaveCount(0);
  await expect(p.locator("#artifact-open-r42-B")).toBeFocused();
  reports.push({
    case: "normal",
    geometry,
    titleToDetails: content.t - title.t,
    requestTiming: "pass",
    normalization: "one-flight",
    iframeState: "pass",
    version: "pass",
    childBack: "pass",
    focus: "pass",
    arrival,
  });
  const slow = await scene("slow");
  await open(slow);
  await slow.waitForFunction(
    () => document.querySelector("[data-inspector-gate]")?.hidden === false,
  );
  await expect(slow.getByText(/正在加载 (HTML 成果|版本内容)…/)).toBeVisible();
  await ready(slow);
  reports.push({ case: "slow", currentDataAfterArrival: "pass" });
  const fast = await scene("fast-switch");
  await open(fast, "A");
  await fast.waitForTimeout(100);
  await open(fast, "B");
  await fast.waitForTimeout(100);
  await open(fast, "C");
  await ready(fast, "C");
  await fast.waitForTimeout(1700);
  await ready(fast, "C");
  await fast.locator('.hp-inspector button[title="关闭"]').click();
  await open(fast, "B");
  await fast.waitForTimeout(100);
  await fast.locator('.hp-inspector button[title="关闭"]').click();
  await open(fast, "C");
  await ready(fast, "C");
  reports.push({ case: "fast", lateData: "pass", closeReopen: "pass" });
  for (const test of ["denied", "offline"]) {
    const q = await scene(test);
    await open(q);
    await expect(q.locator("[data-inspector-gate]")).not.toHaveAttribute("hidden");
    if (test === "denied") {
      await expect(q.locator('.hp-artifact-empty [role="alert"]')).toBeVisible();
      await expect(q.locator('iframe[title="Artifact 预览"]')).toHaveCount(0);
    } else {
      await expect(q.locator(".hp-artifact-notice--error")).toContainText("同步中断");
      await ready(q, "B", 1);
    }
    reports.push({ case: test, safeError: "pass" });
  }
  for (const [width, motion] of [
    [390, "no-preference"],
    [1279, "no-preference"],
    [1280, "no-preference"],
    [1680, "reduce"],
  ]) {
    const q = await scene("normal", width, motion);
    await open(q);
    await ready(q);
    expect(await q.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    if (width < 1280 || motion === "reduce")
      await expect(q.locator(".hp-inspector-relay")).toHaveCount(0);
    if (width === 390) {
      await q.screenshot({ path: out + "/frames/artifact-mobile.png" });
      await expect(q.locator(".hp-inspector")).toHaveAttribute("aria-modal", "true");
    }
    await q.locator('.hp-inspector button[title="关闭"]').click();
    await expect(q.locator(".hp-inspector-exit")).toHaveCount(0);
    reports.push({ case: "responsive", width, motion, result: "pass" });
  }
  // Keep the previously accepted Run optics and geometry identical under the same data.
  const optics = [];
  for (const file of ["before.html", "lab.html"]) {
    const q = await scene("normal", 1680, "no-preference", file);
    await q.locator(".hp-run-detail").nth(1).click();
    await expect(q.locator(".hp-run-result")).toHaveText("执行 B：第二条结果，独立来源。");
    await q.waitForTimeout(750);
    const value = await q.evaluate(() =>
      Object.fromEntries(
        [".hp-inspector-receiver", ".hp-title-capture", ".hp-run-detail", ".hp-composer"].map(
          (s) => {
            const n = document.querySelector(s),
              c = getComputedStyle(n),
              r = n.getBoundingClientRect();
            return [
              s,
              { color: c.color, border: c.border, font: c.font, width: r.width, height: r.height },
            ];
          },
        ),
      ),
    );
    optics.push(value);
    await q.screenshot({
      path: out + `/frames/run-${file === "before.html" ? "baseline" : "current"}.png`,
    });
  }
  expect(optics[1]).toEqual(optics[0]);
  reports.push({ case: "acceptedRun", opticalParity: "pass" });
  const off = await browser.newPage({ viewport: { width: 1680, height: 1000 } });
  const network = [];
  off.on("request", (r) => {
    if (/^https?:/.test(r.url())) network.push(r.url());
  });
  off.on("pageerror", (e) => errors.push(e.message));
  await off.goto(pathToFileURL(out + "/HpAgent-R4.2-Artifact-Standalone.html").href);
  await expect(off.locator(".hp-entry-origin")).toHaveCount(0);
  await expect(off.locator("#artifact-open-r42-B")).toHaveCount(1);
  await open(off);
  await ready(off);
  expect(network).toEqual([]);
  reports.push({ case: "offlineFile", externalRequests: network.length });
  expect(errors).toEqual([]);
  console.log("Artifact browser checks passed");
} finally {
  await writeFile(out + "/browser-check.json", JSON.stringify({ reports, errors }, null, 2));
  await browser.close();
}
