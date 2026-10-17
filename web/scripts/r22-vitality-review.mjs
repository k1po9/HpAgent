import "./review-runtime.mjs";
/* global window, document, getComputedStyle, sessionStorage, location, innerWidth, Response, ReadableStream, TextEncoder, URL, Event */
import "./r1-isolation.mjs";
import { chromium, expect } from "@playwright/test";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import console from "node:console";
import process from "node:process";
const out = resolve("artifacts/product-acceptance/ui-iteration/R2/attempt-03");
const routes = JSON.parse(
  await readFile(resolve("artifacts/product-acceptance/ui-iteration/R2/attempt-01/routes.json")),
);
const b = await chromium.launch({
  executablePath: process.env.HPAGENT_BROWSER_EXECUTABLE,
});
const login = await b.newContext({ reducedMotion: "reduce" }),
  p = await login.newPage();
await p.goto("http://127.0.0.1:5294/");
await p.getByLabel("用户名").fill("r1_review");
await p.getByLabel("密码", { exact: true }).fill("r1-review-isolated-password");
await p.getByRole("button", { name: "登录", exact: true }).click();
await expect(p.locator(".hp-entry-origin")).toHaveCount(0, { timeout: 20000 });
const runId = decodeURIComponent(routes.ST07.split("inspect=run%3A")[1]);
const conversationId = routes.ST02.split("/").at(-1);
const snapshot = await (await p.request.get("http://127.0.0.1:5294/api/v1/runs/" + runId)).json();
const detail = await (
  await p.request.get("http://127.0.0.1:5294/api/v1/conversations/" + conversationId)
).json();
const messagePage = await (
  await p.request.get("http://127.0.0.1:5294/api/v1/conversations/" + conversationId + "/messages")
).json();
await writeFile(
  resolve(out, "fixture.json"),
  JSON.stringify({ seed: snapshot, detail, messagePage, conversationId, runId }, null, 2),
);
const storage = await login.storageState();
await login.close();
const freshProof = {
  browser: await b.version(),
  data: "Real isolated login and existing conversation; controlled GET projections and ReadableStream SSE through actual runFeed/workbench. No provider or command API.",
  cases: [],
  errors: [],
};
const proof = process.argv.includes("--before-only")
  ? JSON.parse(await readFile(resolve(out, "logs/browser-vitality.json")))
  : freshProof;
if (process.argv.includes("--before-only"))
  proof.cases = proof.cases.filter((c) => c.version !== "before");
