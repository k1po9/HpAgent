import { test, expect } from "@playwright/test";
const evidence = "test-results/evidence/run-diagnostics";
async function open(page: import("@playwright/test").Page, query = "") {
  await page.goto(
    `/e2e/run-diagnostics-harness.html?${query}#/ai?inspect=run:0199ed39-a674-7001-8000-000000000007&tab=advanced`,
  );
  await page.getByRole("tab", { name: "高级诊断", exact: true }).click();
  await expect(page.locator(".hp-trace-tree__name").first()).toBeVisible();
  await expect(page.locator("#inspector-title")).toHaveCSS("opacity", "1");
  await page.locator("#inspector-title").blur();
}
test("local diagnostic screenshots", async ({ page }) => {
  await open(page);
  await page.screenshot({
    path: `${evidence}/${process.env.DIAGNOSTICS_BASELINE ? "before" : "after"}-desktop.png`,
  });
  await page.locator(".hp-inspector--run").screenshot({
    path: `${evidence}/${process.env.DIAGNOSTICS_BASELINE ? "before" : "after"}-panel.png`,
  });
  await page.locator(".hp-quiet-rail").screenshot({ path: `${evidence}/after-trace.png` });
});

test("identity copy, independent disclosures, selection and collapse survive refresh and resizing", async ({
  page,
  context,
}) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await open(page);
  await expect(page.getByRole("heading", { name: "本会话 · 第 7 次执行" })).toBeVisible();
  await page.getByRole("button", { name: "复制完整执行标识" }).click();
  await expect(page.getByText("执行标识已复制")).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(
    "0199ed39-a674-7001-8000-000000000007",
  );
  await page.locator("summary").filter({ hasText: "预算与策略" }).click();
  await expect(page.getByText(/观察额度，不代表正在生效/)).toBeVisible();
  await expect(
    page.locator("summary").filter({ hasText: "原始数据" }).locator(".."),
  ).not.toHaveAttribute("open");
  await page.locator("summary").filter({ hasText: "预算与策略" }).click();
  await page.getByRole("button", { name: "折叠节点：查阅工作区资料", exact: true }).click();
  const selected = page.getByRole("button", { name: /交叉检查与归纳，已完成/ });
  await selected.click();
  await expect(page.getByRole("region", { name: "Trace 节点详情" })).toContainText("call-2");
  await page.getByRole("button", { name: "刷新执行轨迹", exact: true }).click();
  await expect(selected).toHaveAttribute("aria-pressed", "true");
  await expect(
    page.getByRole("button", { name: "展开节点：查阅工作区资料", exact: true }),
  ).toHaveAttribute("aria-expanded", "false");
  await page.getByRole("separator", { name: "调整执行详情宽度" }).focus();
  await page.keyboard.press("End");
  await expect(selected).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("button", { name: /查找已授权的周报，/ })).toHaveCount(0);
  await page.getByRole("button", { name: "展开节点：查阅工作区资料", exact: true }).click();
  await expect(page.getByRole("button", { name: /查找已授权的周报，/ })).toBeVisible();
  await page.locator("summary").filter({ hasText: "模型调用明细" }).click();
  await expect(page.locator(".hp-usage-calls article")).toHaveCount(3);
  await page.locator(".hp-usage-calls article").last().getByRole("button").click();
  await expect(page.getByRole("region", { name: "Trace 节点详情" })).toContainText("call-3");
  await page.locator("summary").filter({ hasText: "模型调用明细" }).click();
  await page.evaluate(() => {
    Object.defineProperty(navigator.clipboard, "writeText", {
      configurable: true,
      value: () => Promise.reject(new Error("denied")),
    });
  });
  await page.getByRole("button", { name: "复制完整执行标识" }).click();
  await expect(page.getByText("复制失败，请重试")).toBeVisible();
  await page.getByRole("button", { name: "折叠节点：整理本周资料", exact: true }).click();
  await page.getByRole("button", { name: "展开节点：整理本周资料", exact: true }).click();
  await expect(page.getByRole("button", { name: /组织回复与交付，已完成/ })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
});

