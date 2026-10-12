/* global document, getComputedStyle, innerWidth */
import process from "node:process";
import { Buffer } from "node:buffer";
import console from "node:console";
// Real React UI + isolated API only. Never point this at a daily-use account.
import { auditContrast } from "./phase1-contrast.mjs";
import { chromium, expect } from "@playwright/test";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const evidence = resolve(
  root,
  process.env.HPAGENT_PHASE1_EVIDENCE_DIR ??
    "artifacts/product-acceptance/phase1-paper-flame-20261012",
);
const phase = process.env.HPAGENT_PHASE1_PHASE ?? "before";
if (process.env.HPAGENT_PHASE1_ISOLATED !== "1") throw new Error("Requires an isolated API");
const browser = await chromium.launch({
  headless: true,
  ...(process.env.HPAGENT_BROWSER_EXECUTABLE
    ? { executablePath: process.env.HPAGENT_BROWSER_EXECUTABLE }
    : {}),
});
const context = await browser.newContext({
  viewport: { width: 1680, height: 1000 },
  locale: "zh-CN",
  timezoneId: "Asia/Shanghai",
  reducedMotion: "reduce",
});
const page = await context.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
await mkdir(evidence, { recursive: true });
const base = process.env.HPAGENT_PHASE1_BASE_URL ?? "http://127.0.0.1:5290";
const user = process.env.HPAGENT_PHASE1_USER ?? "phase1_visual";
const password = process.env.HPAGENT_PHASE1_PASSWORD ?? "phase1-isolated-password";
let routes;
const metrics = [];
async function settled() {
  await expect(page.locator(".hp-entry")).toHaveAttribute("data-entering", "false");
  await expect(page.locator(".hp-entry-origin")).toHaveCount(0);
  await expect(page.locator(".hp-entry-destination")).not.toHaveAttribute("inert", "");
  await page.evaluate(() => document.fonts.ready);
}
async function shot(name) {
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: resolve(evidence, `${phase}-${name}.png`) });
  const geometry = await page.evaluate(() => {
    const regions = {};
    for (const selector of [
      ".hp-rail",
      ".hp-context-sidebar",
      ".hp-main-canvas",
      ".hp-inspector",
      ".hp-composer",
    ]) {
      const el = document.querySelector(selector);
      if (!el || el.hidden) continue;
      const r = el.getBoundingClientRect();
      const s = getComputedStyle(el);
      regions[selector] = {
        x: r.x,
        y: r.y,
        width: r.width,
        height: r.height,
        background: s.backgroundColor,
        color: s.color,
      };
    }
    return { viewport: innerWidth, document: document.documentElement.scrollWidth, regions };
  });
  expect(geometry.document).toBeLessThanOrEqual(geometry.viewport + 1);
  const contrast = phase === "after" ? await auditContrast(page) : undefined;
  metrics.push({ name, ...geometry, contrast });
  if (contrast) {
    expect(contrast.violations, name + " text contrast").toEqual([]);
    expect(contrast.legacyBlue, name + " default blue text").toEqual([]);
  }
}
try {
  await page.goto(base);
  await expect(page.getByRole("heading", { name: "HpAgent 登录" })).toBeVisible();
  for (const width of [1680, 1280, 780, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    await shot(`auth-${width}`);
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "没有账号？注册" }).click();
  await page.getByLabel("用户名").fill("client_validation_only");
  await page.getByLabel("密码", { exact: true }).fill("validation-password");
  await page.getByLabel("确认密码").fill("different-password");
  await page.getByRole("button", { name: "注册", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveText("两次输入的密码不一致。");
  await shot("register-error-390");
  await page.getByRole("button", { name: "已有账号？登录" }).click();
  await page.setViewportSize({ width: 1680, height: 1000 });
  if (process.env.HPAGENT_PHASE1_SEED === "1") {
    if (process.env.HPAGENT_PHASE1_REGISTER === "1")
      await page.getByRole("button", { name: "没有账号？注册" }).click();
    await page.getByLabel("用户名").fill(user);
    await page.getByLabel("密码", { exact: true }).fill(password);
    if (process.env.HPAGENT_PHASE1_REGISTER === "1")
      await page.getByLabel("确认密码").fill(password);
    await page
      .getByRole("button", {
        name: process.env.HPAGENT_PHASE1_REGISTER === "1" ? "注册" : "登录",
        exact: true,
      })
      .click();
    await expect(page.locator(".hp-shell")).toBeVisible();
    const skip = page.getByRole("button", { name: /以后再说/ });
    if (await skip.count()) await skip.first().click();
    await settled();
    await page.getByRole("button", { name: "新建对话", exact: true }).click();
    await page.getByLabel("选择已有文件", { exact: true }).click();
    await expect(page.locator(".hp-conv--active")).toBeVisible();
    await page.getByLabel("选择已有文件", { exact: true }).click();
    await page
      .getByLabel("消息输入")
      .fill("请整理视觉基础迁入检查：Conversation、Workspace、Tasks。");
    await page.getByRole("button", { name: "发送", exact: true }).click();
    await expect(page.getByTestId("run-label")).toHaveText("已完成");
    routes = { ai: page.url().replace(base, "") };
    await page.getByRole("button", { name: "生成 HTML", exact: true }).click();
    await expect(page.getByTitle("Artifact 预览")).toBeVisible();
    routes.artifact = page.url().replace(base, "");
    await page.getByRole("button", { name: "关闭HTML 成果" }).click();
    await page
      .getByRole("navigation", { name: "主导航" })
      .getByRole("button", { name: "空间", exact: true })
      .click();
    await page.getByRole("button", { name: "上传文件", exact: true }).click();
    const upload = page.getByRole("dialog", { name: "上传到空间" });
    await upload.getByLabel("上传长期文件").setInputFiles({
      name: "Phase1视觉检查.md",
      mimeType: "text/markdown",
      buffer: Buffer.from(
        "# Phase 1 视觉基线\n\n独立测试 API 的公开检查记录。\n\n- 四区层级\n- 焦点与状态\n- Mercury 兼容准备\n",
      ),
    });
    await upload.getByRole("button", { name: "上传并保存到 Workspace", exact: true }).click();
    await expect(upload.getByRole("status")).toContainText("未自动授权");
    await upload.getByRole("button", { name: "关闭上传到空间" }).click();
    routes.workspace = page.url().replace(base, "");
    await page.getByRole("button", { name: "Phase1视觉检查.md", exact: true }).click();
    await expect(page.locator(".hp-file-preview")).toBeVisible();
    routes.file = page.url().replace(base, "");
    await page.getByRole("button", { name: "关闭文件详情" }).click();
    await page
      .getByRole("navigation", { name: "主导航" })
      .getByRole("button", { name: "任务", exact: true })
      .click();
    await page.getByRole("button", { name: "新建任务", exact: true }).click();
    const task = page.getByRole("dialog", { name: "新建任务", exact: true });
    await task.getByLabel("工作名称").fill("Phase1基础视觉验收");
    await task.getByLabel("目标 / 提醒内容").fill("核对页面色阶、可读性与后续实验边界。");
    await task.getByLabel("执行时间").selectOption("once");
    await task.getByLabel("日期时间").fill("2027-01-01T09:00");
    await task.getByRole("button", { name: "创建工作", exact: true }).click();
    await expect(page.locator(".hp-task-inspector")).toBeVisible();
    routes.task = page.url().replace(base, "");
    await page.getByRole("button", { name: "关闭任务详情" }).click();
    await page
      .getByRole("navigation", { name: "任务分类" })
      .getByRole("button", { name: /等待或已计划/ })
      .click();
    routes.tasks = page.url().replace(base, "");
    await writeFile(resolve(evidence, "routes.json"), JSON.stringify(routes, null, 2) + "\n");
  } else {
    routes = JSON.parse(await readFile(resolve(evidence, "routes.json"), "utf8"));
    await page.getByLabel("用户名").fill(user);
    await page.getByLabel("密码", { exact: true }).fill(password);
    await page.getByRole("button", { name: "登录", exact: true }).click();
    await settled();
  }
  await page.goto(base + routes.ai);
  await settled();
  await expect(page.locator(".hp-run-detail")).toBeVisible();
  await page.locator(".hp-run-detail").click();
  await expect(page.locator(".hp-run-inspector")).toBeVisible();
  routes.run = page.url().replace(base, "");
  routes.new_ai = "/#/ai";
  await writeFile(resolve(evidence, "routes.json"), JSON.stringify(routes, null, 2) + "\n");
  for (const width of [1680, 1280, 780, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    for (const scene of ["ai", "new_ai", "artifact", "run", "workspace", "file", "tasks", "task"]) {
      await page.goto(base + routes[scene]);
      await page.reload();
      await settled();
      await expect(page.locator(".hp-shell")).toBeVisible();
      if (scene === "ai") await expect(page.locator(".hp-msg").first()).toBeVisible();

      if (scene === "artifact") {
        await expect(page.getByTitle("Artifact 预览")).toBeVisible();
        await expect(
          page.frameLocator('iframe[title="Artifact 预览"]').locator("body"),
        ).not.toBeEmpty();
      }
      if (scene === "file") await expect(page.locator(".hp-file-preview")).toBeVisible();
      if (scene === "run") await expect(page.locator(".hp-run-inspector")).toBeVisible();
      if (scene === "task") await expect(page.locator(".hp-task-inspector")).toBeVisible();
      if (["artifact", "run", "file", "task"].includes(scene))
        await expect(page.locator(".hp-inspector")).toHaveAttribute(
          width < 1280 ? "aria-modal" : "role",
          width < 1280 ? "true" : "complementary",
        );
      await shot(`${scene}-${width}`);
    }
  }
  await page.setViewportSize({ width: 1680, height: 1000 });
  await page.goto(base + routes.ai);
  await settled();
  await page.getByLabel("消息输入").fill("保留的视觉验证草稿");
  await page
    .getByRole("navigation", { name: "主导航" })
    .getByRole("button", { name: "空间", exact: true })
    .click();
  await page
    .getByRole("navigation", { name: "主导航" })
    .getByRole("button", { name: "AI", exact: true })
    .click();
  await expect(page.getByLabel("消息输入")).toHaveValue("保留的视觉验证草稿");
  await page.getByLabel("消息输入").focus();
  await page.keyboard.press("Tab");
  expect(await page.evaluate(() => getComputedStyle(document.activeElement).outlineStyle)).not.toBe(
    "none",
  );
  await shot("focus-draft");
  await page.getByLabel("消息输入").fill("");
  await page.getByRole("button", { name: "对话资料", exact: true }).click();
  await shot("resources-modal");
  await page.getByRole("button", { name: "关闭当前对话资料" }).click();
  if (phase === "after") {
    const rail = page
      .getByRole("navigation", { name: "主导航" })
      .getByRole("button", { name: "AI", exact: true });
    await rail.focus();
    expect(await rail.evaluate((el) => getComputedStyle(el).outlineColor)).toBe(
      "rgb(255, 217, 203)",
    );
    await shot("focus-rail");
  }
  expect(errors).toEqual([]);
  await writeFile(
    resolve(evidence, `${phase}-geometry.json`),
    JSON.stringify({ browser: await browser.version(), metrics, errors }, null, 2) + "\n",
  );
  console.log(
    `${phase}: ${metrics.length} real UI screenshots; geometry, draft and focus checks passed`,
  );
} catch (error) {
  await page.screenshot({ path: resolve(evidence, "failure.png") });
  console.error(await page.locator("body").innerText());
  throw error;
} finally {
  await browser.close();
}