const injected = {
  ...snapshot,
  run: { ...snapshot.run, status: "queued", failure: null, created_at: new Date().toISOString() },
  assistant_message: {
    ...snapshot.assistant_message,
    status: "pending",
    content: "正在核对资料，并整理可操作的结论。",
  },
};
for (const [version, port, width] of [
  ["after", 5294, 1680],
  ["before", 5310, 1680],
  ["after", 5294, 390],
]) {
  if (process.argv.includes("--before-only") && version !== "before") continue;
  const folder = resolve(out, "clips", `${version}-${width}`);
  await mkdir(folder, { recursive: true });
  await mkdir(resolve(out, "screenshots"), { recursive: true });
  const c = await b.newContext({
    storageState: storage,
    viewport: { width, height: width === 390 ? 844 : 1000 },
    reducedMotion: "no-preference",
    locale: "zh-CN",
    timezoneId: "Asia/Shanghai",
    recordVideo: { dir: folder, size: { width, height: width === 390 ? 844 : 1000 } },
  });
  const q = await c.newPage();
  q.setDefaultTimeout(10000);
  q.on("pageerror", (e) => proof.errors.push(e.message));
  await q.addInitScript(
    ({ seed, detail, messagePage, conversationId, runId }) => {
      seed = JSON.parse(sessionStorage.getItem("r22-review-seed") || "null") || seed;
      const original = window.fetch.bind(window);
      let sink,
        seq = 0,
        serial = 0;
      const encoder = new TextEncoder();
      window.__reviewSeed = seed;
      const json = (body) =>
        new Response(JSON.stringify(body), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      window.__reviewEmit = (type, payload) => {
        const event = {
          schema_version: 1,
          event_id: "review-" + ++serial,
          event_type: type,
          conversation_id: conversationId,
          run_id: runId,
          message_id: seed.assistant_message.message_id,
          stream_id: type === "run.progress" || type === "run.status" ? "review-stream" : null,
          event_seq: type === "run.progress" || type === "run.status" ? ++seq : null,
          occurred_at: new Date().toISOString(),
          payload,
        };
        if (type === "run.status") seed.run.status = payload.status;
        if (type.startsWith("run.") && payload.snapshot)
          window.__reviewSeed = seed = payload.snapshot;
        sessionStorage.setItem("r22-review-seed", JSON.stringify(seed));
        sink?.enqueue(
          encoder.encode(
            `id: ${event.event_id}\nevent: ${type}\ndata: ${JSON.stringify(event)}\n\n`,
          ),
        );
        return event;
      };
      window.fetch = async (input, init) => {
        const url = new URL(
            typeof input === "string" ? input : input.url || String(input),
            location.origin,
          ),
          method = init?.method || "GET";
        if (method !== "GET") return original(input, init);
        if (url.pathname === `/api/v1/runs/${runId}/events`)
          return new Response(
            new ReadableStream({
              start(controller) {
                sink = controller;
                window.__reviewEmit("run.snapshot", { snapshot: seed });
                init?.signal?.addEventListener(
                  "abort",
                  () => {
                    sink = null;
                    controller.close();
                  },
                  { once: true },
                );
              },
              cancel() {
                sink = null;
              },
            }),
            { headers: { "Content-Type": "text/event-stream" } },
          );
        if (url.pathname === `/api/v1/conversations/${conversationId}`)
          return json({ ...detail, active_run: seed });
        if (url.pathname === `/api/v1/conversations/${conversationId}/messages`)
          return json({
            ...messagePage,
            items: messagePage.items.map((m) =>
              m.message_id === seed.assistant_message.message_id ? seed.assistant_message : m,
            ),
          });
        if (url.pathname === `/api/v1/runs/${runId}`) return json(seed);
        return original(input, init);
      };
    },
    { seed: injected, detail, messagePage, conversationId, runId },
  );
  await q.goto(`http://127.0.0.1:${port}/` + routes.ST02);
  await expect(q.locator(".hp-composer")).toBeVisible();
  await q.locator(`#run-open-${runId}`).click();
  await expect(q.locator(".hp-run-inspector")).toBeVisible();
  await q.waitForTimeout(700);
  const events = [],
    zero = Date.now();
  async function emit(type, payload) {
    const envelope = await q.evaluate(({ type, payload }) => window.__reviewEmit(type, payload), {
      type,
      payload,
    });
    events.push({ ms: Date.now() - zero, type, payload, envelope });
  }
  async function state(name, type, payload, hold = 1100) {
    await emit(type, payload);
    await q.waitForTimeout(hold);
    if (version === "after")
      await expect(q.locator(".hp-vitality--detail")).toHaveAttribute("data-vitality", name);
    await q.screenshot({ path: resolve(out, "screenshots", `${version}-${width}-${name}.png`) });
    console.log(version, width, name);
  }
  if (version === "after")
    await expect(q.locator(".hp-vitality--detail")).toHaveAttribute("data-vitality", "waiting");
  await q.screenshot({ path: resolve(out, "screenshots", `${version}-${width}-queued.png`) });
  await state("running", "run.status", { status: "running" });
  await state("thinking", "run.progress", { phase: "calling_model", summary: "正在等待模型生成" });
  await state("tool", "run.progress", { phase: "executing_tool", summary: "正在执行工具" });
  await state("waiting", "run.progress", {
    phase: "waiting_for_account_execution",
    summary: "正在等待账户执行资源",
  });
  if (version === "after") {
    if (width === 1680) {
      const frames = q.locator(".hp-vitality__frame");
      const before = await frames.evaluateAll((ns) => ns.map((n) => getComputedStyle(n).color));
      await emit("run.progress", { phase: "calling_model", summary: "快速变化：模型处理" });
      await q.waitForTimeout(120);
      const mid = await frames.evaluateAll((ns) => ns.map((n) => getComputedStyle(n).color));
      await emit("run.progress", { phase: "executing_tool", summary: "快速变化：工具调用" });
      await q.waitForTimeout(120);
      const next = await frames.evaluateAll((ns) => ns.map((n) => getComputedStyle(n).color));
      await q.screenshot({
        path: resolve(out, "screenshots", `${version}-${width}-quick-mid.png`),
      });
      proof.colorSamples = { before, mid, next };
    }
    for (let i = 0; i < 20; i++) {
      await emit("run.progress", {
        phase: i % 2 ? "calling_model" : "executing_tool",
        summary: "连续切换 " + (i + 1),
      });
      await q.waitForTimeout(100);
      expect(await q.locator(".hp-vitality__layer").count()).toBeLessThanOrEqual(
        width === 1680 ? 4 : 4,
      );
    }
    await q.waitForTimeout(700);
    const count = await q.locator(".hp-vitality").count();
    expect(await q.locator(".hp-vitality__layer").count()).toBe(count);
    await state("running", "run.progress", { phase: "finalizing", summary: "正在完成处理" });
    // True pending approvals are read only by existing resources query; fixtures do not persist decisions.
    await q.route("**/api/v1/runs/" + runId + "/file-action-approvals", (route) =>
      route.fulfill({ json: { approvals: [] } }),
    );
    await q.getByRole("tab", { name: "使用资料与输出" }).click();
    await q.waitForTimeout(300);
    await q.getByRole("tab", { name: "概览" }).click();
    await q.waitForTimeout(500);
    await q.screenshot({
      path: resolve(out, "screenshots", `${version}-${width}-approvals-none.png`),
    });
    await q.emulateMedia({ reducedMotion: "reduce" });
    await emit("run.progress", { phase: "calling_model", summary: "减动：模型处理" });
    await q.waitForTimeout(100);
    await expect(q.locator(".hp-vitality--detail")).toHaveAttribute("data-animate", "false");
    await q.screenshot({ path: resolve(out, "screenshots", `${version}-${width}-reduce.png`) });
    await q.emulateMedia({ reducedMotion: "no-preference" });
    await q.evaluate(() => {
      Object.defineProperty(document, "hidden", { configurable: true, value: true });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await q.waitForTimeout(80);
    await expect(q.locator(".hp-vitality--detail")).toHaveAttribute("data-animate", "false");
    await emit("run.progress", { phase: "executing_tool", summary: "后台状态已更新" });
    await q.evaluate(() => {
      Object.defineProperty(document, "hidden", { configurable: true, value: false });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await q.waitForTimeout(100);
    await expect(q.locator(".hp-vitality--detail")).toHaveAttribute("data-celebrate", "false");
    // Degrade must retain authoritative running, throw away phase and preserve stop.
    await emit("stream.degraded", { reason: "upstream_disconnected" });
    await q.waitForTimeout(350);
    await expect(q.locator(".hp-vitality--detail")).toHaveAttribute("data-vitality", "running");
    await expect(q.getByTestId("run-degraded").first()).toBeVisible();
    await q.screenshot({ path: resolve(out, "screenshots", `${version}-${width}-degraded.png`) });
    // Restart the page to get a new owned stream; old stream is aborted by real cleanup.
    await q.reload();
    await expect(q.locator(".hp-vitality--detail")).toBeVisible();
    await emit("run.status", { status: "running" });
    await q.waitForTimeout(200);
  }
  const success = await q.evaluate(() => ({
    ...window.__reviewSeed,
    run: { ...window.__reviewSeed.run, status: "succeeded", failure: null },
    assistant_message: { ...window.__reviewSeed.assistant_message, status: "completed" },
  }));
  await state("succeeded", "run.succeeded", { snapshot: success });
  if (version === "after") {
    await expect(q.locator(".hp-vitality--detail")).toHaveAttribute("data-celebrate", "false");
    await q.reload();
    await expect(q.locator(".hp-vitality--detail")).toBeVisible();
    await expect(q.locator(".hp-vitality--detail")).toHaveAttribute("data-celebrate", "false");
    await q.evaluate(() => {
      window.__reviewSeed.run.status = "running";
      sessionStorage.setItem("r22-review-seed", JSON.stringify(window.__reviewSeed));
    });
    await q.reload();
    await expect(q.locator(".hp-vitality--detail")).toBeVisible();
    await q.waitForTimeout(500);
  }
  if (version === "before") {
    await q.evaluate(() => {
      window.__reviewSeed.run.status = "running";
      sessionStorage.setItem("r22-review-seed", JSON.stringify(window.__reviewSeed));
    });
    await q.reload();
    await expect(q.locator(".hp-run-inspector")).toBeVisible();
    await q.waitForTimeout(300);
  }
  const failure = await q.evaluate(() => ({
    ...window.__reviewSeed,
    run: {
      ...window.__reviewSeed.run,
      status: "failed",
      failure: {
        code: "tool_side_effect_uncertain",
        message: "外部操作结果无法确认",
        retryable: true,
      },
    },
    assistant_message: { ...window.__reviewSeed.assistant_message, status: "failed" },
  }));
  await state("failed", "run.failed", { snapshot: failure });
  if (version === "after") await expect(q.getByTestId("retry-run")).toHaveCount(0);
  if (version === "after") {
    await q.evaluate(() => {
      window.__reviewSeed.run.status = "running";
      sessionStorage.setItem("r22-review-seed", JSON.stringify(window.__reviewSeed));
    });
    await q.reload();
    await expect(q.locator(".hp-vitality--detail")).toBeVisible();
    await state("cancelling", "run.status", { status: "cancelling" });
    const cancelled = await q.evaluate(() => ({
      ...window.__reviewSeed,
      run: { ...window.__reviewSeed.run, status: "cancelled", failure: null },
    }));
    await state("cancelled", "run.cancelled", { snapshot: cancelled });
    for (const w of width === 1680 ? [1280, 1279, 780, 1680] : [390]) {
      await q.setViewportSize({ width: w, height: w === 390 ? 568 : 1000 });
      await q.waitForTimeout(150);
      const geometry = await q.evaluate(() => ({
        width: innerWidth,
        scroll: document.documentElement.scrollWidth,
      }));
      expect(geometry.scroll).toBeLessThanOrEqual(geometry.width + 1);
      await q.screenshot({ path: resolve(out, "screenshots", `responsive-${w}.png`) });
      proof.cases.push({ name: "responsive " + w, result: "pass", geometry });
    }
  }
  const video = q.video();
  await c.close();
  await video.saveAs(resolve(out, "clips", `${version}-${width}.webm`));
  proof.cases.push({
    version,
    width,
    result: "pass",
    events,
    video: `clips/${version}-${width}.webm`,
  });
  await writeFile(resolve(out, "logs/browser-vitality.json"), JSON.stringify(proof, null, 2));
}
await b.close();
