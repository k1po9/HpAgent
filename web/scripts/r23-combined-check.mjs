import process from "node:process";
import "./review-runtime.mjs";
/* global window, getComputedStyle, location */
import "./r1-isolation.mjs";
import { chromium, expect } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import console from "node:console";
import { URL } from "node:url";
import { Buffer } from "node:buffer";
const out = resolve("artifacts/product-acceptance/ui-iteration/R2/attempt-04");
const b = await chromium.launch({
  executablePath: process.env.HPAGENT_BROWSER_EXECUTABLE,
});
const c = await b.newContext({
  viewport: { width: 1680, height: 1000 },
  reducedMotion: "reduce",
  locale: "zh-CN",
  timezoneId: "Asia/Shanghai",
  recordVideo: { dir: resolve(out, "clips/combined"), size: { width: 1680, height: 1000 } },
});
const p = await c.newPage();
p.setDefaultTimeout(10000);
const errors = [],
  results = [],
  events = [];
p.on("pageerror", (e) => errors.push(e.message));
await p.goto("http://127.0.0.1:5294/");
await p.getByLabel("用户名").fill("r1_review");
await p.getByLabel("密码", { exact: true }).fill("r1-review-isolated-password");
await p.getByRole("button", { name: "登录", exact: true }).click();
await expect(p.locator(".hp-entry-origin")).toHaveCount(0, { timeout: 20000 });
await mkdir(resolve(out, "screenshots/combined"), { recursive: true });
const zero = Date.now(),
  mark = (name) => {
    events.push({ name, ms: Date.now() - zero });
    console.log(name);
  };
const form = p.locator(".hp-composer"),
  input = p.getByLabel("消息输入");
