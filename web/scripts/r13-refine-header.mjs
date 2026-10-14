/* global document, innerWidth, CompositionEvent, KeyboardEvent */
import "./r1-isolation.mjs";
import { chromium, expect } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { URL } from "node:url";
import console from "node:console";
import process from "node:process";
import { auditContrast } from "./phase1-contrast.mjs";
const out = resolve("artifacts/product-acceptance/ui-iteration/R1.3/attempt-02"),
  dir = resolve(
    out,
    process.argv[2] === "--isolated" ? "screenshots" : "screenshots/" + process.argv[2],
  );
await mkdir(dir, { recursive: true });
const browser = await chromium.launch({
    executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
  }),
  p = await browser.newPage({
    viewport: { width: 1680, height: 1000 },
    locale: "zh-CN",
    timezoneId: "Asia/Shanghai",
    reducedMotion: "reduce",
  });
const base = "http://127.0.0.1:5294",
  route = "#/ai/01a1318f-e479-7d69-bdb5-ab921b23f696";
const results = [],
  metrics = [],
  errors = [];
p.on("pageerror", (e) => errors.push(e.message));
async function settled() {
  await expect(p.locator(".hp-entry-origin")).toHaveCount(0);
  await expect(p.locator(".hp-entry-destination")).not.toHaveAttribute("inert", "");
  await p.evaluate(() => document.fonts.ready);
}
async function shot(name) {
  const overflow = await p.evaluate(() => ({
    viewport: innerWidth,
    document: document.documentElement.scrollWidth,
  }));
  expect(overflow.document).toBeLessThanOrEqual(overflow.viewport + 1);
  await p.screenshot({ path: resolve(dir, name + ".png") });
  metrics.push({ name, overflow, contrast: await auditContrast(p) });
}
try {
  await p.goto(base + "/");
  await p.getByLabel("用户名").fill("r1_review");
  await p.getByLabel("密码", { exact: true }).fill("r1-review-isolated-password");
  await p.getByRole("button", { name: "登录", exact: true }).click();
  await settled();
  for (const width of [1680, 390])
    for (const version of ["before", "after"]) {
      await p.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
      await p.goto((version === "before" ? "http://127.0.0.1:5299" : base) + "/" + route);
      await settled();
      await expect(p.locator(".hp-conversation-header h1")).toBeVisible();
      await p.locator(".hp-thread__viewport").evaluate((e) => (e.scrollTop = 0));
      await p.evaluate(() => document.activeElement?.blur());
      const idleHeight = await p
        .locator(".hp-canvas-header")
        .evaluate((el) => el.getBoundingClientRect().height);
      await shot(`header-${width}-${version}`);
      await p
        .locator(".hp-canvas-header")
        .screenshot({ path: resolve(dir, `detail-header-${width}-${version}.png`) });
      if (version === "before")
        await p.getByRole("button", { name: "修改标题", exact: true }).click();
      else {
        await expect(p.locator(".hp-canvas-actions")).toHaveCount(0);
        await expect(p.getByLabel("对话工具")).toHaveCount(0);
        await p.locator(".hp-title-trigger").click();
        await expect(p.getByLabel("对话标题", { exact: true })).toHaveCount(0);
        await p.locator(".hp-title-trigger").dblclick();
      }
      await expect(p.getByLabel("对话标题", { exact: true })).toBeFocused();
      if (version === "after")
        expect(
          await p.locator(".hp-canvas-header").evaluate((el) => el.getBoundingClientRect().height),
        ).toBe(idleHeight);
      await shot(`editing-${width}-${version}`);
      await p
        .locator(".hp-canvas-header")
        .screenshot({ path: resolve(dir, `detail-editing-${width}-${version}.png`) });
      if (version === "after") {
        await expect(p.locator(".hp-conversation-header h1")).toHaveCount(0);
        await p.keyboard.press("Escape");
        await expect(p.locator(".hp-title-trigger")).toBeFocused();
      }
    }
  results.push({
    name: "same record 1680/390 removal and double-click inline editing; Escape/focus",
    result: "pass",
  });
  // Exercise actual PATCH on a new disposable conversation, leaving the user's viewed record untouched.
  await p.setViewportSize({ width: 1680, height: 1000 });
  await p.goto(base + "/#/ai");
  await settled();
  await p.getByLabel("消息输入").fill("R1.3标题编辑专用验证");
  await p.getByRole("button", { name: "发送", exact: true }).click();
  await expect(p.getByTestId("run-label")).toHaveText("已完成");
  const id = new URL(p.url()).hash.split("/").at(-1),
    original = await p.locator(".hp-title-trigger").innerText();
  await p.getByLabel("消息输入").fill("重命名时保留的中文草稿");
  await p.locator(".hp-title-trigger").focus();
  await p.keyboard.press("F2");
  await p.getByLabel("对话标题", { exact: true }).fill("双击与键盘编辑 · R1.3");
  await p.getByLabel("对话标题", { exact: true }).evaluate((el) => {
    el.dispatchEvent(new CompositionEvent("compositionstart", { bubbles: true }));
    el.dispatchEvent(
      new KeyboardEvent("keydown", {
        key: "Enter",
        bubbles: true,
        isComposing: true,
        keyCode: 229,
      }),
    );
    el.dispatchEvent(new CompositionEvent("compositionend", { bubbles: true }));
  });
  await expect(p.getByLabel("对话标题", { exact: true })).toHaveCount(1);
  await p.keyboard.press("Enter");
  await expect(p.locator(".hp-title-trigger")).toHaveText("双击与键盘编辑 · R1.3");
  await expect(p.locator(".hp-title-trigger")).toBeFocused();
  await expect(p.getByLabel("消息输入")).toHaveValue("重命名时保留的中文草稿");
  const snapshot = await (await p.request.get(base + "/api/v1/conversations/" + id)).json();
  expect(snapshot.conversation.title).toBe("双击与键盘编辑 · R1.3");
  await p.locator(".hp-title-trigger").focus();
  await p.keyboard.press("Enter");
  await p.getByLabel("对话标题", { exact: true }).fill(original);
  await p.keyboard.press("Enter");
  await expect(p.locator(".hp-title-trigger")).toHaveText(original);
  await p.getByLabel("消息输入").fill("");
  results.push({
    name: "production keyboard F2/Enter, real rename API and draft retention, original title restored",
    result: "pass",
  });
  await p.goto(base + "/" + route);
  await settled();
  await p.getByRole("button", { name: "使用资料", exact: true }).click();
  await expect(p.getByRole("dialog")).toBeVisible();
  await p.keyboard.press("Escape");
  results.push({ name: "Composer resource entry remains available", result: "pass" });
  const touchContext = await browser.newContext({
    viewport: { width: 390, height: 844 },
    hasTouch: true,
    isMobile: true,
    storageState: await p.context().storageState(),
    locale: "zh-CN",
  });
  const touchPage = await touchContext.newPage();
  await touchPage.goto(base + "/" + route);
  await expect(touchPage.locator(".hp-entry-origin")).toHaveCount(0);
  await touchPage.locator(".hp-title-trigger").tap();
  await expect(touchPage.getByLabel("对话标题", { exact: true })).toBeFocused();
  await touchPage.getByRole("button", { name: "取消", exact: true }).tap();
  await expect(touchPage.locator(".hp-title-trigger")).toBeVisible();
  await touchContext.close();
  results.push({
    name: "mobile touch activation enters inline editor; cancel restores title",
    result: "pass",
  });
  expect(errors).toEqual([]);
  console.log(results);
} finally {
  await writeFile(
    resolve(
      out,
      "logs/" + (process.argv[2] === "--isolated" ? "browser" : process.argv[2]) + ".json",
    ),
    JSON.stringify({ browser: await browser.version(), results, metrics, errors }, null, 2),
  );
  await browser.close();
}
