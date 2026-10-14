import "./r1-isolation.mjs";
import process from "node:process";
import console from "node:console";
/* global document, getComputedStyle, innerWidth, window, CompositionEvent, KeyboardEvent */
import { chromium, expect } from "@playwright/test";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { Buffer } from "node:buffer";
import { auditContrast } from "./r13-material-contrast.mjs";
const out = resolve("artifacts/product-acceptance/ui-iteration/R3/attempt-01");
const batch = process.argv[2] ?? "checks-02";
const evidence = resolve(out, "screenshots", batch);
await mkdir(evidence, { recursive: true });
const routes = JSON.parse(await readFile(resolve(out, "routes.json")));
const base = "http://127.0.0.1:5294";
const b = await chromium.launch({
  executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
  headless: true,
});
const ctx = await b.newContext({
  viewport: { width: 1680, height: 1000 },
  locale: "zh-CN",
  timezoneId: "Asia/Shanghai",
  reducedMotion: "reduce",
});
const p = await ctx.newPage();
const results = [],
  metrics = [],
  errors = [];
p.on("pageerror", (e) => errors.push(e.message));
async function settled() {
  await expect(p.locator(".hp-entry")).toHaveAttribute("data-entering", "false");
  await expect(p.locator(".hp-entry-origin")).toHaveCount(0);
  await expect(p.locator(".hp-entry-destination")).not.toHaveAttribute("inert", "");
  await p.evaluate(() => document.fonts.ready);
}
async function goto(scene) {
  await p.goto(base + "/" + (routes[scene] ?? scene));
  await settled();
}
async function shot(name, data = "real isolated API") {
  const geometry = await p.evaluate(() => ({
    viewport: innerWidth,
    doc: document.documentElement.scrollWidth,
    modal: document.querySelector(".hp-inspector")?.getAttribute("aria-modal"),
  }));
  expect(geometry.doc).toBeLessThanOrEqual(geometry.viewport + 1);
  await p.screenshot({ path: resolve(evidence, name + ".png") });
  const contrast = await auditContrast(p);
  metrics.push({ name, data, geometry, contrast });
}
let checkIndex = 0;
async function check(name, fn) {
  checkIndex++;
  const until = process.argv.find((arg) => arg.startsWith("--until="));
  if (checkIndex > Number(until?.split("=")[1] ?? Infinity)) return;
  if (checkIndex < Number(process.argv[3] ?? 1)) {
    results.push({
      name,
      result: "not-run",
      reason: "prior batch passed; resume after fixture correction",
    });
    return;
  }
  try {
    await fn();
    results.push({ name, result: "pass" });
    console.log("PASS", name);
  } catch (e) {
    results.push({ name, result: "fail", error: e.message });
    await p.screenshot({ path: resolve(evidence, "failure-" + results.length + ".png") });
    throw e;
  }
}
try {
  await p.goto(base + "/");
  await p.getByLabel("用户名").fill("r1_review");
  await p.getByLabel("密码", { exact: true }).fill("r1-review-isolated-password");
  await p.getByRole("button", { name: "登录", exact: true }).click();
  await settled();
  await check("Inspector hides identifier in header and retains advanced diagnostics", async () => {
    await goto("ST07");
    const runId = decodeURIComponent(routes.ST07.split("inspect=run%3A")[1]);
    await expect(p.locator(".hp-run-identity")).toHaveText("对话执行");
    await expect(p.locator(".hp-run-identity")).not.toContainText(runId);
    await p.getByRole("tab", { name: "高级诊断", exact: true }).click();
    await expect(p.getByText("执行标识：" + runId, { exact: true })).toBeVisible();
    await shot("run-advanced-identifier");
    await p.getByRole("tab", { name: "概览", exact: true }).click();
    await expect(p.getByText("执行标识：" + runId, { exact: true })).toHaveCount(0);
  });
  await check("ST01 production empty + normal motion", async () => {
    await p.emulateMedia({ reducedMotion: "no-preference" });
    for (const v of ["r12"]) {
      await goto("#/ai");
      await expect(p.getByText("开始新的对话吧")).toBeVisible();
      await expect(p.getByRole("button", { name: "发送", exact: true })).toBeDisabled();
      await shot("ST01-" + v);
    }
    await p.emulateMedia({ reducedMotion: "reduce" });
  });
  await check("ST04 real multi-turn + multiple long-title objects", async () => {
    await goto("ST04");
    await expect(p.locator(".hp-artifact-message-card")).toHaveCount(2);
    await p.locator(".hp-thread__viewport").evaluate((e) => (e.scrollTop = 0));
    await shot("ST04-multi");
  });
  await check(
    "ST05 draft across conversations/regions + composition guard + Shift Enter",
    async () => {
      await goto("ST02");
      const input = p.getByLabel("消息输入");
      await input.fill("保留草稿：中文输入");
      await expect(p.locator(".hp-msg[data-role=assistant]").first()).toBeVisible();
      const count = await p.locator("[data-message-id]").count();
      await input.evaluate((el) => {
        el.dispatchEvent(new CompositionEvent("compositionstart", { bubbles: true }));
        el.dispatchEvent(
          new KeyboardEvent("keydown", {
            key: "Enter",
            bubbles: true,
            keyCode: 229,
            isComposing: true,
          }),
        );
        el.dispatchEvent(new CompositionEvent("compositionend", { bubbles: true }));
      });
      await expect(p.locator("[data-message-id]")).toHaveCount(count);
      await input.focus();
      await p.keyboard.press("Shift+Enter");
      await expect(input).toHaveValue("保留草稿：中文输入\n");
      await expect(input).toHaveValue("保留草稿：中文输入\n");
      await p.locator(`[data-conversation-id="${routes.ST03.split("/").at(-1)}"]`).click();
      await expect(input).toHaveValue("");
      await p.locator(`[data-conversation-id="${routes.ST02.split("/").at(-1)}"]`).click();
      await expect(input).toHaveValue("保留草稿：中文输入\n");
      await p
        .getByRole("navigation", { name: "主导航" })
        .getByRole("button", { name: "空间", exact: true })
        .click();
      await p
        .getByRole("navigation", { name: "主导航" })
        .getByRole("button", { name: "AI", exact: true })
        .click();
      await expect(input).toHaveValue("保留草稿：中文输入\n");
      await input.focus();
      await p.keyboard.press("Tab");
      expect(
        await p.evaluate(() => getComputedStyle(document.activeElement).outlineStyle),
      ).not.toBe("none");
      await shot("ST05-draft-focus");
      await input.fill("");
    },
  );
  await check("ST05 controlled slow/failed upload; real input guards", async () => {
    await goto("ST02");
    let reject;
    const gate = new Promise((r) => (reject = r));
    await p.route("**/api/v1/conversations/*/uploads", async (route) => {
      if (route.request().method() === "POST") {
        await gate;
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({
            error: { code: "r1_fixture_upload_unavailable", message: "受控故障：上传暂不可用" },
          }),
        });
      } else await route.continue();
    });
    await p.locator("input[type=file]").setInputFiles({
      name: "受控附件失败.md",
      mimeType: "text/markdown",
      buffer: Buffer.from("# R1 upload fixture"),
    });
    await expect(p.locator(".hp-attachment--uploading")).toBeVisible();
    await p.getByLabel("消息输入").fill("等待附件");
    await expect(p.getByRole("button", { name: "发送", exact: true })).toBeDisabled();
    await shot("ST05-uploading", "controlled network failure on real app");
    reject();
    await expect(p.locator(".hp-attachment--failed")).toBeVisible();
    await expect(p.getByRole("button", { name: "发送", exact: true })).toBeDisabled();
    await shot("ST05-failed", "controlled network failure on real app");
    await p.getByRole("button", { name: "移除 受控附件失败.md" }).click();
    await expect(p.getByRole("button", { name: "发送", exact: true })).toBeEnabled();
    await p.getByLabel("消息输入").fill("");
    await p.unroute("**/api/v1/conversations/*/uploads");
  });
  await check(
    "ST07 Artifact iframe unchanged across resizes/expanded + keyboard Tabs",
    async () => {
      await goto("artifact");
      await expect(p.getByTitle("Artifact 预览")).toBeVisible();
      await expect(p.frameLocator("iframe").locator("body")).not.toBeEmpty();
      await p.getByTitle("Artifact 预览").evaluate((el) => (window.r1FrameNode = el));
      await p
        .frameLocator("iframe")
        .locator("body")
        .evaluate((el) => {
          el.dataset.r1Marker = "preserved";
        });
      await p.getByLabel("修改指令").fill("版本切换外观时保留");
      for (const width of [1280, 1279, 780, 390, 1680]) {
        await p.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
        expect(
          await p.getByTitle("Artifact 预览").evaluate((el) => el === window.r1FrameNode),
        ).toBe(true);
        await expect(p.frameLocator("iframe").locator("body")).toHaveAttribute(
          "data-r1-marker",
          "preserved",
        );
        await expect(p.getByLabel("修改指令")).toHaveValue("版本切换外观时保留");
      }
      await p.getByRole("button", { name: "扩大阅读" }).click();
      await expect(p.frameLocator("iframe").locator("body")).toHaveAttribute(
        "data-r1-marker",
        "preserved",
      );
      await shot("ST07-artifact-expanded");
      await p.getByRole("button", { name: "恢复宽度" }).click();
      await p.getByRole("tab", { name: "预览", exact: true }).focus();
      await p.keyboard.press("End");
      await expect(p.getByRole("tab", { name: "详情", exact: true })).toBeFocused();
      await expect(p).toHaveURL(/tab=details/);
      await p.keyboard.press("Home");
      await expect(p.getByRole("tab", { name: "预览", exact: true })).toBeFocused();
    },
  );
  await check(
    "ST07 nested native Surface Escape preserves parent/iframe/draft/focus on mobile",
    async () => {
      await goto("artifact");
      await expect(p.frameLocator("iframe").locator("body")).not.toBeEmpty();
      await p
        .frameLocator("iframe")
        .locator("body")
        .evaluate((el) => {
          el.dataset.r1Marker = "preserved";
        });
      await p.getByLabel("修改指令").fill("版本切换外观时保留");
      await p.setViewportSize({ width: 390, height: 844 });
      await p.getByRole("button", { name: "保存源码副本到空间" }).click();
      await expect(p.locator(".hp-inspector")).toHaveAttribute("hidden", "");
      await expect(p.getByRole("dialog")).toHaveCount(1);
      await shot("ST08-nested-save");
      await p.keyboard.press("Escape");
      await expect(p.getByTitle("Artifact 预览")).toBeVisible();
      await expect(p.getByLabel("修改指令")).toHaveValue("版本切换外观时保留");
      await expect(p.frameLocator("iframe").locator("body")).toHaveAttribute(
        "data-r1-marker",
        "preserved",
      );
      await expect(p.getByRole("button", { name: "保存源码副本到空间" })).toBeFocused();
      await p.keyboard.press("Escape");
      await expect(p.locator(".hp-inspector")).toHaveCount(0);
    },
  );
  await check(
    "ST02/ST07 color refinement 1280 + 780; boundary 1279/1280; 390 short screen",
    async () => {
      for (const v of ["r12"])
        for (const width of [1280, 780, 1279])
          for (const scene of ["ST02", "ST07"]) {
            await p.setViewportSize({ width, height: 1000 });
            await goto(scene);
            if (scene === "ST07") {
              await expect(p.locator(".hp-inspector-card")).toBeVisible();
              await expect(p.locator(".hp-inspector")).toHaveAttribute(
                width < 1280 ? "aria-modal" : "role",
                width < 1280 ? "true" : "complementary",
              );
            }
            await shot(`${scene}-${width}-${v}`);
          }
      await p.setViewportSize({ width: 390, height: 600 });
      await goto("ST02");
      await expect(p.getByLabel("消息输入")).toBeVisible();
      await shot("ST02-short-390");
    },
  );
  await check("Mobile current Header inline editing and sidebar warm selection", async () => {
    await p.setViewportSize({ width: 390, height: 844 });
    await goto("ST02");
    await expect(p.locator(".hp-canvas-actions")).toHaveCount(0);
    await p.locator(".hp-title-trigger").dblclick();
    await expect(p.getByLabel("对话标题", { exact: true })).toBeFocused();
    await shot("ST02-title-editing-390");
    await p.keyboard.press("Escape");
    await expect(p.locator(".hp-title-trigger")).toBeFocused();
    await p.getByRole("button", { name: "打开侧栏" }).click();
    await p.locator(".hp-conv--active").scrollIntoViewIfNeeded();
    await shot("ST02-sidebar-390");
    await p.keyboard.press("Escape");
    await expect(p.getByRole("button", { name: "打开侧栏" })).toBeFocused();
  });
  await check("ST03 production table/code independent scroll", async () => {
    await p.setViewportSize({ width: 390, height: 844 });
    await goto("ST03");
    await p.locator(".hp-text-part table").scrollIntoViewIfNeeded();
    await shot("ST03-table-390");
    await p.locator(".hp-text-part pre").scrollIntoViewIfNeeded();
    await shot("ST03-code-390");
  });
  await check("ST08/ST09 persisted Workspace + File/Task Inspectors", async () => {
    await p.setViewportSize({ width: 1680, height: 1000 });
    await goto("workspace");
    await shot("ST09-workspace");
    await goto("file");
    await expect(p.locator(".hp-file-preview")).toBeVisible();
    await shot("ST08-file");
    await goto("task");
    await expect(p.locator(".hp-task-inspector")).toBeVisible();
    await shot("ST08-task");
    await goto("tasks");
    await shot("ST09-tasks");
    await p.setViewportSize({ width: 390, height: 844 });
    await shot("ST09-tasks-390");
  });
  await check("Production vertical task chain, ready attachment and return draft", async () => {
    await p.setViewportSize({ width: 1680, height: 1000 });
    await p.emulateMedia({ reducedMotion: "no-preference" });
    await goto("ST02");
    const draft = p.getByLabel("消息输入");
    await draft.fill("最终静态验收草稿：保留输入");
    await p.locator(`[data-conversation-id="${routes.ST03.split("/").at(-1)}"]`).click();
    await expect(p.locator(".hp-text-part h2")).toBeVisible();
    await p.locator(".hp-text-part pre").scrollIntoViewIfNeeded();
    await p.locator(`[data-conversation-id="${routes.ST02.split("/").at(-1)}"]`).click();
    await expect(draft).toHaveValue("最终静态验收草稿：保留输入");
    const fileName =
      "R1.3最终静态核对_一个很长的中英文文件名称_Reading-evidence-and-composer-attachment.md";
    await p.locator("input[type=file]").setInputFiles({
      name: fileName,
      mimeType: "text/markdown",
      buffer: Buffer.from("# R1.3 production task chain"),
    });
    await expect(p.locator(".hp-attachment--ready")).toBeVisible();
    for (let frame = 0; frame < 8; frame++) {
      const contrast = await auditContrast(p);
      expect(contrast.violations).toEqual([]);
    }
    await shot("ST05-ready-long-filename");
    await p.locator(".hp-artifact-message-card").first().click();
    await expect(p.frameLocator("iframe").locator("body")).not.toBeEmpty();
    // The persisted HTML fixture attaches its script after the body has rendered.
    const toggle = p.frameLocator("iframe").getByRole("button", { name: "切换状态" });
    await expect.poll(() => toggle.evaluate((el) => typeof el.onclick)).toBe("function");
    await toggle.click();
    await expect(p.frameLocator("iframe").locator("body")).toContainText("开启");
    await p.getByRole("tab", { name: "详情", exact: true }).click();
    await p.getByRole("tab", { name: "预览", exact: true }).click();
    await expect(p.frameLocator("iframe").locator("body")).not.toBeEmpty();
    await p
      .frameLocator("iframe")
      .locator("body")
      .evaluate((el) => (el.dataset.r1Marker = "production-flow"));
    await p.getByRole("button", { name: "保存源码副本到空间" }).click();
    await expect(p.getByRole("dialog")).toBeVisible();
    await p.keyboard.press("Escape");
    await expect(p.getByRole("button", { name: "保存源码副本到空间" })).toBeFocused();
    await expect(p.frameLocator("iframe").locator("body")).toHaveAttribute(
      "data-r1-marker",
      "production-flow",
    );
    await p.getByRole("button", { name: "关闭HTML 成果" }).click();
    await expect(draft).toHaveValue("最终静态验收草稿：保留输入");
    await p.getByRole("button", { name: "移除 " + fileName }).click();
    await draft.focus();
    await shot("ST05-return-continued-input");
    await draft.fill("");
    await p.setViewportSize({ width: 1680, height: 600 });
    await shot("ST02-desktop-short");
    await p.emulateMedia({ reducedMotion: "reduce" });
    await p.setViewportSize({ width: 390, height: 844 });
  });
  await check("ST09 login/register error; real form validation only", async () => {
    await p.getByRole("button", { name: "账户设置" }).click();
    await p.getByRole("button", { name: "退出登录", exact: true }).click();
    await expect(p.getByRole("heading", { name: "HpAgent 登录" })).toBeVisible();
    await shot("ST09-login-390");
    await p.getByRole("button", { name: "没有账号？注册" }).click();
    await p.getByLabel("用户名").fill("r1_validation_only");
    await p.getByLabel("密码", { exact: true }).fill("validation-password");
    await p.getByLabel("确认密码").fill("different-password");
    await p.getByRole("button", { name: "注册", exact: true }).click();
    await expect(p.getByRole("alert")).toHaveText("两次输入的密码不一致。");
    await shot("ST09-register-error-390");
  });
  expect(errors).toEqual([]);
  await writeFile(resolve(out, "routes.json"), JSON.stringify(routes, null, 2));
} finally {
  await writeFile(
    resolve(out, "logs/browser-" + batch + ".json"),
    JSON.stringify({ browser: await b.version(), results, metrics, errors }, null, 2),
  );
  await b.close();
}