// Real isolated upload, accepted send, actual SSE + Fake executor, detail, return to draft.
await p.goto("http://127.0.0.1:5294/#/ai");
await expect(form).toBeVisible();
await expect(p.locator(".hp-entry-origin")).toHaveCount(0);
await p.emulateMedia({ reducedMotion: "no-preference" });
await input.fill("R2组合验收：使用附件整理要点，然后继续阅读。");
await p.mouse.move(0, 0);
await expect(form).toHaveAttribute("data-action-phase", "ready");
await p.waitForTimeout(500);
await form.screenshot({ path: resolve(out, "screenshots/combined/composer-local-ready.png") });
mark("real eligible input");
await p.locator('.hp-composer input[type="file"]').setInputFiles({
  name: "r2-combination.txt",
  mimeType: "text/plain",
  buffer: Buffer.from("R2 disposable isolated review evidence. No external messages."),
});
await expect(p.getByText("已就绪", { exact: true })).toBeVisible({ timeout: 15000 });
await expect(form).toHaveAttribute("data-action-phase", "ready");
await p.screenshot({ path: resolve(out, "screenshots/combined/attachment-ready.png") });
mark("real uploaded attachment ready");
await p.getByRole("button", { name: "发送", exact: true }).hover();
await expect(form).toHaveAttribute("data-action-phase", "intent");
await p.waitForTimeout(400);
await form.screenshot({ path: resolve(out, "screenshots/combined/composer-local-intent.png") });
const accepted = p.waitForResponse(
  (r) => r.request().method() === "POST" && /\/messages$/.test(new URL(r.url()).pathname),
);
await p.getByRole("button", { name: "发送", exact: true }).click();
const dto = await (await accepted).json();
mark("real API accepted " + dto.run.status);
await expect(form).toHaveAttribute("data-action-phase", "active");
expect(await form.evaluate((n) => getComputedStyle(n, "::after").opacity)).toBe("0");
await p.screenshot({ path: resolve(out, "screenshots/combined/actual-running.png") });
await p.getByRole("button", { name: "查看执行详情", exact: true }).last().click();
await expect(p.locator(".hp-vitality--detail")).toBeVisible();
mark("detail same Run");
await expect(p.locator(".hp-vitality--detail")).toHaveAttribute("data-vitality", "succeeded", {
  timeout: 15000,
});
mark("real API/Fake executor terminal");
await p.screenshot({ path: resolve(out, "screenshots/combined/actual-terminal.png") });
await p.getByRole("button", { name: "关闭执行详情", exact: true }).click();
await input.fill("返回后继续写作的草稿");
await expect(form).toHaveAttribute("data-action-phase", "ready");
mark("return and continue input");
results.push({
  name: "real input + upload + send + active + terminal + detail + return",
  result: "pass",
  runId: dto.run.run_id,
});
const nav = p.getByRole("navigation", { name: "主导航" });
await nav.getByRole("button", { name: "任务", exact: true }).click();
await expect(p.locator("[data-task-lane]")).toHaveCount(4);
await p.getByRole("button", { name: "账户设置", exact: true }).click();
await p.waitForTimeout(500);
await p.screenshot({ path: resolve(out, "screenshots/combined/task-account.png") });
await p.keyboard.press("Escape");
await nav.getByRole("button", { name: "AI", exact: true }).click();
await expect(input).toHaveValue("返回后继续写作的草稿");
mark("B tasks/account/navigation with retained draft");
results.push({ name: "task four buckets/account/B return preserve draft", result: "pass" });
// Long running state from explicit external browser fixture, through the actual feed/store.
await p.goto("http://127.0.0.1:5314/lab.html");
await expect(p.locator(".hp-vitality--detail")).toBeVisible();
await p.evaluate(() => {
  location.hash = "#/ai/" + window.__reviewSeed.run.conversation_id;
});
await expect(p.locator(".hp-vitality--detail")).toHaveCount(0);
await p.getByRole("button", { name: "查看执行详情", exact: true }).first().click();
await expect(p.locator(".hp-vitality--detail")).toBeVisible();
await input.fill("活跃执行期间保留草稿，不能假装已就绪");
await p.evaluate(() =>
  window.__reviewEmit("run.progress", { phase: "calling_model", summary: "正在等待模型生成" }),
);
await expect(form).toHaveAttribute("data-action-phase", "active");
expect(await form.evaluate((n) => getComputedStyle(n, "::after").opacity)).toBe("0");
await p.screenshot({ path: resolve(out, "screenshots/combined/live-C-neutral-A.png") });
mark("controlled live C / neutral A");
for (let i = 0; i < 20; i++) {
  await p.evaluate(
    (i) =>
      window.__reviewEmit("run.progress", {
        phase: i % 2 ? "calling_model" : "executing_tool",
        summary: "快速切换 " + i,
      }),
    i,
  );
  await p.waitForTimeout(120);
}
await p.waitForTimeout(700);
expect(await p.locator(".hp-vitality__layer").count()).toBe(
  await p.locator(".hp-vitality").count(),
);
results.push({ name: "20 composite C changes keep A active, no seam", result: "pass" });
await p.getByRole("button", { name: "关闭执行详情", exact: true }).click();
await p.evaluate(() => {
  const s = window.__reviewSeed;
  window.__reviewEmit("run.succeeded", {
    snapshot: {
      ...s,
      run: { ...s.run, status: "succeeded" },
      assistant_message: { ...s.assistant_message, status: "completed" },
    },
  });
});
await expect(form).toHaveAttribute("data-action-phase", "ready");
await p.getByRole("button", { name: "发送", exact: true }).hover();
await expect(form).toHaveAttribute("data-action-phase", "intent");
await p.waitForTimeout(700);
await p.screenshot({ path: resolve(out, "screenshots/combined/static-C-ready-A.png") });
results.push({ name: "C terminal stops, A eligible intent resumes", result: "pass" });
// Explicit uploaded/failed branches never emit a ready seam.
await p.goto("http://127.0.0.1:5294/#/ai");
await expect(form).toBeVisible();
await input.fill("上传守卫验证");
let finish;
const latch = new Promise((r) => (finish = r));
await p.route("**/api/v1/conversations/*/uploads", async (route) => {
  await latch;
  await route.fulfill({
    status: 500,
    json: { error: { code: "upload_failed", message: "controlled failure" } },
  });
});
await p.locator('.hp-composer input[type="file"]').setInputFiles({
  name: "r2-delayed.txt",
  mimeType: "text/plain",
  buffer: Buffer.from("guard only"),
});
await expect(form).toHaveAttribute("data-action-phase", "blocked");
expect(await form.evaluate((n) => getComputedStyle(n, "::after").opacity)).toBe("0");
await p.screenshot({ path: resolve(out, "screenshots/combined/uploading-blocked.png") });
finish();
await expect(p.getByText("上传失败", { exact: true })).toBeVisible();
await expect(form).toHaveAttribute("data-action-phase", "blocked");
await p.screenshot({ path: resolve(out, "screenshots/combined/upload-failed.png") });
results.push({ name: "controlled uploading/failed attachment blocks A", result: "pass" });
await p.unroute("**/api/v1/conversations/*/uploads");
const video = p.video();
await c.close();
await video.saveAs(resolve(out, "clips/combined-chain.webm"));
await writeFile(
  resolve(out, "logs/combined-browser.json"),
  JSON.stringify(
    {
      browser: await b.version(),
      events,
      results,
      errors,
      source:
        "real API/Fake for full chain; explicit browser fixture for long running/rapid C; controlled upload failure",
    },
    null,
    2,
  ),
);
await b.close();
