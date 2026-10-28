import { workFixture } from "../src/components/tasks/taskFixtures";
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
  const overview = page.getByRole("tab", { name: "概览", exact: true });
  await expect(overview).toBeFocused();
  await expect(overview).toHaveCSS("outline-style", "solid");
  await expect(overview).toHaveCSS("outline-width", "2px");
  await expect(overview).toHaveCSS("outline-offset", "-3px");
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
    await overview.focus();
    await expect(overview).toHaveCSS("outline-offset", "-3px");
    const tabBounds = (await overview.boundingBox())!;
    expect(tabBounds.width).toBeGreaterThan(6);
    expect(tabBounds.height).toBeGreaterThan(6);
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

test("controls keep touch targets at responsive boundaries and use measured Shell width", async ({
  page,
}) => {
  await page.goto("/e2e/run-frame-harness.html#/ai?inspect=run:frame-run");
  await page.getByRole("tab", { name: "使用资料与输出", exact: true }).click();
  for (const width of [320, 390, 599, 600, 959, 960, 1279, 1280]) {
    await page.setViewportSize({ width, height: 620 });
    const buttons = page.locator(".hp-inspector button:visible");
    for (const button of await buttons.all()) {
      const bounds = (await button.boundingBox())!;
      expect(bounds.height).toBeGreaterThanOrEqual(width < 960 ? 44 : 36);
      expect(bounds.x).toBeGreaterThanOrEqual(0);
      expect(bounds.x + bounds.width).toBeLessThanOrEqual(width + 1);
    }
  }
  await page.setViewportSize({ width: 1536, height: 1000 });
  await page.locator(".hp-shell").evaluate((node) => {
    node.style.width = "1400px";
  });
  const edge = page.getByRole("separator", { name: "调整执行详情宽度" });
  await expect(edge).toHaveAttribute("aria-valuemax", "692");
  await edge.focus();
  await page.keyboard.press("End");
  await expect
    .poll(async () => (await page.locator(".hp-inspector--run").boundingBox())!.width)
    .toBe(692);
  expect((await page.locator(".hp-main-canvas").boundingBox())!.width).toBeGreaterThanOrEqual(600);
});

test("project menu stays in the nested native Surface and Escape returns focus one layer at a time", async ({
  page,
}) => {
  await page.goto("/e2e/ui7-surface-harness.html?menus=1");
  await page.getByRole("button", { name: "页面菜单", exact: true }).click();
  await expect(page.getByRole("menuitem", { name: "页面动作" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "页面菜单", exact: true })).toBeFocused();
  await page.getByRole("button", { name: "打开父层", exact: true }).click();
  await page.getByRole("button", { name: "打开子操作", exact: true }).click();
  await page.getByRole("textbox", { name: "子操作草稿" }).fill("保留草稿");
  await page.getByRole("button", { name: "子层菜单", exact: true }).focus();
  await page.keyboard.press("ArrowDown");
  const item = page.getByRole("menuitem", { name: "菜单动作" });
  await expect(item).toBeFocused();
  expect(await item.evaluate((node) => Boolean(node.closest("dialog[open]:not([inert])")))).toBe(
    true,
  );
  await page.keyboard.press("Escape");
  await expect(item).toHaveCount(0);
  await expect(page.getByRole("button", { name: "子层菜单", exact: true })).toBeFocused();
  await expect(page.getByRole("textbox", { name: "子操作草稿" })).toHaveValue("保留草稿");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "打开子操作", exact: true })).toBeFocused();
});

