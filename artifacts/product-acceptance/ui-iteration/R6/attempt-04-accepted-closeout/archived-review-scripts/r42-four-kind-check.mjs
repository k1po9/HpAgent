/* global window, document, location, performance, requestAnimationFrame, EventTarget, console, process, URL, setTimeout */
import { chromium, expect } from "@playwright/test";
import { resolve } from "node:path";
import { writeFile, mkdir } from "node:fs/promises";
import os from "node:os";
const out = resolve("artifacts/product-acceptance/ui-iteration/R4/attempt-16-four-kind-closure");
const browser = await chromium.launch({ args: ["--no-sandbox"] });
const conv = "01a13187-fc64-7b62-bc70-bc5a6d25c0ad",
  base = "http://127.0.0.1:5333/review/after/index.html";
const reports = [],
  errors = [];
const gate = (p) => p.locator("[data-inspector-gate]");
const close = (p) => p.locator('.hp-inspector button[title="关闭"]');
async function ready(p, kind) {
  await expect(p.locator(".hp-inspector--" + kind)).toBeVisible();
  await expect(gate(p)).not.toHaveAttribute("hidden");
}
async function route(p, hash) {
  await p.evaluate((h) => (location.hash = h), hash);
  await p.waitForTimeout(250);
}
async function setup(p) {
  await p.addInitScript(() => {
    const frames = new Set(),
      timers = new Set(),
      listeners = [];
    const raf = window.requestAnimationFrame.bind(window),
      cancel = window.cancelAnimationFrame.bind(window),
      timeout = window.setTimeout.bind(window),
      clear = window.clearTimeout.bind(window);
    window.requestAnimationFrame = (fn) => {
      const id = raf((t) => {
        frames.delete(id);
        fn(t);
      });
      frames.add(id);
      return id;
    };
    window.cancelAnimationFrame = (id) => {
      frames.delete(id);
      cancel(id);
    };
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
    const add = EventTarget.prototype.addEventListener,
      remove = EventTarget.prototype.removeEventListener;
    const wanted = new Set([
      "resize",
      "scroll",
      "wheel",
      "touchmove",
      "visibilitychange",
      "hpagent-motion-change",
      "storage",
    ]);
    EventTarget.prototype.addEventListener = function (type, fn, options) {
      const cap = typeof options === "boolean" ? options : !!options?.capture;
      if (
        wanted.has(type) &&
        !listeners.some((r) => r.target === this && r.type === type && r.fn === fn && r.cap === cap)
      )
        listeners.push({ target: this, type, fn, cap });
      return add.call(this, type, fn, options);
    };
    EventTarget.prototype.removeEventListener = function (type, fn, options) {
      const cap = typeof options === "boolean" ? options : !!options?.capture;
      const i = listeners.findIndex(
        (r) => r.target === this && r.type === type && r.fn === fn && r.cap === cap,
      );
      if (i >= 0) listeners.splice(i, 1);
      return remove.call(this, type, fn, options);
    };
    window.closureResources = () => ({
      raf: frames.size,
      timers: timers.size,
      listeners: listeners.length,
      overlays: document.querySelectorAll(".hp-inspector-relay,.hp-inspector-exit,.hp-action-relay")
        .length,
      animations: document.getAnimations().length,
    });
    window.closureEvents = [];
    window.addEventListener("hp-inspector-relay-event", (e) =>
      window.closureEvents.push({ t: performance.now(), hash: location.hash, ...e.detail }),
    );
  });
  p.on("pageerror", (e) => errors.push(e.message));
  p.setDefaultTimeout(10000);
}
async function check(name, fn) {
  const p = await browser.newPage({
    viewport: { width: 1680, height: 1000 },
    reducedMotion: "no-preference",
  });
  await setup(p);
  let evidence;
  try {
    evidence = await fn(p);
    reports.push({ name, result: "pass", evidence });
    console.log("PASS", name);
  } catch (e) {
    reports.push({ name, result: "fail", error: String(e) });
    console.log("FAIL", name, String(e));
  }
  await p.screenshot({ path: out + "/screenshots/four-kind-" + name + ".png" }).catch(() => {});
  await writeFile(
    out + "/evidence/four-kind-" + name + ".json",
    JSON.stringify(
      await p
        .evaluate(() => ({
          hash: location.hash,
          events: window.closureEvents,
          resources: window.closureResources(),
          requests: window.__taskRequests,
          focus: document.activeElement?.outerHTML.slice(0, 400),
        }))
        .catch(() => ({})),
      null,
      2,
    ),
  );
  await p.close();
  await writeFile(
    out + "/evidence/four-kind.json",
    JSON.stringify({ reports, errors, browser: browser.version(), fixtureOnly: true }, null, 2),
  );
}
await mkdir(out + "/screenshots", { recursive: true });
await check("task-artifact-run-file-task-history", async (p) => {
  await p.goto(base + `#/ai/${conv}?inspect=task%3Aai-task&tab=outputs`);
  await ready(p, "task");
  const history = [];
  const capture = async () => history.push(await p.evaluate(() => location.hash));
  await capture();
  await p.getByRole("button", { name: "查看原引用版本", exact: true }).click();
  await ready(p, "artifact");
  await expect(p).toHaveURL(/version=r42-B-v1/);
  await capture();
  await p.getByRole("tab", { name: "详情", exact: true }).click();
  await p.getByRole("button", { name: "r4-work", exact: true }).click();
  await ready(p, "run");
  await capture();
  await p.getByRole("tab", { name: "使用资料与输出", exact: true }).click();
  await p.locator("#run-file-r4-work-ai-input-node").click();
  await ready(p, "file");
  await capture();
  await p.getByRole("tab", { name: "详情", exact: true }).click();
  await p.getByRole("button", { name: "查看来源任务", exact: true }).click();
  await ready(p, "task");
  await capture();
  for (const kind of ["file", "run", "artifact", "task"]) {
    await p.getByRole("button", { name: "返回", exact: true }).click();
    await ready(p, kind);
    await capture();
  }
  await expect(p.locator("#task-tab-outputs")).toHaveAttribute("aria-selected", "true");
  await close(p).click();
  await expect(p.locator(".hp-inspector")).toHaveCount(0);
  expect(
    await p
      .locator(".hp-inspector-exit")
      .evaluateAll((ns) =>
        ns.every(
          (n) =>
            n.inert &&
            n.getAttribute("aria-hidden") === "true" &&
            !n.textContent.trim() &&
            !n.querySelector("button,input,iframe,[tabindex]"),
        ),
      ),
  ).toBe(true);
  return { history };
});
await check("version-dom-draft-identity", async (p) => {
  await p.goto(base + `#/ai/${conv}?inspect=task%3Aai-task&tab=outputs`);
  await ready(p, "task");
  await p.getByRole("button", { name: "查看原引用版本", exact: true }).click();
  await ready(p, "artifact");
  const frame = p.getByTitle("Artifact 预览");
  await frame.evaluate((n) => (window.savedFrame = n));
  await p.frameLocator('iframe[title="Artifact 预览"]').locator("#counter").click();
  await p.getByLabel("修改指令").fill("跨Tab/扩大阅读保留");
  await p.getByLabel("修改指令").evaluate((n) => (window.savedEditor = n));
  const count = await p.evaluate(
    () => window.closureEvents.filter((e) => e.direction === "arrival").length,
  );
  for (const tab of ["版本历史", "详情", "预览"])
    await p.getByRole("tab", { name: tab, exact: true }).click();
  await p.locator(".hp-inspector-expand").click();
  await p.locator(".hp-inspector-expand").click();
  expect(await frame.evaluate((n) => n === window.savedFrame)).toBe(true);
  expect(await p.getByLabel("修改指令").evaluate((n) => n === window.savedEditor)).toBe(true);
  await expect(p.getByLabel("修改指令")).toHaveValue("跨Tab/扩大阅读保留");
  await expect(p.frameLocator('iframe[title="Artifact 预览"]').locator("#counter")).toHaveText("1");
  await p.getByRole("tab", { name: "版本历史", exact: true }).click();
  await p.getByRole("button", { name: "查看 v2", exact: true }).first().click();
  await p.getByRole("tab", { name: "预览", exact: true }).click();
  await expect(p.frameLocator('iframe[title="Artifact 预览"]').locator("#version")).toHaveText(
    "r42-B · v2",
  );
  expect(await frame.evaluate((n) => n === window.savedFrame)).toBe(false);
  expect(
    await p.evaluate(() => window.closureEvents.filter((e) => e.direction === "arrival").length),
  ).toBe(count);
  await p.getByRole("button", { name: "返回任务成果与验收", exact: true }).click();
  await ready(p, "task");
  await p.getByRole("button", { name: "查看原引用版本", exact: true }).click();
  await ready(p, "artifact");
  await expect(p).toHaveURL(/version=r42-B-v1/);
  return {
    sameVersionIframeRetained: true,
    editorRetained: true,
    originalTaskVersionUnchanged: true,
  };
});
await check("mixed-late-replies", async (p) => {
  await p.goto(base + `#/ai/${conv}`);
  await expect(p.locator("#run-open-r4-A")).toHaveCount(1);
  await p.evaluate(() => {
    const fetch = window.fetch;
    window.fetch = async (input, init) => {
      const path = new URL(typeof input === "string" ? input : input.url, location.href).pathname;
      if (path === "/api/v1/runs/r4-A" || path === "/api/v1/artifacts/r42-B")
        await new Promise((r) => setTimeout(r, 2200));
      return fetch(input, init);
    };
  });
  await p.locator("#run-open-r4-A").evaluate((n) => n.scrollIntoView({ block: "center" }));
  await p.waitForTimeout(150);
  await p.locator("#run-open-r4-A").click();
  await p.locator("#artifact-open-r42-B").evaluate((n) => n.scrollIntoView({ block: "center" }));
  await p.locator("#artifact-open-r42-B").click();
  await route(p, `#/ai/${conv}?inspect=task%3Aai-task&tab=outputs`);
  await ready(p, "task");
  await p.getByRole("button", { name: "查看空间入口", exact: true }).click();
  await ready(p, "file");
  await p.waitForTimeout(2600);
  await ready(p, "file");
  await expect(
    p.locator(".hp-inspector--task,.hp-inspector--artifact,.hp-inspector--run"),
  ).toHaveCount(0);
  return { final: "file", delayed: ["run", "artifact"] };
});
await check("browser-history-refresh", async (p) => {
  await p.goto(base + "#/tasks?bucket=attention&type=all");
  await p.locator("#task-ai-task").click();
  await ready(p, "task");
  await p.getByRole("tab", { name: "成果与执行", exact: true }).click();
  await p.getByRole("button", { name: "查看原引用版本", exact: true }).click();
  await ready(p, "artifact");
  const artifactHash = await p.evaluate(() => location.hash);
  await p.goBack();
  await ready(p, "task");
  await p.goForward();
  await ready(p, "artifact");
  expect(await p.evaluate(() => location.hash)).toBe(artifactHash);
  await p.reload();
  await ready(p, "artifact");
  await expect(p.locator(".hp-inspector-relay")).toHaveCount(0);
  return { artifactHash, noFabricatedSourceAfterRefresh: true };
});
await check("twenty-mixed-normal-cycles", async (p) => {
  await p.goto(base + `#/ai/${conv}`);
  const sources = [
    ["run", `#/ai/${conv}`, "#run-open-r4-A"],
    ["artifact", `#/ai/${conv}`, "#artifact-open-r42-B"],
    ["file", "#/workspace?dir=ai-folder", "#workspace-node-ai-input-node"],
    ["task", "#/tasks?bucket=attention&type=all", "#task-ai-task"],
  ];
  async function cycle(i) {
    const [kind, hash, id] = sources[i % 4];
    await route(p, hash);
    const source = p.locator(id);
    await source.evaluate((n) => n.scrollIntoView({ block: "center" }));
    await p.waitForTimeout(100);
    await source.click();
    await ready(p, kind);
    await close(p).click();
    await expect(p.locator(".hp-inspector-exit,.hp-inspector-relay")).toHaveCount(0);
    return await p.evaluate(() => window.closureResources());
  }
  for (let i = 0; i < 4; i++) await cycle(i);
  await route(p, `#/ai/${conv}`);
  await p.waitForTimeout(500);
  const before = await p.evaluate(() => window.closureResources());
  await p.evaluate(() => {
    window.frameIntervals = [];
    window.measureStart = performance.now();
    let last = performance.now();
    function tick(t) {
      window.frameIntervals.push(t - last);
      last = t;
      if (performance.now() - window.measureStart < 120000) requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  });
  const samples = [];
  const start = Date.now();
  for (let i = 0; i < 20; i++) samples.push(await cycle(i));
  await route(p, `#/ai/${conv}`);
  await p.waitForTimeout(500);
  const after = await p.evaluate(() => {
    window.measureStart = -Infinity;
    return window.closureResources();
  });
  expect(after.overlays).toBe(0);
  expect(after.listeners).toBe(before.listeners);
  expect(after.timers).toBeLessThanOrEqual(before.timers);
  expect(after.raf).toBeLessThanOrEqual(before.raf + 1);
  const intervals = await p.evaluate(() => window.frameIntervals),
    sorted = intervals.sort((a, b) => a - b);
  return {
    device: os.cpus()[0]?.model,
    os: os.platform() + " " + os.release(),
    viewport: "1680x1000",
    throttle: "none",
    activeInspectors: 1,
    durationMs: Date.now() - start,
    before,
    after,
    samples,
    frameSamples: sorted.length,
    p50: sorted[Math.floor(sorted.length * 0.5)],
    p95: sorted[Math.floor(sorted.length * 0.95)],
    max: sorted.at(-1),
    method:
      "20 mixed normal cycles after warming four kinds; selected listener/RAF/timer instrumentation; not heap proof or 60fps claim",
  };
});
await browser.close();
if (reports.some((r) => r.result === "fail") || errors.length) process.exitCode = 1;
