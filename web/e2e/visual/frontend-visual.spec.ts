import { expect, test, type Page } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";

// These checks use the application and its real API. No Store injection or response fixtures.
const enabled = process.env.HPAGENT_VISUAL_ISOLATED === "1";
const evidence = resolve(
  process.cwd(),
  "../artifacts/product-acceptance/visual-2026-10-09/recheck",
);
async function screenshot(page: Page, name: string) {
  await mkdir(evidence, { recursive: true });
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: resolve(evidence, `${name}.png`) });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
    (page.viewportSize()?.width ?? 0) + 1,
  );
}

test("auth presentation, validation and real connection recovery", async ({ page }) => {
  test.skip(!enabled, "Set HPAGENT_VISUAL_ISOLATED=1 only for a separately provisioned test API.");
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "HpAgent 登录" })).toBeVisible();
  await screenshot(page, "auth-desktop");
  await page.getByRole("button", { name: "没有账号？注册" }).click();
  await page.getByLabel("用户名").fill("client-validation-only");
  await page.getByLabel("密码", { exact: true }).fill("password-validation");
  await page.getByLabel("确认密码").fill("different-password");
  await page.getByRole("button", { name: "注册", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveText("两次输入的密码不一致。");
  await page.setViewportSize({ width: 390, height: 844 });
  await screenshot(page, "register-mobile-validation");
  await page.route("**/api/v1/me", (route) => route.abort("connectionfailed"));
  await page.reload();
  await expect(page.getByRole("alert")).toHaveText("无法连接 HpAgent API");
  await screenshot(page, "connection-error-mobile");
  await page.unroute("**/api/v1/me");
  await page.getByRole("button", { name: "重试", exact: true }).click();
  await expect(page.getByRole("heading", { name: "HpAgent 登录" })).toBeVisible();
});

test("real records, Inspector breakpoints, navigation and drafts", async ({ page }) => {
  test.skip(
    !enabled || !process.env.HPAGENT_VISUAL_USER || !process.env.HPAGENT_VISUAL_PASSWORD,
    "Requires an isolated test account with existing public records; no data is seeded by this test.",
  );
  await page.goto("/");
  await page.getByLabel("用户名").fill(process.env.HPAGENT_VISUAL_USER!);
  await page.getByLabel("密码", { exact: true }).fill(process.env.HPAGENT_VISUAL_PASSWORD!);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.locator(".hp-shell")).toBeVisible();
  const nav = page.getByRole("navigation", { name: "主导航" });
  await page.locator(".hp-conv").last().click();
  await expect(page.locator(".hp-msg").first()).toBeVisible();
  await screenshot(page, "ai-desktop");
  await page.getByLabel("消息输入").fill("未发送的视觉回归草稿");
  const composer = await page.getByLabel("消息输入").elementHandle();
  await nav.getByRole("button", { name: "空间", exact: true }).click();
  await page
    .getByRole("button", {
      name: process.env.HPAGENT_VISUAL_FILE ?? "视觉检查记录.md",
      exact: true,
    })
    .click();
  await expect(page.locator(".hp-file-preview")).toBeVisible();
  await screenshot(page, "workspace-desktop");
  for (const width of [390, 959, 960, 1279, 1280, 1303, 1304, 1403, 1404, 1536]) {
    await page.setViewportSize({ width, height: width < 600 ? 844 : 1024 });
    const inspector = page.locator(".hp-inspector");
    if (width < 1280) await expect(inspector).toHaveAttribute("aria-modal", "true");
    else await expect(inspector).not.toHaveAttribute("aria-modal", "true");
    const rect = await inspector.boundingBox();
    expect(rect!.x).toBeGreaterThanOrEqual(0);
    expect(rect!.x + rect!.width).toBeLessThanOrEqual(width + 1);
    expect(await composer!.evaluate((node) => node.isConnected)).toBe(true);
  }
  await page.getByRole("button", { name: "关闭文件详情" }).click();
  await nav.getByRole("button", { name: "AI", exact: true }).click();
  await expect(page.getByLabel("消息输入")).toHaveValue("未发送的视觉回归草稿");
  await page.getByLabel("消息输入").fill("");
  await page.locator(".hp-artifact-message-card").last().click();
  await expect(page.getByTitle("Artifact 预览")).toBeVisible();
  expect(await page.getByTitle("Artifact 预览").getAttribute("sandbox")).not.toContain(
    "allow-same-origin",
  );
  await screenshot(page, "artifact-desktop");
  await page.setViewportSize({ width: 390, height: 844 });
  await screenshot(page, "artifact-mobile");
  await page.getByRole("button", { name: "关闭HTML 成果" }).click();
  await nav.getByRole("button", { name: "任务", exact: true }).click();
  await page.getByRole("button", { name: "打开侧栏" }).click();
  await page.getByRole("button", { name: /等待或已计划/ }).click();
  if (await page.getByRole("button", { name: "关闭上下文导航" }).isVisible())
    await page.getByRole("button", { name: "关闭上下文导航" }).click();
  await page
    .getByRole("button", {
      name: process.env.HPAGENT_VISUAL_TASK ?? "公开资料检查提醒",
      exact: true,
    })
    .click();
  await expect(page.locator(".hp-task-inspector")).toBeVisible();
  await screenshot(page, "task-mobile");
});
