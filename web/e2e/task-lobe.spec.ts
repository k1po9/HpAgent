import { expect, test, type Locator, type Page } from "@playwright/test";
import { installTaskLobeFixture } from "./task-lobe-fixture";

async function choose(page: Page, owner: Locator, label: string, name: string) {
  await owner.getByLabel(label, { exact: true }).click();
  await page.getByRole("option", { name, exact: true }).click();
}
async function openTask(page: Page) {
  await page.goto("/#/tasks?bucket=waiting");
  await page.getByRole("button", { name: "任务交互验收", exact: true }).click();
  return page.locator(".hp-task-inspector");
}

test("select changes mark drafts dirty; nested Escape preserves the editor and restores focus", async ({
  page,
}) => {
  const state = await installTaskLobeFixture(page);
  const inspector = await openTask(page);
  await inspector.getByRole("button", { name: "修改要求", exact: true }).click();
  const editor = page.getByRole("dialog", { name: "修改任务要求", exact: true });
  const strategy = editor.getByLabel("推理模式", { exact: true });
  await strategy.click();
  await expect(page.getByRole("listbox")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(editor).toBeVisible();
  await expect(strategy).toBeFocused();
  await choose(page, editor, "推理模式", "计划后执行");
  await editor.getByRole("button", { name: "关闭修改任务要求", exact: true }).click();
  const abandon = page.getByRole("dialog", { name: "放弃未提交的任务草稿？", exact: true });
  await expect(abandon).toBeVisible();
  await abandon.getByRole("button", { name: "继续编辑", exact: true }).click();
  await expect(editor.getByLabel("推理模式", { exact: true })).toHaveText("计划后执行");
  expect(state.writes).toHaveLength(0);
  await choose(page, editor, "执行时间", "指定时间");
  await editor.getByLabel("日期时间", { exact: true }).fill("2027-02-01T13:00");
  await editor.getByLabel("修订原因", { exact: true }).fill("用户修订计划");
  await editor.getByRole("button", { name: "提交修订", exact: true }).click();
  await expect(editor).not.toBeVisible();
  expect(state.writes).toHaveLength(1);
  expect(state.writes[0]).toMatchObject({
    match: '"work-lobe-task-v1"',
    body: {
      requirement: {
        spec: { reasoning_mode: "plan_and_execute" },
        timing: { due_at: "2027-02-01T05:00:00.000Z" },
      },
    },
  });
});

test("visited resource drafts survive tabs, expand/restore, and responsive mode changes", async ({
  page,
}) => {
  const state = await installTaskLobeFixture(page);
  const inspector = await openTask(page);
  expect(state.reads.filter((path) => path.endsWith("/resources"))).toHaveLength(0);
  await page.getByRole("tab", { name: "使用资料", exact: true }).click();
  await inspector.getByText("高级写入权限", { exact: true }).click();
  const permission = inspector.getByRole("checkbox", { name: "创建子项", exact: true });
  await permission.check();
  const identity = await permission.elementHandle();
  await page.getByRole("tab", { name: "概览", exact: true }).click();
  const guard = page.getByRole("dialog", { name: "放弃未提交的权限编辑？", exact: true });
  await expect(guard).toBeVisible();
  await guard.getByRole("button", { name: "继续编辑", exact: true }).click();
  await expect(permission).toBeChecked();
  await page.getByRole("button", { name: "扩大阅读", exact: true }).click();
  await expect(page.getByRole("button", { name: "恢复宽度", exact: true })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(permission).toBeChecked();
  expect(await permission.evaluate((node, original) => node === original, identity)).toBe(true);
  expect(state.writes).toHaveLength(0);
  await page.getByRole("button", { name: "恢复宽度", exact: true }).click();
  await inspector.getByRole("button", { name: "确认授予所选权限", exact: true }).click();
  await expect(inspector.getByText("授权已生效，下一轮可用。", { exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "概览", exact: true }).click();
  await page.getByRole("tab", { name: "使用资料", exact: true }).click();
  // Successful grant clears its draft; a visited panel retains that settled state.
  await expect(permission).not.toBeChecked();
  expect(await permission.evaluate((node, original) => node === original, identity)).toBe(true);
  expect(state.writes[0]).toMatchObject({
    path: "/api/v1/works/lobe-task/resources",
    body: {
      node_id: "notes",
      operations: ["list_metadata", "read_content", "create_child"],
      recursive: false,
    },
  });
});

test("menu opens stop confirmation without closing its Surface; cancel restores its trigger", async ({
  page,
}) => {
  const state = await installTaskLobeFixture(page);
  const inspector = await openTask(page);
  const more = inspector.getByRole("button", { name: "更多操作", exact: true });
  await more.click();
  await expect(page.getByRole("menu")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(more).toBeFocused();
  await expect(inspector).toBeVisible();
  await more.click();
  await page.getByRole("menuitem", { name: "停止任务", exact: true }).click();
  const confirm = page.getByRole("dialog", { name: "停止任务", exact: true });
  await expect(confirm).toBeVisible();
  expect(state.writes).toHaveLength(0);
  await page.keyboard.press("Escape");
  await expect(confirm).not.toBeVisible();
  await expect(more).toBeFocused();
  await more.click();
  await page.getByRole("menuitem", { name: "停止任务", exact: true }).click();
  let release!: () => void;
  state.pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  const stop = confirm.getByRole("button", { name: "确认停止任务", exact: true });
  await stop.click();
  await expect(stop).toBeDisabled();
  release();
  await expect(confirm).not.toBeVisible();
  await expect(inspector.getByText("已停止", { exact: true })).toBeVisible();
  expect(state.writes).toHaveLength(1);
});

test("budget conflict retains edited dimension; submit uses reviewed baseline only", async ({
  page,
}) => {
  const state = await installTaskLobeFixture(page);
  const inspector = await openTask(page);
  await inspector.getByRole("button", { name: "提高预算", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "提高任务预算", exact: true });
  const input = dialog.getByRole("textbox", { name: /模型 Token 上限/ });
  await input.fill("2000");
  state.failBudget = true;
  await dialog.getByRole("button", { name: "确认提高预算", exact: true }).click();
  await expect(dialog.getByRole("alert").last()).toBeVisible();
  await expect(input).toHaveValue(/^2,?000$/);
  await dialog.getByRole("button", { name: "已审阅新预算，采用新基线", exact: true }).click();
  await dialog.getByRole("button", { name: "确认提高预算", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  expect(state.writes).toHaveLength(2);
  expect(state.writes[1]).toMatchObject({
    match: '"work-lobe-task-v2"',
    body: { limits: { model_total_tokens: 2000 } },
  });
});

test("unknown pause response disables mutations and retry replays the original intent", async ({
  page,
}) => {
  const state = await installTaskLobeFixture(page);
  const inspector = await openTask(page);
  state.dropNextWrite = true;
  await inspector.getByRole("button", { name: "暂停任务", exact: true }).click();
  await expect(inspector.getByRole("button", { name: "重试原操作", exact: true })).toBeVisible();
  await expect(inspector.getByRole("button", { name: "暂停任务", exact: true })).toBeDisabled();
  await expect(inspector.getByRole("button", { name: "修改要求", exact: true })).toBeDisabled();
  await inspector.getByRole("button", { name: "重试原操作", exact: true }).click();
  await expect(inspector.getByRole("button", { name: "恢复任务", exact: true })).toBeVisible();
  expect(state.writes).toHaveLength(2);
  expect(state.writes[0]!.key).toBeTruthy();
  expect(state.writes[1]).toEqual(state.writes[0]);
  expect(state.work.row_version).toBe(2);
});

test("sidebar selection and keyboard tabs retain their route and active state", async ({
  page,
}) => {
  await installTaskLobeFixture(page);
  const inspector = await openTask(page);
  const navigation = page.getByRole("navigation", { name: "任务分类", exact: true });
  await expect(navigation.getByRole("button", { name: /等待或已计划/ })).toHaveAttribute(
    "aria-current",
    "true",
  );
  await choose(page, page.locator(".hp-task-toolbar"), "任务类型", "通用任务");
  await expect(page.locator(".hp-task-toolbar").getByLabel("任务类型", { exact: true })).toHaveText(
    "通用任务",
  );
  await page.getByRole("tab", { name: "概览", exact: true }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "成果与执行", exact: true })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(inspector.getByRole("tabpanel", { name: "成果与执行", exact: true })).toBeVisible();
  await navigation.getByRole("button", { name: /已结束/ }).click();
  await expect(navigation.getByRole("button", { name: /已结束/ })).toHaveAttribute(
    "aria-current",
    "true",
  );
  await expect(page.getByRole("tab", { name: "成果与执行", exact: true })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(inspector).toBeVisible();
});

test("board selection follows the route through categories, menus, and browser history", async ({
  page,
}) => {
  const state = await installTaskLobeFixture(page);
  await page.goto("/#/tasks?bucket=waiting");
  const card = page.getByRole("article", { name: "任务交互验收", exact: true });
  const title = card.getByRole("button", { name: "任务交互验收", exact: true });
  await title.click();
  await expect(card).toHaveAttribute("data-selected", "true");
  await expect(title).toHaveAttribute("aria-expanded", "true");
  const nav = page.getByRole("navigation", { name: "任务分类", exact: true });
  const ended = nav.getByRole("button", { name: /已结束/ });
  await ended.focus();
  await page.keyboard.press("Enter");
  await expect(ended).toHaveAttribute("aria-current", "true");
  await expect(ended).toHaveCSS("background-color", "rgb(253, 237, 232)");
  await expect(page.locator('.hp-task-lane[data-task-lane="ended"]')).toHaveClass(
    /hp-task-lane--current/,
  );
  await expect(card).toHaveAttribute("data-selected", "true");
  const more = card.getByRole("button", { name: "更多", exact: true });
  await more.click();
  await expect(more).toHaveAttribute("aria-expanded", "true");
  await page.getByRole("menuitem", { name: "停止任务", exact: true }).click();
  const stop = page.getByRole("dialog", { name: "停止任务", exact: true });
  await expect(stop).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(stop).not.toBeVisible();
  await expect(more).toBeFocused();
  await expect(card).toHaveAttribute("data-selected", "true");
  expect(state.writes).toHaveLength(0);
  await page.goBack();
  await expect(card).toHaveAttribute("data-selected", "false");
  await expect(title).toHaveAttribute("aria-expanded", "false");
});

test("board primary action retains its node, focus, and pending state across a status change", async ({
  page,
}) => {
  const state = await installTaskLobeFixture(page);
  state.work = { ...state.work, status: "paused" };
  await page.goto("/#/tasks?bucket=waiting");
  const card = page.getByRole("article", { name: "任务交互验收", exact: true });
  const resume = card.getByRole("button", { name: "恢复任务", exact: true });
  const identity = await resume.elementHandle();
  let release!: () => void;
  state.pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  await resume.click();
  await expect(resume).toBeDisabled();
  await expect(card.getByRole("button", { name: "更多", exact: true })).toBeDisabled();
  release();
  const next = card.locator(".hp-task-primary-action");
  await expect(next).toBeEnabled();
  await expect(next).toBeFocused();
  expect(await next.evaluate((node, original) => node === original, identity)).toBe(true);
  expect(state.writes).toHaveLength(1);
  expect(state.writes[0]!.path).toBe("/api/v1/works/lobe-task/resume");
});

test("refresh retains cards while loading and opening the editor restores its toolbar trigger", async ({
  page,
}) => {
  const state = await installTaskLobeFixture(page);
  await page.goto("/#/tasks?bucket=waiting");
  const card = page.getByRole("article", { name: "任务交互验收", exact: true });
  await expect(card).toBeVisible();
  let release!: () => void;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/v1/works", async (route) => {
    await pending;
    await route.fulfill({ json: { items: [state.work], next_before: null } });
  });
  const refresh = page.getByRole("button", { name: "刷新任务", exact: true });
  await refresh.click();
  await expect(refresh).toBeDisabled();
  await expect(card).toBeVisible();
  release();
  await expect(refresh).toBeEnabled();
  const create = page.getByRole("button", { name: "新建任务", exact: true });
  await create.click();
  const editor = page.getByRole("dialog", { name: "新建任务", exact: true });
  await expect(editor).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(editor).not.toBeVisible();
  await expect(create).toBeFocused();
  expect(state.writes).toHaveLength(0);
});