test("workspace table responds to the canvas beside Inspector and keeps long file actions reachable", async ({
  page,
}) => {
  const name = "很长的文件名称".repeat(20) + ".md";
  await page.route("**/api/v1/workspace", (route) =>
    route.fulfill({
      json: {
        workspace_id: "isolated-ui",
        root_id: "root",
        nodes: [
          {
            node_id: "root",
            parent_id: null,
            kind: "directory",
            name: "",
            file_id: null,
            source: null,
          },
          {
            node_id: "long-file",
            parent_id: "root",
            kind: "file",
            name,
            file_id: "file",
            source: { purpose: "input", size_bytes: 200 },
          },
        ],
      },
    }),
  );
  await page.goto("/e2e/run-frame-harness.html#/workspace?inspect=run:frame-run");
  const table = page.getByRole("table", { name: "空间文件" });
  await expect(table).toBeVisible();
  const edge = page.getByRole("separator", { name: "调整执行详情宽度" });
  await edge.focus();
  await page.keyboard.press("End");
  await expect(table.getByRole("columnheader", { name: "类型", exact: true })).toBeHidden();
  expect(await table.evaluate((node) => node.scrollWidth <= node.clientWidth + 1)).toBe(true);
  const details = page.getByRole("button", { name: `查看${name}详情`, exact: true });
  const bounds = (await details.boundingBox())!;
  const canvas = (await page.locator(".hp-main-canvas").boundingBox())!;
  expect(bounds.x + bounds.width).toBeLessThanOrEqual(canvas.x + canvas.width);
  await edge.focus();
  await page.keyboard.press("Home");
  await expect(table.getByRole("columnheader", { name: "类型", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "关闭执行详情", exact: true }).click();
  for (const width of [320, 390, 599, 600, 959, 960]) {
    await page.setViewportSize({ width, height: 480 });
    await expect(details).toBeVisible();
    expect(await table.evaluate((node) => node.scrollWidth <= node.clientWidth + 1)).toBe(true);
    expect(
      await page
        .locator(".hp-workspace-screen")
        .evaluate((node) => node.scrollWidth <= node.clientWidth + 1),
    ).toBe(true);
  }
});

test("task menu preserves the permanent trigger across object navigation and local dialogs", async ({
  page,
}) => {
  const work = workFixture();
  await page.route("**/api/v1/works**", (route) => {
    const path = new URL(route.request().url()).pathname;
    return route.fulfill({
      json:
        path === "/api/v1/works"
          ? { items: [work], next_before: null }
          : path === "/api/v1/works/w1"
            ? { work }
            : { items: [], next: null },
    });
  });
  await page.goto("/e2e/run-frame-harness.html#/tasks");
  const trigger = page.getByRole("button", { name: "更多", exact: true });
  await trigger.click();
  await page.getByRole("menuitem", { name: "查看详情", exact: true }).click();
  await expect(page.getByRole("complementary", { name: "任务详情", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "关闭任务详情", exact: true }).click();
  await expect(trigger).toBeFocused();
  await trigger.click();
  await page.getByRole("menuitem", { name: "停止任务", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "停止任务", exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(trigger).toBeFocused();
});

test("budget fields expose scoped validation and keyboard focus inside the Surface", async ({
  page,
}) => {
  const work = workFixture();
  await page.route("**/api/v1/works**", (route) => {
    const path = new URL(route.request().url()).pathname;
    return route.fulfill({
      json:
        path === "/api/v1/works"
          ? { items: [work], next_before: null }
          : path === "/api/v1/works/w1"
            ? { work }
            : { items: [], next: null },
    });
  });
  await page.goto("/e2e/run-frame-harness.html#/tasks");
  const trigger = page.getByRole("button", { name: "更多", exact: true });
  await trigger.click();
  await page.getByRole("menuitem", { name: "提高预算", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "提高任务预算" });
  const tokens = dialog.getByRole("spinbutton", { name: /模型 Token 上限/ });
  const tools = dialog.getByRole("spinbutton", { name: /tool_calls/ });
  await tokens.fill("9007199254740992");
  await tools.fill("20");
  await dialog.getByRole("button", { name: "确认提高预算" }).click();
  await expect(tokens).toHaveAttribute("aria-invalid", "true");
  await expect(tokens).toHaveAccessibleDescription(/请输入严格高于旧值的整数限额/);
  await expect(tools).not.toHaveAttribute("aria-invalid", "true");
  await expect(tools).toHaveAccessibleDescription("新值 20 · 增加 10");
  for (const width of [1536, 390]) {
    await page.setViewportSize({ width, height: 844 });
    await tools.focus();
    await page.keyboard.press("Shift+Tab");
    await expect(tokens).toBeFocused();
    await expect(tokens).toHaveCSS("outline-style", "solid");
    await expect(tokens).toHaveCSS("outline-width", "2px");
    await expect(tokens).toHaveCSS("outline-offset", "3px");
    expect(
      await tokens.evaluate((node) => {
        const rect = node.getBoundingClientRect();
        const parent = node.closest(".hp-surface-body")!.getBoundingClientRect();
        return rect.left - 5 >= parent.left && rect.right + 5 <= parent.right;
      }),
    ).toBe(true);
  }
  await tokens.fill("2000");
  await expect(tokens).not.toHaveAttribute("aria-invalid", "true");
  await expect(tokens).toHaveAccessibleDescription("新值 2000 · 增加 1000");
  await page.keyboard.press("Escape");
  await expect(trigger).toBeFocused();
});
