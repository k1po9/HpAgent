import { test, expect } from "@playwright/test";
const evidence = "test-results/evidence/run-overview";
test.beforeEach(async ({ page }) => {
  await page.route("**/api/v1/**", (route) =>
    route.fulfill({
      json: {
        items: [],
        conversations: [],
        works: [],
        candidates: [],
        files: [],
        approvals: [],
        visibility: "none",
        roots: [],
      },
    }),
  );
});
async function open(page: import("@playwright/test").Page, query = "") {
  await page.goto(`/e2e/run-overview-harness.html?${query}#/ai?inspect=run:overview-run`);
  await expect(page.getByRole("region", { name: "执行概览", exact: true })).toBeVisible();
  await expect(page.locator("#inspector-title")).toHaveCSS("opacity", "1");
  await page.locator("#inspector-title").blur();
}
test("success preserves raw reply, truthful cross-day timing and approval navigation", async ({
  page,
}) => {
  await open(page);
  await expect(page.getByRole("img", { name: "执行成功" })).toBeVisible();
  await expect(page.getByText("本次执行已完成", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /回到所属对话/ })).toHaveCount(0);
  await expect(page.getByText("阶段未提供")).toHaveCount(0);
  await expect(page.locator(".hp-run-result")).toContainText("**本周重点**");
  await expect(page.locator(".hp-run-result strong")).toHaveCount(0);
  await expect(page.getByText("等待 3秒", { exact: true })).toBeVisible();
  await expect(page.getByText("执行 1分 29秒", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "查看操作审批" })).toHaveCSS(
    "background-color",
    "rgb(243, 246, 248)",
  );
  await page.screenshot({ path: `${evidence}/after-desktop.png` });
  const edge = page.getByRole("separator", { name: "调整执行详情宽度" });
  await edge.focus();
  await page.keyboard.press("End");
  await edge.blur();
  await page.screenshot({ path: `${evidence}/after-wide.png` });
  await edge.focus();
  await page.keyboard.press("Home");
  await edge.blur();
  await page.screenshot({ path: `${evidence}/after-min.png` });
  await page.getByRole("button", { name: "查看操作审批" }).click();
  await expect(page.getByRole("tab", { name: "使用资料与输出", exact: true })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await page.getByRole("tab", { name: "高级诊断", exact: true }).click();
  await expect(page.locator("#run-panel-advanced")).toBeVisible();
});
test("active, waiting, failed, cancelled and approval facts", async ({ page }) => {
  for (const query of [
    "status=running&live=1",
    "status=queued&live=1",
    "status=failed&live=1",
    "status=cancelled",
    "status=running&live=1&approval=pending",
    "approval=none",
    "missing=1",
  ]) {
    await open(page, query);
    if (query.includes("status=running")) {
      await expect(page.getByText("当前活动", { exact: true })).toBeVisible();
      await expect(page.getByTestId("stop-run")).toBeVisible();
    }
    if (query.includes("queued"))
      await expect(
        page.locator(".hp-run-overview").getByText("等待执行资源", { exact: true }),
      ).toBeVisible();
    if (query.includes("failed"))
      await expect(
        page.locator(".hp-run-overview").getByTestId("run-failure-message"),
      ).toBeVisible();
    if (query.includes("approval=pending"))
      await expect(page.getByText("有待审批操作", { exact: true })).toBeVisible();
    if (query === "approval=none")
      await expect(page.getByText("已核实，无待审批", { exact: true })).toBeVisible();
    if (query === "missing=1") {
      await expect(page.getByText("时间未提供", { exact: true })).toBeVisible();
      await expect(page.locator(".hp-run-duration")).toHaveCount(0);
    }
    await page.screenshot({ path: `${evidence}/${query.replaceAll(/[^a-z0-9]/g, "-")}.png` });
  }
});
test("long raw text scrolls in its reading mat across narrow widths", async ({ page }) => {
  await open(page, "long=1");
  const result = page.locator(".hp-run-result");
  expect(await result.evaluate((el) => el.scrollHeight)).toBeGreaterThan(1000);
  expect(await result.evaluate((el) => el.clientHeight)).toBeLessThan(
    await result.evaluate((el) => el.scrollHeight),
  );
  await result.focus();
  await page.keyboard.press("End");
  await expect.poll(() => result.evaluate((el) => el.scrollTop)).toBeGreaterThan(0);
  for (const width of [1280, 1279, 768, 390]) {
    await page.setViewportSize({ width, height: 844 });
    await result.scrollIntoViewIfNeeded();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
      width,
    );
    const overview = page.locator(".hp-run-overview");
    expect(await overview.evaluate((el) => el.scrollWidth)).toBeLessThanOrEqual(
      await overview.evaluate((el) => el.clientWidth),
    );
  }
  await page.getByRole("button", { name: "查看操作审批" }).scrollIntoViewIfNeeded();
  await expect(page.getByRole("button", { name: "查看操作审批" })).toBeVisible();
  await page.screenshot({ path: `${evidence}/after-long-mobile-end.png` });
  await open(page);
  await page.screenshot({ path: `${evidence}/after-mobile.png` });
});
test("normal motion settles success and expires stale current activity", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await open(page);
  expect(
    await page
      .locator(".hp-run-completion")
      .evaluate((el) => el.getAnimations({ subtree: true }).length),
  ).toBe(0);
  await page.clock.install();
  await open(page, "status=running&live=1");
  await expect(page.getByText("当前活动", { exact: true })).toBeVisible();
  await page.clock.fastForward(31_000);
  await expect(page.getByText("当前活动", { exact: true })).toHaveCount(0);
  await expect(page.locator(".hp-run-overview-state")).toContainText("运行中");
  await expect(page.locator(".hp-run-overview").getByTestId("stop-run")).toBeEnabled();
  await page.screenshot({ path: `${evidence}/after-activity-expired.png` });
});

