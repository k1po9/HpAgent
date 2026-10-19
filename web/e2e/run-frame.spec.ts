import { test, expect } from "@playwright/test";
const evidence = "test-results/run-frame";
test.beforeEach(async ({ page }) => {
  await page.route("**/api/v1/**", (route) =>
    route.fulfill({
      json: {
        items: [],
        conversations: [],
        works: [],
        unread_count: 0,
        candidates: [],
        files: [],
        approvals: [],
        next: null,
        visibility: "none",
        roots: [],
      },
    }),
  );
});
test("continuous resize preserves reading, draft, tabs and focus", async ({ page }) => {
  await page.goto("/e2e/run-frame-harness.html#/ai?inspect=run:frame-run");
  const frame = page.locator(".hp-inspector--run");
  await expect(page.getByRole("tab", { name: "概览", exact: true })).toBeVisible();
  await expect(page.locator("#inspector-title")).toHaveCSS("opacity", "1");
  await expect(page.locator("#inspector-run-title")).toBeVisible();
  await expect(page.getByRole("button", { name: "扩大阅读" })).toHaveCount(0);
  await page.screenshot({ path: `${evidence}/after-desktop.png` });
  const draft = page.getByRole("textbox").last();
  await draft.fill("保留输入草稿，不发送");
  const input = await draft.elementHandle();
  const content = await page.locator("#run-panel-overview").elementHandle();
  const edge = page.getByRole("separator", { name: "调整执行详情宽度" });
  await edge.focus();
  await page.keyboard.press("End");
  await expect(edge).toHaveAttribute("aria-valuenow", "828");
  await expect.poll(async () => (await frame.boundingBox())!.width).toBe(828);
  expect((await page.locator(".hp-main-canvas").boundingBox())!.width).toBeGreaterThanOrEqual(600);
  await page.screenshot({ path: `${evidence}/after-wide.png` });
  await page.keyboard.press("Home");
  await expect.poll(async () => (await frame.boundingBox())!.width).toBe(360);
  await page.screenshot({ path: `${evidence}/after-min.png` });
  const rect = (await edge.boundingBox())!;
  await page.mouse.move(rect.x + 5, rect.y + 180);
  await page.mouse.down();
  await page.mouse.move(rect.x - 230, rect.y + 180, { steps: 12 });
  await page.mouse.up();
  await expect.poll(async () => (await frame.boundingBox())!.width).toBe(595);
  expect(await input!.evaluate((node) => node.isConnected)).toBe(true);
  expect(await content!.evaluate((node) => node.isConnected)).toBe(true);
  await expect(draft).toHaveValue("保留输入草稿，不发送");
  await expect(page.locator("body")).not.toHaveClass(/hp-run-resizing/);
  await page.getByRole("button", { name: "刷新执行状态", exact: true }).click();
  await expect(page.locator("#run-refresh-hint")).toHaveText("已刷新");
  const resources = page.getByRole("tab", { name: "使用资料与输出", exact: true });
  await resources.click();
  await expect(page.locator("#run-panel-resources")).toBeVisible();
  await resources.focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "高级诊断", exact: true })).toBeFocused();
  await expect(
    page.getByRole("button", { name: "刷新执行状态与执行轨迹", exact: true }),
  ).toBeVisible();
  await page.keyboard.press("Home");
  for (const width of [1280, 1279, 960, 768, 390]) {
    await page.setViewportSize({ width, height: 844 });
    if (width < 1280) {
      await expect(frame).toHaveAttribute("aria-modal", "true");
      await expect(edge).toHaveCount(0);
    } else await expect(frame).not.toHaveAttribute("aria-modal", "true");
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
      width,
    );
    const bounds = (await frame.boundingBox())!;
    expect(bounds.x).toBeGreaterThanOrEqual(0);
    expect(bounds.x + bounds.width).toBeLessThanOrEqual(width + 1);
    await expect(page.locator("#inspector-run-title")).toBeVisible();
    await expect(page.getByRole("button", { name: "关闭执行详情" })).toBeVisible();
    await page.screenshot({ path: `${evidence}/after-${width}.png` });
  }
  await page.keyboard.press("Escape");
  await expect(frame).toHaveCount(0);
  await expect(draft).toHaveValue("保留输入草稿，不发送");
  await expect(page.locator("#canvas-title")).toBeFocused();
});
test("refresh failure keeps feedback and retry available", async ({ page }) => {
  await page.goto("/e2e/run-frame-harness.html?failure=1#/ai?inspect=run:frame-run");
  const refresh = page.getByRole("button", { name: "刷新执行状态", exact: true });
  await expect(refresh).toBeEnabled();
  await refresh.click();
  await expect(refresh).toBeEnabled();
  await refresh.focus();
  await expect(page.locator("#run-refresh-hint")).toHaveText("执行状态刷新失败，请重试");
  await page.screenshot({ path: `${evidence}/refresh-failure.png` });
});

test("normal Mercury arrival and responsive resize cancellation", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.goto("/e2e/run-frame-harness.html?arrival=1#/ai");
  await page.getByRole("button", { name: "查看示例执行" }).click();
  await expect(page.getByRole("tab", { name: "概览", exact: true })).toBeVisible();
  await expect(page.locator(".hp-capture-dual")).toHaveAttribute("data-handoff", "settled");
  await page.locator("#inspector-title").blur();
  await page.screenshot({ path: `${evidence}/after-motion-desktop.png` });
  const edge = page.getByRole("separator", { name: "调整执行详情宽度" });
  const rect = (await edge.boundingBox())!;
  await page.mouse.move(rect.x + 4, rect.y + 160);
  await page.mouse.down();
  await page.mouse.move(rect.x - 240, rect.y + 160, { steps: 30 });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.mouse.up();
  await expect(page.locator("body")).not.toHaveClass(/hp-run-resizing/);
  await page.locator("#inspector-title").blur();
  await page.screenshot({ path: `${evidence}/after-motion-mobile.png` });
  await page.keyboard.press("Escape");
  await expect(page.locator(".hp-inspector--run")).toHaveCount(0);
});

test("nested Surface Escape closes only the foreground and restores focus", async ({ page }) => {
  await page.goto("/e2e/ui7-surface-harness.html");
  await page.getByRole("button", { name: "打开父层", exact: true }).click();
  await page.getByRole("button", { name: "打开子操作", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "子操作", exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "原生焦点回归", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "打开子操作", exact: true })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "打开父层", exact: true })).toBeFocused();
});
