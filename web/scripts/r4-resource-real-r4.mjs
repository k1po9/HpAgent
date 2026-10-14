import console from "node:console";
import { URL } from "node:url";
/* global window, document, performance, requestAnimationFrame, EventTarget, location */
import "./r1-isolation.mjs";
import { chromium, expect } from "@playwright/test";
import { resolve } from "node:path";
import { readFile, writeFile } from "node:fs/promises";
import os from "node:os";
const out = resolve("artifacts/product-acceptance/ui-iteration/R4/attempt-04");
const data = JSON.parse(await readFile(resolve(out, "fixture.json"), "utf8"));
const b = await chromium.launch({
    executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
  }),
  c = await b.newContext({
    viewport: { width: 1680, height: 1000 },
    reducedMotion: "no-preference",
  }),
  p = await c.newPage();
const errors = [],
  requests = [];
p.on("pageerror", (e) => errors.push(e.message));
p.on("request", (r) => {
  if (r.url().includes("/api/v1/runs/"))
    requests.push({ type: "request", path: new URL(r.url()).pathname, t: Date.now() });
});
p.on("response", (r) => {
  if (r.url().includes("/api/v1/runs/"))
    requests.push({
      type: "response",
      path: new URL(r.url()).pathname,
      status: r.status(),
      t: Date.now(),
    });
});
await p.addInitScript(() => {
  const add = EventTarget.prototype.addEventListener,
    remove = EventTarget.prototype.removeEventListener,
    records = [];
  EventTarget.prototype.addEventListener = function (type, fn, options) {
    if (this === window || this === document) {
      const cap = options === true || options?.capture === true;
      if (
        !records.some((r) => r.target === this && r.type === type && r.fn === fn && r.cap === cap)
      )
        records.push({ target: this, type, fn, cap });
    }
    return add.call(this, type, fn, options);
  };
  EventTarget.prototype.removeEventListener = function (type, fn, options) {
    const cap = options === true || options?.capture === true;
    const i = records.findIndex(
      (r) => r.target === this && r.type === type && r.fn === fn && r.cap === cap,
    );
    if (i >= 0) records.splice(i, 1);
    return remove.call(this, type, fn, options);
  };
  const timeout = window.setTimeout.bind(window),
    clear = window.clearTimeout.bind(window),
    timers = new Set();
  window.setTimeout = (fn, delay, ...args) => {
    const id = timeout(() => {
      timers.delete(id);
      if (typeof fn === "function") fn(...args);
    }, delay);
    timers.add(id);
    return id;
  };
  window.clearTimeout = (id) => {
    timers.delete(id);
    clear(id);
  };
  const raf = window.requestAnimationFrame.bind(window),
    cancel = window.cancelAnimationFrame.bind(window),
    active = new Set();
  window.requestAnimationFrame = (fn) => {
    const id = raf((t) => {
      active.delete(id);
      fn(t);
    });
    active.add(id);
    return id;
  };
  window.cancelAnimationFrame = (id) => {
    active.delete(id);
    cancel(id);
  };
  window.__resources = () => ({
    listeners: records.length,
    timeouts: timers.size,
    raf: active.size,
    overlays: document.querySelectorAll(".hp-inspector-relay,.hp-inspector-exit").length,
  });
});
try {
  await p.goto("http://127.0.0.1:5321/lab.html");
  if (await p.getByLabel("用户名").isVisible()) {
    await p.getByLabel("用户名").fill("r1_review");
    await p.getByLabel("密码", { exact: true }).fill("r1-review-isolated-password");
    await p.getByRole("button", { name: "登录", exact: true }).click();
  }
  await expect(p.locator(".hp-run-detail")).toHaveCount(3);
  await expect(p.locator(".hp-entry-origin")).toHaveCount(0);
  await p.waitForTimeout(400);
  const before = await p.evaluate(() => window.__resources());
  await p.evaluate(() => {
    window.__intervals = [];
    let last = performance.now(),
      start = last;
    const tick = (t) => {
      window.__intervals.push(t - last);
      last = t;
      if (t - start < 6000) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
  for (let i = 0; i < 20; i++) {
    await p
      .locator(".hp-run-detail")
      .nth(i % 3)
      .click();
    await p.waitForTimeout(65);
    await p.locator('.hp-inspector .hp-surface-header button[title="关闭"]').click();
    await p.waitForTimeout(65);
  }
  await p.waitForTimeout(1200);
  const after = await p.evaluate(() => window.__resources());
  expect(after.overlays).toBe(0);
  expect(after.listeners).toBeLessThanOrEqual(before.listeners);
  expect(after.timeouts).toBeLessThanOrEqual(before.timeouts);
  expect(after.raf).toBeLessThanOrEqual(before.raf + 1);
  const intervals = await p.evaluate(() => window.__intervals),
    sorted = [...intervals].sort((a, b) => a - b);
  const perf = {
    device: os.cpus()[0]?.model,
    os: os.platform() + " " + os.release(),
    browser: b.version(),
    viewport: "1680x1000",
    sampling:
      "6s requestAnimationFrame intervals during 20 open/close interrupts; 3 Run sources; one active Inspector",
    samples: sorted.length,
    p50: sorted[Math.floor(sorted.length * 0.5)],
    p95: sorted[Math.floor(sorted.length * 0.95)],
    max: sorted.at(-1),
    before,
    after,
  };
  await writeFile(resolve(out, "logs/resources-performance.json"), JSON.stringify(perf, null, 2));
  await p.goto("http://127.0.0.1:5321/product.html#/ai/" + data.conversationId);
  if (await p.getByLabel("用户名").isVisible()) {
    await p.getByLabel("用户名").fill("r1_review");
    await p.getByLabel("密码", { exact: true }).fill("r1-review-isolated-password");
    await p.getByRole("button", { name: "登录", exact: true }).click();
  }
  await expect(p.getByLabel("消息输入")).toBeVisible({ timeout: 25000 });
  await expect(p.locator(".hp-entry-origin")).toHaveCount(0, { timeout: 25000 });
  await p.waitForTimeout(300);
  const count = await p.locator(".hp-run-detail").count();
  expect(count).toBeGreaterThan(0);
  requests.length = 0;
  await p.locator(".hp-run-detail").first().click();
  await expect(p.locator(".hp-inspector")).toBeVisible();
  await expect(p.locator("[data-inspector-phase]")).toHaveAttribute(
    "data-inspector-phase",
    "receiving",
  );
  const requestDuringGate = await p
    .locator("[data-inspector-gate]")
    .evaluate((n) => ({ t: Date.now(), hidden: n.hidden, hash: location.hash }));
  await expect(p.locator(".hp-run-inspector")).toBeVisible();
  await p.screenshot({ path: resolve(out, "frames/real-api-run.png") });
  expect(requests.some((r) => r.type === "request" && r.t < requestDuringGate.t)).toBe(true);
  await p.locator('.hp-inspector .hp-surface-header button[title="关闭"]').click();
  await expect(p.locator(".hp-inspector")).toHaveCount(0);
  await writeFile(
    resolve(out, "logs/real-api.json"),
    JSON.stringify(
      {
        data: "actual isolated 8197 API; existing Fake-executor records; no browser business fixtures; read-only",
        requests,
        requestDuringGate,
        errors,
      },
      null,
      2,
    ),
  );
  expect(errors).toEqual([]);
  console.log("resources stable, performance sampled, actual API queried during receiving");
} finally {
  await c.close();
  await b.close();
}
