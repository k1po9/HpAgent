import process from "node:process";
import "./review-runtime.mjs";
import console from "node:console";
/* global window, document, performance, getComputedStyle, HTMLElement, DOMRect, innerWidth, location, URLSearchParams */
import { chromium, expect } from "@playwright/test";
import { resolve } from "node:path";
import { writeFile } from "node:fs/promises";
const out = resolve("artifacts/product-acceptance/ui-iteration/R4/attempt-05");
const b = await chromium.launch({
  executablePath: process.env.HPAGENT_BROWSER_EXECUTABLE,
});
const reports = [],
  errors = [];
try {
  for (const [scene, width, edge] of [
    ["normal", 1680, false],
    ["slow", 1680, false],
    ["normal", 3840, false],
    ["normal", 1680, true],
  ]) {
    const c = await b.newContext({
        viewport: { width, height: width === 3840 ? 2160 : 1000 },
        reducedMotion: "no-preference",
      }),
      p = await c.newPage();
    p.on("pageerror", (e) => errors.push(e.message));
    await p.addInitScript(() => {
      window.__visual = [];
      window.addEventListener("hp-inspector-relay-event", (e) => {
        const svg = document.querySelector(".hp-inspector-relay");
        if (e.detail.stage === "started" && svg) {
          const routeId = new URLSearchParams(location.hash.split("?")[1]).get("inspect")?.slice(4);
          if (routeId) window.__visualSource = routeId;
          const id = routeId ?? window.__visualSource,
            a = document.getElementById("run-open-" + id)?.querySelector("[data-relay-anchor]"),
            t = document.getElementById("inspector-title");
          window.__visual.push({
            t: performance.now(),
            direction: e.detail.direction,
            initial: {
              opacity: svg.style.opacity,
              tip: svg.dataset.tipLength,
              paths: [
                ...svg.querySelectorAll(".hp-filament-shell,.hp-filament-echo,.hp-filament-core"),
              ].map((n) => ({
                opacity: n.style.opacity,
                width: getComputedStyle(n).strokeWidth,
                stroke: getComputedStyle(n).stroke,
              })),
              mask: svg.querySelector("mask path").style.strokeDasharray,
              head: {
                x: svg.querySelector("circle").getAttribute("cx"),
                y: svg.querySelector("circle").getAttribute("cy"),
              },
            },
            source: a?.getBoundingClientRect().toJSON(),
            target: t?.getBoundingClientRect().toJSON(),
            points: { ...svg.dataset },
          });
        }
      });
    });
    await p.goto("http://127.0.0.1:5322/lab.html?case=" + scene);
    await expect(p.locator(".hp-run-detail")).toHaveCount(3);
    await p.waitForTimeout(200);
    if (edge)
      await p.evaluate(() => {
        const native = HTMLElement.prototype.getBoundingClientRect;
        HTMLElement.prototype.getBoundingClientRect = function () {
          const r = native.call(this);
          return this.id === "inspector-title"
            ? new DOMRect(r.x, r.y, innerWidth - r.x + 0.0000915527344, r.height)
            : r;
        };
      });
    await p.locator(".hp-run-detail").nth(1).click();
    await expect(p.locator(".hp-inspector-relay")).toHaveCount(1);
    await p.waitForTimeout(350);
    await expect(p.locator("[data-inspector-phase]")).toHaveAttribute(
      "data-inspector-phase",
      "receiving",
    );
    await p.screenshot({
      path: resolve(out, `frames/visual-${scene}-${width}-${edge ? "edge" : "natural"}-travel.png`),
    });
    const start = await p.evaluate(() => window.__visual.find((e) => e.direction === "arrival"));
    expect(start.initial.opacity).toBe("0");
    expect(start.initial.tip).toBe("0");
    expect(start.initial.mask.split(/[ ,]+/).map(Number)).toEqual([0, 100000]);
    expect(start.initial.paths.every((n) => n.opacity === "0")).toBe(true);
    expect(Number(start.points.sourceX)).toBeCloseTo(start.source.right - 4, 2);
    expect(Number(start.points.targetX)).toBeCloseTo(start.target.left + 24, 2);
    expect(Number(start.points.targetY)).toBeCloseTo(start.target.top + start.target.height / 2, 2);
    expect(start.initial.paths.map((n) => n.width)).toEqual(["11px", "1.25px", "2.2px"]);
    await expect(p.locator("#inspector-title.hp-inspector-accept")).toBeVisible();
    const landed = await p.evaluate(() => ({
      t: performance.now(),
      ready: document.getElementById("inspector-title").classList.contains("hp-inspector-ready"),
      events: window.__r4events,
    }));
    if (scene === "slow") {
      expect(landed.ready).toBe(false);
      await expect(p.getByText("正在加载执行…", { exact: true })).toBeVisible();
    }
    await expect(p.locator("#inspector-title.hp-inspector-ready")).toBeVisible();
    const loaded = await p.evaluate(() => ({
      t: performance.now(),
      body: document.querySelector(".hp-run-result")?.textContent,
      requests: window.__r4log.filter((n) => n.type === "request" || n.type === "response"),
    }));
    await p.screenshot({
      path: resolve(out, `frames/visual-${scene}-${width}-${edge ? "edge" : "natural"}-landed.png`),
    });
    expect(loaded.body).toContain("执行 B");
    await expect(p.locator("[data-inspector-phase]")).toHaveAttribute(
      "data-inspector-phase",
      "settled",
    );
    await p.locator('.hp-inspector button[title="关闭"]').click();
    await p.waitForTimeout(120);
    expect(await p.locator(".hp-inspector").count()).toBe(0);
    expect(await p.locator(".hp-inspector-exit").textContent()).toBe("");
    const before = await p.locator(".hp-inspector-relay").evaluate((n) => ({ ...n.dataset }));
    await p.waitForTimeout(180);
    const after = await p.locator(".hp-inspector-relay").evaluate((n) => ({ ...n.dataset }));
    expect(Number(after.tailLength)).toBeGreaterThan(0);
    expect(Number(after.tipLength) - Number(after.tailLength)).toBeLessThan(
      Number(after.totalLength) * 0.26,
    );
    expect(Number(after.sourceX)).not.toBe(Number(before.sourceX));
    await p.screenshot({
      path: resolve(out, `frames/visual-${scene}-${width}-${edge ? "edge" : "natural"}-return.png`),
    });
    await p.waitForTimeout(450);
    await expect(p.locator(".hp-inspector-exit")).toHaveCount(0);
    const events = await p.evaluate(() => window.__r4events);
    expect(events.some((e) => e.direction === "return" && e.reason === "completed")).toBe(true);
    reports.push({
      scene,
      width,
      edge,
      start,
      landed,
      loaded,
      returnBefore: before,
      returnAfter: after,
      events,
    });
    await c.close();
    console.log("visual", scene, width, edge, "pass");
  }
  const c = await b.newContext({ viewport: { width: 1920, height: 1200 } }),
    p = await c.newPage();
  await p.goto("http://127.0.0.1:5322/REVIEW.html");
  const f = p.frameLocator('iframe[title="B5.3原始样机"]');
  await expect(f.locator('.object-card[data-key="run"]')).toBeVisible();
  await expect(
    p.frameLocator('iframe[title="真实React视觉返修"]').locator(".hp-run-detail"),
  ).toHaveCount(3);
  await p.waitForTimeout(300);
  await p.evaluate(() => {
    document
      .querySelector('iframe[title="B5.3原始样机"]')
      .contentDocument.querySelector('.object-card[data-key="run"] .object-open')
      .click();
    document
      .querySelector('iframe[title="真实React视觉返修"]')
      .contentDocument.querySelectorAll(".hp-run-detail")[1]
      .click();
  });
  await p.waitForTimeout(350);
  await p.screenshot({ path: resolve(out, "frames/reference-and-production.png") });
  await c.close();
  expect(errors).toEqual([]);
  await writeFile(
    resolve(out, "logs/visual.json"),
    JSON.stringify(
      {
        reports,
        errors,
        reference: "original B5.3 HTML; reference only, never production test evidence",
      },
      null,
      2,
    ),
  );
} finally {
  await b.close();
}
