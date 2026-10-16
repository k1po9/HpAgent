/* global window, document, performance, requestAnimationFrame, location, getComputedStyle, Event, console, process, EventTarget */
/* Review-only browser checks against an isolated static production bundle. */
import { chromium, expect } from "@playwright/test";
import { resolve } from "node:path";
import { mkdir, readFile, writeFile } from "node:fs/promises";
const out = resolve("artifacts/product-acceptance/ui-iteration/R4/attempt-16-four-kind-closure");
const browser = await chromium.launch({ args: ["--no-sandbox"] });
const only = process.env.TASK_CHECK_ONLY?.split(",");
const results = only
    ? JSON.parse(await readFile(out + "/evidence/browser.json", "utf8")).results.filter(
        (r) => !only.includes(r.name),
      )
    : [],
  errors = [],
  network = [];
let pages = [];
const conv = "01a13187-fc64-7b62-bc70-bc5a6d25c0ad";
const root = "http://127.0.0.1:5333/review/after/index.html";
async function scene({
  width = 1680,
  height = 1000,
  motion = "no-preference",
  route = "#/tasks?bucket=attention&type=all",
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
    window.taskResourceCounts = () => ({
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
    if (/^https?:/.test(r.url()) && !r.url().startsWith("http://127.0.0.1:5333/"))
      network.push(r.url());
  });
  await p.goto(root + (query ? "?" + query : "") + route);
  await p.locator(".hp-shell").waitFor({ state: "visible" });
  await p.locator('.hp-entry[data-entering="false"]').waitFor();
  return p;
}
const gate = (p) => p.locator("[data-inspector-gate]");
const close = (p) => p.locator('.hp-inspector button[title="关闭"]');
async function ready(p, id = "ai-task") {
  await expect(gate(p)).not.toHaveAttribute("hidden");
  await expect(p.locator(".hp-task-inspector")).toBeVisible();
  await expect(p).toHaveURL(new RegExp("inspect=task%3A" + id));
}
async function sample(p) {
  await p.evaluate(() => {
    window.taskFrames = [];
    const start = performance.now();
    function tick() {
      const g = document.querySelector("[data-inspector-gate]"),
        r = document.querySelector(".hp-inspector-relay"),
        t = document.querySelector(".hp-inspector-title-arrival");
      window.taskFrames.push({
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
              requests: window.__taskRequests,
              events: window.__r4events,
              taskRelayLog: window.__taskRelayLog,
              frames: window.taskFrames,
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
await check("actual-task-arrival", async () => {
  const p = await scene();
  const source = p.locator("#task-ai-task");
  await source.evaluate((n) => (window.sourceNode = n));
  await sample(p);
  await source.click();
  await expect(gate(p)).toHaveAttribute("hidden");
  await expect(gate(p)).toHaveAttribute("inert");
  await expect(p.locator(".hp-inspector")).toHaveAttribute("aria-label", "接纳中");
  await expect(p.locator(".hp-inspector-relay")).toHaveCount(1);
  await p.screenshot({ path: out + "/screenshots/task-travel.png" });
  await p.waitForFunction(() => {
    const r = document.querySelector(".hp-inspector-relay");
    return r && +r.dataset.tipLength / +r.dataset.totalLength > 0.3;
  });
  await p.screenshot({ path: out + "/screenshots/task-mid.png" });
  await ready(p);
  const e = await p.evaluate(() => ({
    events: window.__r4events,
    frames: window.taskFrames,
    requests: window.__taskRequests,
    same: window.sourceNode === document.querySelector("#task-ai-task"),
  }));
  const arrived = e.events.find((e) => e.reason === "actual-tip" && e.direction === "arrival");
  expect(arrived).toBeTruthy();
  expect(arrived.tip).toBeGreaterThanOrEqual(arrived.length - 0.1);
  const title = e.frames.find((f) => f.title),
    body = e.frames.find((f) => f.gate && !f.gate.hidden);
  expect(body.t - title.t).toBeGreaterThan(140);
  expect(e.requests.find((r) => r.path === "/api/v1/works/ai-task").start).toBeLessThan(title.t);
  expect(e.same).toBe(true);
  await close(p).click();
  await expect(p.locator(".hp-inspector-exit")).toHaveAttribute("inert");
  expect(await p.locator(".hp-inspector-exit").textContent()).toBe("");
  await expect(
    p.locator(".hp-inspector-exit button,.hp-inspector-exit input,.hp-inspector-exit textarea"),
  ).toHaveCount(0);
  await expect(source).toBeFocused();
  return { arrived, titleBeforeBody: body.t - title.t, sameSource: e.same };
});
for (const action of ["outputs", "resources"])
  await check("row-action-" + action, async () => {
    const p = await scene();
    await p.locator('[data-work-id="ai-task"] summary').click();
    await p.locator("#task-action-row-ai-task-" + action).click();
    await ready(p);
    await expect(p.locator("#task-tab-" + action)).toHaveAttribute("aria-selected", "true");
    return await p.evaluate(() => window.__taskRelayLog);
  });
await check("keyboard-and-dom-retention", async () => {
  const p = await scene();
  await p.locator("#task-ai-task").focus();
  await p.keyboard.press("Enter");
  await ready(p);
  const count = await p.evaluate(() => window.__r4events.length);
  await p.getByRole("tab", { name: "高级详情", exact: true }).click();
  const input = p.getByLabel("已验证来源消息编号");
  await input.fill("keep-source");
  await input.evaluate((n) => (window.targetInput = n));
  await p.getByRole("tab", { name: "概览", exact: true }).click();
  await p.locator(".hp-inspector-expand").click();
  await p.getByRole("tab", { name: "高级详情", exact: true }).click();
  expect(await input.evaluate((n) => n === window.targetInput)).toBe(true);
  await expect(input).toHaveValue("keep-source");
  await p.locator("#task-ai-task").click();
  await ready(p);
  await p.getByRole("tab", { name: "高级详情", exact: true }).click();
  expect(await input.evaluate((n) => n === window.targetInput)).toBe(true);
  expect(await p.evaluate(() => window.__r4events.length)).toBe(count);
  await p.locator("#task-tab-advanced").focus();
  await p.keyboard.press("Home");
  await expect(p.locator("#task-tab-overview")).toBeFocused();
});
await check("task-editor-cancel-and-discard", async () => {
  const p = await scene({ motion: "reduce" });
  await p.locator("#task-ai-task").click();
  await ready(p);
  await p.locator(".hp-inspector").getByRole("button", { name: "修改要求", exact: true }).click();
  const field = p.getByLabel("目标 / 提醒内容");
  await field.fill("private task draft");
  await field.evaluate((n) => (window.editorField = n));
  await p.getByRole("button", { name: "关闭修改任务要求", exact: true }).click();
  await expect(
    p.getByRole("dialog", { name: "放弃未提交的任务草稿？", exact: true }),
  ).toBeVisible();
  await p.keyboard.press("Escape");
  await expect(field).toHaveValue("private task draft");
  expect(await field.evaluate((n) => n === window.editorField)).toBe(true);
  const hash = await p.evaluate(() => location.hash);
  // Browser/hash navigation remains guarded even while a nested native dialog owns focus.
  await p.evaluate(
    () =>
      (location.hash =
        location.hash.replace(/tab=[^&]+/, "tab=advanced") +
        (location.hash.includes("tab=") ? "" : "&tab=advanced")),
  );
  await expect(p.getByRole("button", { name: "放弃草稿并继续", exact: true })).toBeVisible();
  await p.getByRole("button", { name: "继续编辑", exact: true }).click();
  await expect(p).toHaveURL(new RegExp(hash.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "$"));
  expect(await field.evaluate((n) => n === window.editorField)).toBe(true);
  await expect(field).toHaveValue("private task draft");
  await p.evaluate(() => (location.hash = location.hash + "&tab=advanced"));
  await p.getByRole("button", { name: "放弃草稿并继续", exact: true }).click();
  await expect(p.locator("#task-tab-advanced")).toHaveAttribute("aria-selected", "true");
  await expect(p.getByRole("dialog")).toHaveCount(0);
  await p.getByRole("tab", { name: "概览", exact: true }).click();
  await expect(field).toHaveCount(0);
  await p.locator(".hp-inspector").getByRole("button", { name: "修改要求", exact: true }).click();
  await expect(field).not.toHaveValue("private task draft");
});
await check("task-grant-cancel-and-discard", async () => {
  const p = await scene({ motion: "reduce" });
  await p.locator("#task-ai-task").click();
  await ready(p);
  await p.getByRole("tab", { name: "使用资料", exact: true }).click();
  await p.getByLabel("选择长期目录或文件").selectOption("ai-folder");
  const select = p.getByLabel("选择长期目录或文件");
  await select.evaluate((n) => (window.resourceSelect = n));
  const recursive = p.getByLabel("包含此目录的所有子目录与文件");
  await recursive.check();
  await p.getByRole("tab", { name: "高级详情", exact: true }).click();
  await p.getByRole("button", { name: "继续编辑", exact: true }).click();
  await expect(recursive).toBeChecked();
  await close(p).click();
  await p.getByRole("button", { name: "继续编辑", exact: true }).click();
  await expect(recursive).toBeChecked();
  await p.getByRole("tab", { name: "高级详情", exact: true }).click();
  await p.getByRole("button", { name: "放弃编辑并继续", exact: true }).click();
  await p.getByRole("tab", { name: "使用资料", exact: true }).click();
  await expect(recursive).not.toBeChecked();
  expect(await select.evaluate((n) => n === window.resourceSelect)).toBe(true);
});
await check("budget-and-rejected-write", async () => {
  const p = await scene({ motion: "reduce" });
  await p.locator("#task-ai-task").click();
  await ready(p);
  await p.locator(".hp-inspector").getByRole("button", { name: "提高预算", exact: true }).click();
  const value = p.getByRole("spinbutton", { name: /模型 Token 上限/ });
  await value.fill("120000");
  await value.evaluate((n) => (window.budgetInput = n));
  await p.getByRole("button", { name: "确认提高预算", exact: true }).click();
  await expect(p.getByRole("dialog", { name: "提高任务预算" }).getByRole("alert")).toBeVisible();
  await expect(value).toHaveValue("120000");
  expect(await value.evaluate((n) => n === window.budgetInput)).toBe(true);
  const writes = await p.evaluate(() => window.__taskRequests.filter((r) => r.method !== "GET"));
  expect(writes).toHaveLength(1);
  expect(writes[0].status).toBe(409);
  expect(JSON.parse(writes[0].body)).toEqual({
    budget_version: 1,
    limits: { model_total_tokens: 120000 },
  });
  await p.keyboard.press("Escape");
  await expect(
    p.locator(".hp-inspector").getByRole("button", { name: "提高预算", exact: true }),
  ).toBeFocused();
  return writes;
});
await check("acceptance-and-delivery-confirmations", async () => {
  const p = await scene({ motion: "reduce" });
  await p.locator("#task-ai-task").click();
  await ready(p);
  await p.getByRole("tab", { name: "成果与执行", exact: true }).click();
  await p.getByRole("button", { name: "接受这份成果", exact: true }).click();
  await expect(p.getByRole("dialog", { name: "接受指定成果版本" })).toContainText("r42-B-v1");
  expect(
    await p.evaluate(() => window.__taskRequests.filter((r) => r.method !== "GET").length),
  ).toBe(0);
  await p.getByRole("button", { name: "确认接受此版本", exact: true }).click();
  await expect(
    p.getByRole("dialog", { name: "接受指定成果版本" }).getByRole("alert"),
  ).toBeVisible();
  await p.keyboard.press("Escape");
  await p.getByRole("button", { name: "确认已送达", exact: true }).click();
  expect(
    await p.evaluate(() => window.__taskRequests.filter((r) => r.method !== "GET").length),
  ).toBe(1);
  await expect(p.getByRole("dialog", { name: "确认投递决策" })).toContainText("不代表已读");
  await p.getByRole("button", { name: "确认此投递决策", exact: true }).click();
  await expect(p.getByRole("dialog", { name: "确认投递决策" }).getByRole("alert")).toBeVisible();
  const writes = await p.evaluate(() => window.__taskRequests.filter((r) => r.method !== "GET"));
  expect(writes.map((w) => w.status)).toEqual([409, 409]);
  return writes;
});
await check("busy-and-unknown-command", async () => {
  const p = await scene({ motion: "reduce", query: "taskCase=busy" });
  await p.locator("#task-ai-task").click();
  await ready(p);
  await p.locator(".hp-inspector").getByRole("button", { name: "提高预算", exact: true }).click();
  await p.getByRole("spinbutton", { name: /模型 Token 上限/ }).fill("120000");
  await p.getByRole("button", { name: "确认提高预算", exact: true }).click();
  await expect(p.getByRole("button", { name: "确认提高预算", exact: true })).toBeDisabled();
  await expect(p.getByRole("button", { name: "确认提高预算", exact: true })).toBeEnabled();
  const q = await scene({ motion: "reduce", query: "taskCase=unknown" });
  await q.locator("#task-ai-task").click();
  await ready(q);
  await q.locator(".hp-inspector").getByRole("button", { name: "暂停任务", exact: true }).click();
  await expect(
    q.locator(".hp-inspector").getByRole("button", { name: "重试原操作", exact: true }),
  ).toBeVisible();
  await expect(
    q.locator(".hp-inspector").getByRole("button", { name: "提高预算", exact: true }),
  ).toBeDisabled();
  await q.locator(".hp-inspector").getByRole("button", { name: "重试原操作", exact: true }).click();
  const writes = await q.evaluate(() => window.__taskRequests.filter((r) => r.method !== "GET"));
  expect(writes).toHaveLength(2);
  expect(writes[1].headers).toEqual(writes[0].headers);
  expect(writes[1].body).toBe(writes[0].body);
  return writes;
});
for (const query of ["slow", "forbidden", "missing", "late"])
  await check("response-" + query, async () => {
    const p = await scene({ query: "taskCase=" + query });
    await sample(p);
    await p.locator("#task-ai-task").click();
    if (query === "forbidden" || query === "missing") {
      await expect(p.locator(".hp-inspector").getByRole("alert")).toContainText("对象不可用");
      await expect(p.locator(".hp-task-inspector")).toHaveCount(0);
      await expect(gate(p)).not.toHaveAttribute("hidden");
    } else if (query === "slow") {
      await ready(p);
      const e = await p.evaluate(() => ({
        events: window.__r4events,
        requests: window.__taskRequests,
      }));
      const arrival = e.events.find((e) => e.reason === "actual-tip");
      expect(arrival).toBeTruthy();
      // Task rows already supply a legitimate cached snapshot. The slow verification remains in flight.
      expect(e.requests.find((r) => r.path === "/api/v1/works/ai-task").end).toBeUndefined();
      await p.waitForTimeout(4000);
      await ready(p);
      return { cachedRow: true, ...e };
    } else {
      await p.locator("#task-ai-task-b").click();
      await p.locator("#task-ai-task-c").click();
      await ready(p, "ai-task-c");
      await p.waitForTimeout(2700);
      await expect(p.locator(".hp-task-inspector > h3")).toHaveText("项目评审与交付 C");
      await close(p).click();
      await p.locator("#task-ai-task-c").click();
      await ready(p, "ai-task-c");
    }
  });
for (const mode of ["reduce", "resize", "hidden", "scroll", "missing-source", "long-frame"])
  await check("interrupt-" + mode, async () => {
    const p = await scene();
    await p.locator("#task-ai-task").click();
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
      await p.evaluate(() => document.querySelector("#task-ai-task").remove());
    if (mode === "long-frame")
      await p.evaluate(() => {
        const t = performance.now();
        while (performance.now() - t < 280) {
          /* fault injection */
        }
      });
    await ready(p);
    await expect(p.locator(".hp-inspector-relay")).toHaveCount(0);
    if (mode === "hidden")
      await p.evaluate(() => {
        delete document.hidden;
        document.dispatchEvent(new Event("visibilitychange"));
      });
  });
for (const width of [390, 780, 1279, 1280, 1680])
  await check("viewport-" + width, async () => {
    const p = await scene({ width, motion: "reduce" });
    await p.locator("#task-ai-task").click();
    await ready(p);
    const modal = p.locator("dialog[open]:not(.hp-surface--region)");
    await expect(modal).toHaveCount(width < 1280 ? 1 : 0);
    await expect(close(p)).toBeInViewport();
    await p.locator(".hp-inspector").getByRole("button", { name: "提高预算", exact: true }).click();
    await expect(modal).toHaveCount(1);
    await p.keyboard.press("Escape");
    await expect(
      p.locator(".hp-inspector").getByRole("button", { name: "提高预算", exact: true }),
    ).toBeFocused();
    await p.getByRole("tab", { name: "高级详情", exact: true }).click();
    await p.getByLabel("已验证来源消息编号").fill("viewport draft");
    await p.locator(".hp-inspector-expand").click();
    await expect(p.getByLabel("已验证来源消息编号")).toHaveValue("viewport draft");
  });
await check("short-screen-root-text", async () => {
  const p = await scene({ width: 1280, height: 480, motion: "reduce" });
  await p.locator("#task-ai-task").click();
  await ready(p);
  await p.getByRole("tab", { name: "高级详情", exact: true }).click();
  await p.getByLabel("已验证来源消息编号").fill("text zoom draft");
  await p.evaluate(() => (document.documentElement.style.fontSize = "200%"));
  await expect(close(p)).toBeInViewport();
  await p.getByLabel("已验证来源消息编号").scrollIntoViewIfNeeded();
  await expect(p.getByLabel("已验证来源消息编号")).toHaveValue("text zoom draft");
});
await check("parent-task-children-back", async () => {
  const p = await scene({ motion: "reduce" });
  await p.locator("#task-ai-task").click();
  await ready(p);
  await p.getByRole("tab", { name: "成果与执行", exact: true }).click();
  for (const [button, child] of [
    ["查看原引用版本", "artifact"],
    ["查看空间入口", "file"],
    ["查看执行详情与诊断", "run"],
  ]) {
    await p.getByRole("button", { name: button, exact: true }).click();
    await expect(p.locator(".hp-inspector--" + child)).toBeVisible();
    await p.getByRole("button", { name: "返回", exact: true }).click();
    await ready(p);
    await expect(p.locator("#task-tab-outputs")).toHaveAttribute("aria-selected", "true");
  }
});
for (const source of ["file", "artifact", "inbox"])
  await check("incoming-" + source, async () => {
    const route =
      source === "file"
        ? `#/ai/${conv}?inspect=file%3Aai-output-node&tab=details`
        : source === "artifact"
          ? `#/ai/${conv}?inspect=task%3Aai-task&tab=overview`
          : "#/tasks?bucket=attention&type=all";
    const p = await scene({ route });
    if (source === "file")
      await p.getByRole("button", { name: "查看来源任务", exact: true }).click();
    if (source === "artifact") {
      await p.getByRole("tab", { name: "成果与执行", exact: true }).click();
      await p.getByRole("button", { name: "查看原引用版本", exact: true }).click();
    }
    if (source === "artifact")
      await p.getByRole("button", { name: "返回任务成果与验收", exact: true }).click();
    if (source === "inbox") {
      await p.getByRole("button", { name: "收件箱", exact: true }).click();
      await p.getByRole("button", { name: "查看任务", exact: true }).click();
    }
    await ready(p);
    await expect(p.locator(".hp-inspector-relay")).toHaveCount(0);
    if (source === "file") {
      await p.getByRole("button", { name: "返回", exact: true }).click();
      await expect(p.locator(".hp-file-inspector")).toBeVisible();
    }
    return {
      disconnectedSourceSettles: true,
      events: await p.evaluate(() => window.__taskRelayLog),
    };
  });
await check("close-during-travel-reopen", async () => {
  const p = await scene();
  await p.locator("#task-ai-task").click();
  await expect(gate(p)).toHaveAttribute("hidden");
  await close(p).click();
  await p.locator("#task-ai-task").click();
  await ready(p);
  await p.waitForTimeout(800);
  await ready(p);
  await expect(p.locator(".hp-inspector-exit")).toHaveCount(0);
});
await check("twenty-normal-cycles", async () => {
  const p = await scene();
  await p.waitForTimeout(350);
  const before = await p.evaluate(() => window.taskResourceCounts());
  const samples = [];
  for (let i = 0; i < 20; i++) {
    await p.locator("#task-ai-task").click();
    await ready(p);
    await close(p).click();
    await expect(p.locator(".hp-inspector-exit,.hp-inspector-relay")).toHaveCount(0);
    samples.push(await p.evaluate(() => window.taskResourceCounts()));
  }
  const after = samples.at(-1);
  expect(after.decorations).toBe(0);
  expect(after.listeners).toBe(before.listeners);
  expect(after.raf).toBeLessThanOrEqual(before.raf);
  return {
    before,
    after,
    samples,
    scope: "selected listener registrations and RAF; not heap profiling",
  };
});
await check("run-arrival-and-input-draft", async () => {
  const p = await scene({ route: `#/ai/${conv}` });
  const input = p.locator(".hp-composer__input");
  await input.fill("继续阅读后的输入草稿");
  await input.evaluate((n) => (window.chatInput = n));
  const source = p.locator("#run-open-r4-A");
  await source.evaluate((n) => n.scrollIntoView({ block: "center" }));
  await p.waitForTimeout(150);
  await source.click();
  await expect(p.locator("[data-inspector-gate]")).not.toHaveAttribute("hidden");
  await expect(p.locator("#inspector-run-title")).toBeVisible();
  await p.getByRole("tab", { name: "使用资料与输出", exact: true }).click();
  await expect(p.locator("#run-file-r4-A-ai-input-node")).toBeVisible();
  await close(p).click();
  await expect(input).toHaveValue("继续阅读后的输入草稿");
  expect(await input.evaluate((n) => n === window.chatInput)).toBe(true);
  expect(
    await p.evaluate(() => window.__taskRequests.filter((r) => r.method !== "GET").length),
  ).toBe(0);
});
await check("delivery-duplicate-risk", async () => {
  const p = await scene({ query: "taskCase=delivery-risk", motion: "reduce" });
  await p.locator("#task-ai-task").click();
  await ready(p);
  await p.getByRole("tab", { name: "成果与执行", exact: true }).click();
  await p.getByRole("button", { name: "接受重复风险并重试投递", exact: true }).click();
  await expect(p.getByRole("dialog", { name: "确认投递决策" })).toContainText("重复通知");
  expect(
    await p.evaluate(() => window.__taskRequests.filter((r) => r.method !== "GET").length),
  ).toBe(0);
  await p.getByRole("button", { name: "确认承担重复风险并重试", exact: true }).click();
  await expect(p.getByRole("dialog", { name: "确认投递决策" }).getByRole("alert")).toBeVisible();
  const writes = await p.evaluate(() => window.__taskRequests.filter((r) => r.method !== "GET"));
  expect(writes).toHaveLength(1);
  expect(writes[0].status).toBe(409);
  expect(JSON.parse(writes[0].body)).toEqual({ outcome: "retry_accepting_duplicate_risk" });
  return writes;
});
await browser.close();
if (results.some((r) => r.result === "fail") || errors.length || network.length)
  process.exitCode = 1;
console.log(
  JSON.stringify({
    groups: results.length,
    failed: results.filter((r) => r.result === "fail").map((r) => r.name),
    errors,
    network,
  }),
);