test("approved card and proportional timeline keep exact positions without label collisions", async ({
  page,
}) => {
  await open(page, "approved=1");
  await expect(page.locator(".hp-run-time-chart li")).toHaveCount(2);
  await expect(page.getByText("创建 / 开始", { exact: true })).toBeVisible();
  await expect(page.locator(".hp-run-overview")).toHaveCSS(
    "background-color",
    "rgb(255, 255, 255)",
  );
  await expect(page.locator(".hp-run-completion")).toHaveCSS(
    "background-color",
    "rgb(225, 242, 235)",
  );
  await page.screenshot({ path: `${evidence}/approved-desktop.png` });
  await page
    .locator(".hp-inspector--run")
    .screenshot({ path: `${evidence}/approved-default-panel.png` });
  const edge = page.getByRole("separator", { name: "调整执行详情宽度" });
  await edge.focus();
  await page.keyboard.press("End");
  await edge.blur();
  await page.screenshot({ path: `${evidence}/approved-wide.png` });
  await page.locator(".hp-inspector--run").screenshot({ path: `${evidence}/approved-panel.png` });
  await page.setViewportSize({ width: 390, height: 1000 });
  await page.locator("#inspector-title").blur();
  await page.screenshot({ path: `${evidence}/approved-mobile.png` });
  for (const query of ["wait=7&exec=21", "wait=1&exec=999", "wait=999&exec=1"]) {
    await open(page, query);
    const points = page.locator(".hp-run-time-chart li");
    const ratio = query === "wait=7&exec=21" ? 25 : query.includes("wait=1&") ? 0.1 : 99.9;
    await expect(points.nth(1)).toHaveAttribute("data-position", String(ratio));
    const markers = page.locator(".hp-run-time-point");
    const rects = await markers.evaluateAll((els) => els.map((el) => el.getBoundingClientRect().x));
    expect(((rects[1]! - rects[0]!) / (rects[2]! - rects[0]!)) * 100).toBeCloseTo(ratio, 1);
    const boxes = await points.evaluateAll((els) =>
      els.map((el) => {
        const r = el.getBoundingClientRect();
        return { x: r.x, y: r.y, right: r.right, bottom: r.bottom };
      }),
    );
    for (let i = 0; i < boxes.length; i++)
      for (let j = i + 1; j < boxes.length; j++) {
        const a = boxes[i]!,
          b = boxes[j]!;
        expect(a.right <= b.x || b.right <= a.x || a.bottom <= b.y || b.bottom <= a.y).toBe(true);
      }
    await page.screenshot({ path: `${evidence}/proportional-${ratio}.png` });
  }
});
