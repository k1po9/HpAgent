/* global window */
import "./r1-isolation.mjs";
import { chromium, expect } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import console from "node:console";
const out = resolve("artifacts/product-acceptance/ui-iteration/R2/attempt-03");
const b = await chromium.launch({
  executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
});
const login = await b.newContext({ reducedMotion: "reduce" }),
  p = await login.newPage();
await p.goto("http://127.0.0.1:5294/");
await p.getByLabel("用户名").fill("r1_review");
await p.getByLabel("密码", { exact: true }).fill("r1-review-isolated-password");
await p.getByRole("button", { name: "登录", exact: true }).click();
await expect(p.locator(".hp-entry-origin")).toHaveCount(0, { timeout: 20000 });
const storage = await login.storageState();
await login.close();
const proof = [];
for (const [width, end] of [
  [1680, "succeeded"],
  [1680, "failed"],
  [390, "succeeded"],
]) {
  const name = `clean-${width}-${end}`;
  const folder = resolve(out, "clips", name);
  await mkdir(folder, { recursive: true });
  const c = await b.newContext({
    storageState: storage,
    viewport: { width, height: width === 390 ? 844 : 1000 },
    locale: "zh-CN",
    timezoneId: "Asia/Shanghai",
    reducedMotion: "reduce",
    recordVideo: { dir: folder, size: { width, height: width === 390 ? 844 : 1000 } },
  });
  const q = await c.newPage();
  q.setDefaultTimeout(10000);
  await q.goto("http://127.0.0.1:5311/lab.html");
  await expect(q.locator(".hp-vitality--detail")).toBeVisible();
  await expect(q.locator(".hp-entry-origin")).toHaveCount(0);
  await q.emulateMedia({ reducedMotion: "no-preference" });
  await q.waitForTimeout(500);
  const zero = Date.now(),
    events = [];
  for (const [phase, state] of [
    ["starting", "running"],
    ["calling_model", "thinking"],
    ["executing_tool", "tool"],
    ["waiting_for_account_execution", "waiting"],
    ["calling_model", "thinking"],
  ]) {
    await q.evaluate(
      ({ phase }) =>
        window.__reviewEmit("run.progress", {
          phase,
          summary: {
            starting: "正在持续执行",
            calling_model: "正在等待模型生成",
            executing_tool: "正在执行工具",
            waiting_for_account_execution: "正在等待账户执行资源",
          }[phase],
        }),
      { phase },
    );
    events.push({ ms: Date.now() - zero, state });
    await expect(q.locator(".hp-vitality--detail")).toHaveAttribute("data-vitality", state);
    await q.waitForTimeout(1400);
  }
  for (const phase of [
    "calling_model",
    "executing_tool",
    "waiting_for_account_execution",
    "calling_model",
  ]) {
    await q.evaluate(
      (phase) => window.__reviewEmit("run.progress", { phase, summary: "连续快速交接" }),
      phase,
    );
    events.push({ ms: Date.now() - zero, phase });
    await q.waitForTimeout(120);
  }
  await q.evaluate((end) => {
    const s = window.__reviewSeed;
    window.__reviewEmit("run." + end, {
      snapshot: {
        ...s,
        run: {
          ...s.run,
          status: end,
          failure:
            end === "failed"
              ? {
                  code: "tool_side_effect_uncertain",
                  message: "外部操作结果无法确认",
                  retryable: true,
                }
              : null,
        },
        assistant_message: {
          ...s.assistant_message,
          status: end === "succeeded" ? "completed" : "failed",
        },
      },
    });
  }, end);
  events.push({ ms: Date.now() - zero, state: end });
  await expect(q.locator(".hp-vitality--detail")).toHaveAttribute("data-vitality", end);
  await q.waitForTimeout(1600);
  await expect(q.locator(".hp-vitality--detail")).toHaveAttribute("data-celebrate", "false");
  if (end === "failed") await expect(q.getByTestId("retry-run")).toHaveCount(0);
  const video = q.video();
  await c.close();
  await video.saveAs(resolve(out, "clips", name + ".webm"));
  proof.push({
    width,
    end,
    events,
    source:
      "compiled production React with labeled browser GET/SSE fixture, load under reduce then normal motion; no page reload inside sequence",
    video: "clips/" + name + ".webm",
  });
  console.log(name);
}
await writeFile(
  resolve(out, "logs/clean-clips.json"),
  JSON.stringify({ browser: await b.version(), proof }, null, 2),
);
await b.close();
