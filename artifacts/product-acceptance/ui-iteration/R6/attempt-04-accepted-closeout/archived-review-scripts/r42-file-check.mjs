/* global window, document, performance, requestAnimationFrame, location, getComputedStyle, Event, console, process, EventTarget */
/* Review-only browser checks against an isolated static production bundle. */
import { chromium, expect } from "@playwright/test";
import { resolve } from "node:path";
import { mkdir, readFile, writeFile } from "node:fs/promises";
const out = resolve("artifacts/product-acceptance/ui-iteration/R4/attempt-14-file-relay");
const browser = await chromium.launch({ args: ["--no-sandbox"] });
const only = process.env.FILE_CHECK_ONLY?.split(",");
const results = only
    ? JSON.parse(await readFile(out + "/evidence/browser.json", "utf8")).results.filter(
        (r) => !only.includes(r.name),
      )
    : [],
  errors = [],
  network = [];
let pages = [];
const conv = "01a13187-fc64-7b62-bc70-bc5a6d25c0ad";
const root = "http://127.0.0.1:5331/review/after/index.html";
async function scene({
  width = 1680,
  height = 1000,
  motion = "no-preference",
  route = "#/workspace?dir=ai-folder",
  query = "",
} = {}) {
  const p = await browser.newPage({
    viewport: { width, height },
    locale: "zh-CN",
    timezoneId: "Asia/Shanghai",
    reducedMotion: motion,
  });
  await p.addInitScript(() => {
    const raf = window.requestAnimationFrame.bind(window),
      cancel = window.cancelAnimationFrame.bind(window);
    const frames = new Set();
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
    const tracked = new Map();
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
    function entries(target, type, capture) {
      if (!tracked.has(target)) tracked.set(target, new Map());
      const key = type + ":" + capture,
        map = tracked.get(target);
      if (!map.has(key)) map.set(key, new Set());
      return map.get(key);
    }
    EventTarget.prototype.addEventListener = function (type, fn, options) {
      if (wanted.has(type))
        entries(this, type, typeof options === "boolean" ? options : !!options?.capture).add(fn);
      return add.call(this, type, fn, options);
    };
    EventTarget.prototype.removeEventListener = function (type, fn, options) {
      if (wanted.has(type))
        entries(this, type, typeof options === "boolean" ? options : !!options?.capture).delete(fn);
      return remove.call(this, type, fn, options);
    };
    window.fileResourceCounts = () => ({
      raf: frames.size,
      listeners: [...tracked.values()].reduce(
        (sum, map) => sum + [...map.values()].reduce((n, set) => n + set.size, 0),
        0,
      ),
      animations: document.getAnimations().length,
      decorations: document.querySelectorAll(
        ".hp-inspector-relay,.hp-inspector-exit,.hp-title-capture",
      ).length,
    });
  });
  pages.push(p);
  p.setDefaultTimeout(8000);
  p.on("pageerror", (e) => errors.push(e.message));
  p.on("request", (r) => {
    if (/^https?:/.test(r.url()) && !r.url().startsWith("http://127.0.0.1:5331/"))
      network.push(r.url());
  });
  await p.goto(root + (query ? "?" + query : "") + route);
  await p.locator(".hp-shell").waitFor({ state: "visible" });
  await p.locator('.hp-entry[data-entering="false"]').waitFor();
  return p;
}
const gate = (p) => p.locator("[data-inspector-gate]");
const close = (p) => p.locator('.hp-inspector button[title="关闭"]');
async function ready(p, id = "ai-input-node") {
  await expect(gate(p)).not.toHaveAttribute("hidden");
  await expect(p.locator(".hp-file-inspector")).toBeVisible();
  await expect(p).toHaveURL(new RegExp("inspect=file%3A" + id));
}
async function sample(p) {
  await p.evaluate(() => {
    window.fileFrames = [];
    const start = performance.now();
    function tick() {
      const g = document.querySelector("[data-inspector-gate]"),
        r = document.querySelector(".hp-inspector-relay"),
        t = document.querySelector(".hp-inspector-title-arrival");
      window.fileFrames.push({
        t: performance.now(),
        gate: g ? { hidden: g.hidden, inert: g.inert } : null,
        title: !!t,
        label: document.querySelector(".hp-inspector")?.getAttribute("aria-label"),
        relay: r
          ? {
              tip: r.dataset.tipLength,
              length: r.dataset.totalLength,
              sx: r.dataset.sourceX,
              sy: r.dataset.sourceY,
              tx: r.dataset.targetX,
              ty: r.dataset.targetY,
            }
          : null,
        focus: document.activeElement?.id,
      });
      if (performance.now() - start < 6500) requestAnimationFrame(tick);
    }
    tick();
  });
}
async function check(name, fn) {
  if (only && !only.includes(name)) return;
  pages = [];
  try {
    const evidence = await fn();
    results.push({ name, result: "pass", evidence });
    console.log("PASS", name);
  } catch (e) {
    results.push({ name, result: "fail", error: String(e) });
    console.log("FAIL", name, String(e));
  } finally {
    for (const [i, p] of pages.entries()) {
      await p.screenshot({ path: `${out}/screenshots/${name}-${i}.png` }).catch(() => {});
      await writeFile(
        `${out}/evidence/${name}-${i}.json`,
        JSON.stringify(
          await p
            .evaluate(() => ({
              hash: location.hash,
              focus: {
                id: document.activeElement?.id,
                text: document.activeElement?.textContent?.slice(0, 120),
              },
              requests: window.__fileRequests,
              events: window.__r4events,
              fileRelayLog: window.__fileRelayLog,
              frames: window.fileFrames,
              hiddenLeaks: [...document.querySelectorAll(".hp-inspector [hidden],dialog[hidden]")]
                .filter((n) => getComputedStyle(n).display !== "none")
                .map((n) => n.outerHTML.slice(0, 100)),
              dialogs: [...document.querySelectorAll("dialog[open]:not(.hp-surface--region)")].map(
                (n) => n.getAttribute("aria-label"),
              ),
            }))
            .catch(() => ({})),
          null,
          2,
        ),
      );
      await p.close();
    }
    await writeFile(
      out + "/evidence/browser.json",
      JSON.stringify(
        { browser: browser.version(), fixtureOnly: true, results, errors, network },
        null,
        2,
      ),
    );
  }
}
await mkdir(out + "/screenshots", { recursive: true });
await check("actual-arrival", async () => {
  const p = await scene();
  const source = p.locator("#workspace-node-ai-input-node");
  await expect(source).toBeVisible();
  await source.evaluate((n) => (window.sourceNode = n));
  await sample(p);
  const click = await p.evaluate(() => performance.now());
  await source.click();
  await expect(gate(p)).toHaveAttribute("hidden");
  await expect(gate(p)).toHaveAttribute("inert");
  await expect(p.locator(".hp-inspector-relay")).toHaveCount(1);
  await p.screenshot({ path: out + "/screenshots/arrival-travel.png" });
  await p.waitForFunction(() => {
    const n = document.querySelector(".hp-inspector-relay");
    return n && +n.dataset.tipLength / +n.dataset.totalLength > 0.28;
  });
  await p.screenshot({ path: out + "/screenshots/arrival-mid.png" });
  await ready(p);
  await p.screenshot({ path: out + "/screenshots/arrival-body.png" });
  await expect(p.locator(".hp-file-preview")).toBeVisible();
  const e = await p.evaluate(() => ({
    events: window.__r4events,
    fileRelayLog: window.__fileRelayLog,
    frames: window.fileFrames,
    requests: window.__fileRequests,
    same: window.sourceNode === document.querySelector("#workspace-node-ai-input-node"),
  }));
  const arrived = e.events.find((e) => e.reason === "actual-tip" && e.direction === "arrival");
  expect(arrived).toBeTruthy();
  expect(arrived.tip).toBeGreaterThanOrEqual(arrived.length - 0.1);
  const title = e.frames.find((f) => f.title),
    body = e.frames.find((f) => f.gate && !f.gate.hidden);
  expect(body.t - title.t).toBeGreaterThan(140);
  const req = e.requests.find((r) => r.path === "/api/v1/files/ai-input");
  expect(req.start).toBeLessThan(title.t);
  expect(req.end).toBeLessThan(title.t);
  expect(e.same).toBe(true);
  await p.evaluate(() => (window.previewNode = document.querySelector(".hp-file-preview")));
  const count = e.events.length;
  await p.getByRole("tab", { name: "详情", exact: true }).click();
  await p.getByRole("button", { name: "查看来源链", exact: true }).click();
  await expect(p.getByText(/交付报告-v1.md/)).toBeVisible();
  await p.getByRole("tab", { name: "版本历史", exact: true }).click();
  const draft = p.getByLabel("编辑 Run ID");
  await draft.fill("keep-this-version-draft");
  await draft.evaluate((n) => (window.versionInput = n));
  await p.getByRole("tab", { name: "AI 使用范围", exact: true }).click();
  await p.getByLabel(/^目标对话/).selectOption(conv);
  await expect(p.getByRole("region", { name: "主体权限编辑" })).toBeVisible();
  await p.getByRole("tab", { name: "预览", exact: true }).click();
  await p.locator(".hp-inspector-expand").click();
  expect(
    await p.evaluate(() => window.previewNode === document.querySelector(".hp-file-preview")),
  ).toBe(true);
  await p.getByRole("tab", { name: "版本历史", exact: true }).click();
  await expect(draft).toHaveValue("keep-this-version-draft");
  expect(await draft.evaluate((n) => n === window.versionInput)).toBe(true);
  expect(await p.evaluate(() => window.__r4events.length)).toBe(count);
  await close(p).click();
  await p.screenshot({ path: out + "/screenshots/close-empty-shell.png" });
  expect(await p.evaluate(() => location.hash)).not.toContain("inspect");
  await expect(p.locator(".hp-inspector-exit")).toHaveAttribute("inert");
  expect(await p.locator(".hp-inspector-exit").textContent()).toBe("");
  await expect(
    p.locator(".hp-inspector-exit button,.hp-inspector-exit input,.hp-inspector-exit iframe"),
  ).toHaveCount(0);
  await expect(source).toBeFocused();
  await expect(p.locator(".hp-inspector-exit")).toHaveCount(0);
  return {
    click,
    arrived,
    titleBeforeBody: body.t - title.t,
    requestLead: title.t - req.start,
    sourceSame: e.same,
  };
});
await check("directory-keyboard", async () => {
  const p = await scene({ route: "#/workspace" });
  const b = p.locator("#workspace-details-ai-folder");
  await sample(p);
  await b.focus();
  await expect(b).toBeFocused();
  await p.keyboard.press("Enter");
  await ready(p, "ai-folder");
  await expect(p.getByRole("tab")).toHaveCount(2);
  expect(
    await p.evaluate(
      () => window.__fileRequests.filter((r) => /^\/api\/v1\/files\//.test(r.path)).length,
    ),
  ).toBe(0);
  expect(await p.evaluate(() => window.__r4events.some((e) => e.reason === "actual-tip"))).toBe(
    true,
  );
  await p.getByRole("tab", { name: "AI 使用范围", exact: true }).click();
  await p.getByLabel(/^目标对话/).selectOption(conv);
  await p.getByLabel("包含此目录的所有子目录与文件").check();
  await close(p).click();
  await expect(p.getByRole("dialog", { name: "放弃未提交的权限编辑？" })).toBeVisible();
  await p.getByRole("button", { name: "继续编辑", exact: true }).click();
  await expect(p.getByLabel("包含此目录的所有子目录与文件")).toBeChecked();
  await p.getByRole("tab", { name: "详情", exact: true }).click();
  await p.getByRole("button", { name: "放弃编辑并继续", exact: true }).click();
  await p.getByRole("tab", { name: "AI 使用范围", exact: true }).click();
  await expect(p.getByLabel("包含此目录的所有子目录与文件")).not.toBeChecked();
});
await check("slow-metadata", async () => {
  const p = await scene({ query: "fileCase=slow" });
  await sample(p);
  await p.locator("#workspace-node-ai-input-node").click();
  await ready(p);
  await expect(p.getByText("正在读取文件信息…")).toBeVisible();
  await expect(p.locator(".hp-file-preview")).toBeVisible();
  return await p.evaluate(() => window.__fileRequests);
});
await check("permission-failure", async () => {
  const p = await scene({ query: "fileCase=forbidden" });
  await p.locator("#workspace-node-ai-input-node").click();
  await expect(p.getByRole("alert")).toContainText("文件信息不可用");
  await expect(gate(p)).not.toHaveAttribute("hidden");
  await expect(p.locator(".hp-file-preview")).toHaveCount(0);
  await expect(p.getByRole("link", { name: "下载", exact: true })).toHaveCount(0);
});
await check("rapid-late-reopen", async () => {
  const p = await scene({ query: "fileCase=late" });
  await sample(p);
  await p.locator("#workspace-node-ai-input-node").click();
  await p.locator("#workspace-node-ai-output-node").click();
  await ready(p, "ai-output-node");
  await close(p).click();
  await p.locator("#workspace-node-ai-output-node").click();
  await ready(p, "ai-output-node");
  await p.waitForTimeout(2800);
  await expect(p.locator(".hp-file-inspector h3")).toHaveText("文件：交付报告.md");
  await expect(p.locator(".hp-inspector-exit")).toHaveCount(0);
});
for (const mode of ["reduce", "resize", "hidden", "scroll", "missing-source"])
  await check("interrupt-" + mode, async () => {
    const p = await scene();
    await sample(p);
    await p.locator("#workspace-node-ai-input-node").click();
    await expect(gate(p)).toHaveAttribute("hidden");
    if (mode === "reduce") await p.emulateMedia({ reducedMotion: "reduce" });
    if (mode === "resize") await p.setViewportSize({ width: 1280, height: 1000 });
    if (mode === "hidden")
      await p.evaluate(() => {
        Object.defineProperty(document, "hidden", { configurable: true, value: true });
        document.dispatchEvent(new Event("visibilitychange"));
      });
    if (mode === "scroll") await p.evaluate(() => window.dispatchEvent(new Event("wheel")));
    if (mode === "missing-source")
      await p.evaluate(() => document.querySelector("#workspace-node-ai-input-node").remove());
    await ready(p);
    await p.waitForTimeout(300);
    await expect(p.locator(".hp-inspector-relay")).toHaveCount(0);
    if (mode === "hidden")
      await p.evaluate(() => {
        delete document.hidden;
        document.dispatchEvent(new Event("visibilitychange"));
      });
    return await p.evaluate(() => ({
      events: window.__r4events,
      fileRelayLog: window.__fileRelayLog,
      frameCount: window.fileFrames.length,
    }));
  });
for (const width of [390, 780, 1279, 1280, 1680])
  await check("viewport-" + width, async () => {
    const p = await scene({ width, motion: "reduce" });
    await p.locator("#workspace-node-ai-input-node").click();
    await ready(p);
    await expect(close(p)).toBeInViewport();
    const modal = p.locator("dialog[open]:not(.hp-surface--region)");
    await expect(modal).toHaveCount(width < 1280 ? 1 : 0);
    await p.getByRole("button", { name: "在对话中使用", exact: true }).click();
    await expect(p.getByRole("dialog", { name: "在对话中使用" })).toBeVisible();
    await expect(modal).toHaveCount(1);
    await p.keyboard.press("Escape");
    await expect(p.getByRole("button", { name: "在对话中使用", exact: true })).toBeFocused();
    await p.getByRole("tab", { name: "版本历史", exact: true }).click();
    await p.getByLabel("编辑 Run ID").fill("viewport draft");
    await p.locator(".hp-inspector-expand").click();
    await expect(p.getByLabel("编辑 Run ID")).toHaveValue("viewport draft");
    expect(
      await p.evaluate(
        () =>
          [...document.querySelectorAll("[hidden]")].filter(
            (n) => n.closest(".hp-inspector") && getComputedStyle(n).display !== "none",
          ).length,
      ),
    ).toBe(0);
  });
await check("ai-run-file-back", async () => {
  const p = await scene({
    route: `#/ai/${conv}?inspect=run%3Ar4-A&tab=resources`,
    motion: "reduce",
  });
  await expect(p.locator("#run-file-r4-A-ai-input-node")).toBeVisible();
  await p.locator("#run-file-r4-A-ai-input-node").click();
  await ready(p);
  await p.getByRole("button", { name: "返回", exact: true }).click();
  await expect(p.locator("#run-file-r4-A-ai-input-node")).toBeVisible();
  expect(await p.evaluate(() => location.hash)).toContain("run%3Ar4-A");
});
await check("twenty-close-cycles", async () => {
  const p = await scene({ motion: "reduce" });
  const baseline = await p.evaluate(() => ({
    animations: document.getAnimations().length,
    relays: document.querySelectorAll(".hp-inspector-relay,.hp-inspector-exit").length,
  }));
  for (let i = 0; i < 20; i++) {
    await p.locator("#workspace-node-ai-input-node").click();
    await ready(p);
    await close(p).click();
  }
  await expect(p.locator(".hp-inspector-relay,.hp-inspector-exit")).toHaveCount(0);
  return {
    baseline,
    after: await p.evaluate(() => ({
      animations: document.getAnimations().length,
      relays: document.querySelectorAll(".hp-inspector-relay,.hp-inspector-exit").length,
    })),
  };
});
await check("normal-cycle-resources", async () => {
  const p = await scene();
  await p.waitForTimeout(300);
  const before = await p.evaluate(() => window.fileResourceCounts());
  const samples = [];
  for (let i = 0; i < 20; i++) {
    await p.locator("#workspace-node-ai-input-node").click();
    await ready(p);
    await close(p).click();
    await expect(p.locator(".hp-inspector-exit,.hp-inspector-relay")).toHaveCount(0);
    samples.push(await p.evaluate(() => window.fileResourceCounts()));
  }
  const after = samples.at(-1);
  expect(after.decorations).toBe(0);
  expect(after.listeners).toBe(before.listeners);
  expect(after.raf).toBeLessThanOrEqual(before.raf);
  return {
    before,
    after,
    samples,
    scope: "RAF and selected listener registrations; no heap profiling",
  };
});
await check("short-screen-and-root-text", async () => {
  const p = await scene({ width: 1280, height: 480, motion: "reduce" });
  await p.locator("#workspace-node-ai-input-node").click();
  await ready(p);
  await p.getByRole("tab", { name: "版本历史", exact: true }).click();
  await expect(close(p)).toBeInViewport();
  await p.getByLabel("编辑 Run ID").fill("short screen draft");
  await p.evaluate(() => (document.documentElement.style.fontSize = "200%"));
  await expect(close(p)).toBeInViewport();
  await p.getByLabel("编辑 Run ID").scrollIntoViewIfNeeded();
  await expect(p.getByLabel("编辑 Run ID")).toHaveValue("short screen draft");
  return { method: "root font-size 200%; not OS text size or native browser zoom" };
});
await check("long-frame", async () => {
  const p = await scene();
  await p.locator("#workspace-node-ai-input-node").click();
  await expect(p.locator(".hp-inspector-relay")).toHaveCount(1);
  await p.evaluate(() => {
    const t = performance.now();
    while (performance.now() - t < 280) {
      /* Deliberately block one frame in this fault test. */
    }
  });
  await ready(p);
  await expect(p.locator(".hp-inspector-relay")).toHaveCount(0);
  return await p.evaluate(() => window.__r4events);
});
await check("run-baseline", async () => {
  const p = await scene({ route: `#/ai/${conv}` });
  const button = p.locator("#run-open-r4-B");
  await button.scrollIntoViewIfNeeded();
  await p.waitForTimeout(200);
  await button.click();
  await expect(gate(p)).not.toHaveAttribute("hidden");
  await expect(p.locator("#inspector-run-title")).toHaveText("执行详情");
  expect(await p.evaluate(() => window.__r4events.some((e) => e.reason === "actual-tip"))).toBe(
    true,
  );
  const count = await p.evaluate(() => window.__r4events.length);
  await p.getByRole("tab", { name: "高级诊断", exact: true }).click();
  await p.locator(".hp-inspector-expand").click();
  expect(await p.evaluate(() => window.__r4events.length)).toBe(count);
  await close(p).click();
  expect(await p.evaluate(() => location.hash)).not.toContain("inspect");
  await expect(button).toBeFocused();
});
await check("task-file-back", async () => {
  const p = await scene({
    motion: "reduce",
    route: `#/ai/${conv}?inspect=task%3Aai-task&tab=outputs`,
  });
  await p.locator("#task-file-ai-task-r4-work").click();
  await ready(p, "ai-output-node");
  await p.getByRole("button", { name: "返回", exact: true }).click();
  await expect(p.locator("#task-file-ai-task-r4-work")).toBeVisible();
  expect(await p.evaluate(() => location.hash)).toContain("tab=outputs");
});
await check("offline-api-errors-and-deeplink", async () => {
  const p = await scene({
    motion: "reduce",
    route: `#/ai/${conv}?inspect=file%3Aai-input-node`,
    query: "fileCase=tree-error",
  });
  await expect(p.getByRole("alert")).toContainText("空间加载失败");
  await expect(gate(p)).not.toHaveAttribute("hidden");
  await expect(p.locator(".hp-file-preview")).toHaveCount(0);
  return { deepLinkNoFlight: await p.evaluate(() => window.__r4events.length === 0) };
});
await browser.close();
if (results.some((r) => r.result === "fail") || errors.length || network.length)
  process.exitCode = 1;