test("default, wide, minimum, narrow and mobile remain readable", async ({ page }) => {
  await page.setViewportSize({ width: 1536, height: 1400 });
  await open(page);
  await page.locator(".hp-inspector--run").screenshot({ path: `${evidence}/after-panel.png` });
  const edge = page.getByRole("separator", { name: "调整执行详情宽度" });
  await edge.focus();
  await page.keyboard.press("End");
  await edge.blur();
  await page.screenshot({ path: `${evidence}/after-wide.png` });
  await page.locator(".hp-inspector--run").screenshot({ path: `${evidence}/after-wide-panel.png` });
  await page.locator(".hp-quiet-rail").screenshot({ path: `${evidence}/after-wide-trace.png` });
  await edge.focus();
  await page.keyboard.press("Home");
  await edge.blur();
  await page.screenshot({ path: `${evidence}/after-minimum.png` });
  for (const width of [1280, 1279, 768, 390]) {
    await page.setViewportSize({ width, height: 1100 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
      width,
    );
    const panel = page.locator("#run-panel-advanced");
    expect(await panel.evaluate((el) => el.scrollWidth)).toBeLessThanOrEqual(
      await panel.evaluate((el) => el.clientWidth),
    );
    if (width < 960) {
      for (const control of await page
        .locator(".hp-trace-tree__node:visible, .hp-trace-tree__expand:visible")
        .all())
        expect((await control.boundingBox())!.height).toBeGreaterThanOrEqual(44);
    }
    const duration = page.locator(".hp-trace-tree__duration").last();
    await duration.scrollIntoViewIfNeeded();
    const box = await duration.boundingBox();
    expect(box!.x + box!.width).toBeLessThanOrEqual(width);
  }
  await page.setViewportSize({ width: 390, height: 1400 });
  await page
    .locator(".hp-surface-body")
    .last()
    .evaluate((el) => (el.scrollTop = 0));
  await page.locator("#inspector-title").blur();
  await page.screenshot({ path: `${evidence}/after-mobile.png` });
});

test("only actual running paths animate and reduced motion settles immediately", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await open(page);
  expect(
    await page
      .locator(".hp-trace-tree")
      .evaluate((el) => el.getAnimations({ subtree: true }).length),
  ).toBe(0);
  await open(page, "running=1");
  expect(
    await page
      .locator(".hp-trace-tree")
      .evaluate((el) => el.getAnimations({ subtree: true }).length),
  ).toBeGreaterThan(0);
  await page.screenshot({ path: `${evidence}/after-running.png` });
  await page.emulateMedia({ reducedMotion: "reduce" });
  expect(
    await page
      .locator(".hp-trace-tree")
      .evaluate((el) => el.getAnimations({ subtree: true }).length),
  ).toBe(0);
  await open(page, "limited=1");
  await page.locator("summary").filter({ hasText: "预算与策略" }).click();
  await expect(page.getByText(/超出 2,480/)).toBeVisible();
  await page.screenshot({ path: `${evidence}/after-over-budget.png` });
});

test("missing usage and empty trace keep honest states and original detail container", async ({
  page,
}) => {
  await open(page, "missing=1");
  await expect(page.getByText("本次执行暂无用量记录。")).toBeVisible();
  await expect(page.locator(".hp-usage-bar")).toHaveCount(0);
  await page.goto(
    "/e2e/run-diagnostics-harness.html?empty=1#/ai?inspect=run:0199ed39-a674-7001-8000-000000000007",
  );
  await page.getByRole("tab", { name: "高级诊断", exact: true }).click();
  await expect(page.getByText("暂无可用诊断记录。")).toBeVisible();
  await expect(page.getByText("选择节点查看详情。")).toBeVisible();
  await expect(page.getByText(/idle|0 nodes|Trace Debug/)).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Model Input", exact: true })).toHaveCount(0);
  await page.screenshot({ path: `${evidence}/after-empty.png` });
});

test("long names, deep trees, multiple roots and errors keep their own identities", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 1100 });
  await open(page, "stress=1");
  await expect(page.getByText("执行异常 · provider_timeout")).toBeVisible();
  await expect(page.locator('.hp-trace-tree__item[data-root="true"]')).toHaveCount(2);
  const deep = page.getByRole("button", { name: /深层 10 ·/ });
  await deep.scrollIntoViewIfNeeded();
  await deep.click();
  await expect(page.getByRole("region", { name: "Trace 节点详情" })).toContainText("深层 10");
  const box = await deep.locator(".hp-trace-tree__duration").boundingBox();
  expect(box!.x + box!.width).toBeLessThanOrEqual(390);
  expect((await deep.locator(".hp-trace-tree__name").boundingBox())!.width).toBeGreaterThan(70);
  expect(
    await page.locator("#run-panel-advanced").evaluate((el) => el.scrollWidth <= el.clientWidth),
  ).toBe(true);
  await page.screenshot({ path: `${evidence}/after-deep-mobile.png` });
});
