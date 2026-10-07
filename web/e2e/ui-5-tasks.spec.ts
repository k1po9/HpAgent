import { workFixture, deliveryFixture } from "../src/components/tasks/taskFixtures";
import { randomUUID } from "node:crypto";
import { expect, test, type Page } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { login } from "./helpers";
const evidence = resolve(
  process.env.HPAGENT_UI5_EVIDENCE_DIR ?? "../artifacts/product-acceptance/ui-5",
);
const inspector = (page: Page) => page.locator(".hp-inspector");
async function create(page: Page, title: string, kind = "reminder") {
  await page.getByRole("button", { name: "新建任务", exact: true }).click();
  const form = page.getByRole("dialog", { name: "新建任务", exact: true });
  await form.getByLabel("工作名称").fill(title);
  await form.getByLabel("目标 / 提醒内容").fill("核实任务计划与资料");
  await form.getByLabel("工作类型").selectOption(kind);
  await form.getByLabel("执行时间").selectOption("once");
  await form.getByLabel("日期时间").fill("2027-01-01T09:00");
  const result = page.waitForResponse(
    (r) => new URL(r.url()).pathname === "/api/v1/works" && r.request().method() === "POST",
  );
  await form.getByRole("button", { name: "创建工作", exact: true }).click();
  const response = await result;
  expect(response.status()).toBe(201);
  const body = await response.json();
  await expect(inspector(page).getByRole("heading", { name: title, exact: true })).toBeVisible();
  return body.work;
}
test("creates, revises, controls, increases one limit and returns from inspector children", async ({
  page,
}) => {
  await login(page);
  await page.getByRole("button", { name: "任务", exact: true }).click();
  const title = `UI5-计划-${Date.now()}`;
  const work = await create(page, title);
  expect(new Date(work.requirement.timing.due_at).toISOString()).toBe("2027-01-01T01:00:00.000Z");
  await inspector(page).getByRole("button", { name: "修改要求" }).click();
  const editor = page.getByRole("dialog", { name: "修改任务要求" });
  await editor.getByLabel("目标 / 提醒内容").fill("新的提醒目标");
  await editor.getByLabel("修订原因").fill("验证保留时间与字段");
  await editor.getByRole("button", { name: "提交修订" }).click();
  await expect(inspector(page).getByText("新的提醒目标", { exact: true })).toBeVisible();
  await inspector(page).getByRole("button", { name: "暂停任务" }).click();
  await expect(inspector(page).getByRole("button", { name: "恢复任务" })).toBeVisible();
  await inspector(page).getByRole("button", { name: "提高预算" }).click();
  const budget = page.getByRole("dialog", { name: "提高任务预算" });
  const old = work.budget.limits.model_total_tokens;
  await budget.getByRole("spinbutton", { name: /模型 Token 上限/ }).fill(String(old + 1000));
  await budget.getByRole("button", { name: "确认提高预算" }).click();
  await expect(budget).toHaveCount(0);
  const current = (await (await page.request.get(`/api/v1/works/${work.work_id}`)).json()).work;
  expect(current.budget.limits.model_total_tokens).toBe(old + 1000);
  for (const [key, value] of Object.entries(work.budget.limits))
    if (key !== "model_total_tokens") expect(current.budget.limits[key]).toBe(value);
  await inspector(page).getByRole("button", { name: "恢复任务" }).click();
  await expect(inspector(page).getByRole("button", { name: "暂停任务" })).toBeVisible();
  await inspector(page).getByRole("button", { name: "停止任务" }).click();
  await page
    .getByRole("dialog", { name: "停止任务", exact: true })
    .getByRole("button", { name: "确认停止任务" })
    .click();
  await expect(inspector(page).getByText("已结束 · 已停止", { exact: true })).toBeVisible();
  await expect(inspector(page).getByRole("button", { name: "修改要求" })).toHaveCount(0);
  await inspector(page).getByRole("button", { name: "关闭任务详情" }).click();
  await page
    .getByRole("navigation", { name: "任务分类" })
    .getByRole("button", { name: /已结束/ })
    .click();
  await expect(page.getByRole("article", { name: title })).toBeVisible();
  await page.getByRole("button", { name: title, exact: true }).click();
  await inspector(page).getByRole("tab", { name: "高级详情" }).click();
  await expect(inspector(page).getByText("r2 · 验证保留时间与字段", { exact: true })).toBeVisible();
});
test("lost create response retries identical intention and does not duplicate a task", async ({
  page,
}) => {
  await login(page);
  await page.getByRole("button", { name: "任务", exact: true }).click();
  let lost = true;
  const keys: string[] = [];
  await page.route("**/api/v1/works", async (route) => {
    if (route.request().method() === "POST") {
      keys.push(route.request().headers()["idempotency-key"]!);
      if (lost) {
        lost = false;
        await route.fetch();
        await route.abort();
        return;
      }
    }
    await route.continue();
  });
  await page.getByRole("button", { name: "新建任务", exact: true }).click();
  const form = page.getByRole("dialog", { name: "新建任务", exact: true });
  const title = `lost-create-${Date.now()}`;
  await form.getByLabel("工作名称").fill(title);
  await form.getByLabel("目标 / 提醒内容").fill("响应丢失保留草稿");
  await form.getByLabel("执行时间").selectOption("once");
  await form.getByLabel("日期时间").fill("2027-01-01T09:00");
  await form.getByRole("button", { name: "创建工作", exact: true }).click();
  await expect(form.getByRole("alert")).toContainText("响应未知");
  await expect(form.getByLabel("工作名称")).toHaveValue(title);
  await form.getByRole("button", { name: "创建工作", exact: true }).click();
  await expect(form).toHaveCount(0);
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBe(keys[1]);
  const list = await (await page.request.get("/api/v1/works")).json();
  expect(list.items.filter((w: { title: string }) => w.title === title)).toHaveLength(1);
});
test("task deep link, unavailable object, focus, responsive screens and inbox modal", async ({
  page,
}) => {
  await mkdir(evidence, { recursive: true });
  await login(page);
  await page.getByRole("button", { name: "任务", exact: true }).click();
  const title = `长中文标题 LongEnglishTaskTitle 不会挤出页面-${Date.now()}`;
  const work = await create(page, title, "research_report");
  await page.goto(`/#/tasks?bucket=waiting&type=research&work=${work.work_id}&tab=outputs`);
  await expect(inspector(page).getByRole("tab", { name: "成果与执行" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await inspector(page).getByRole("tab", { name: "概览" }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(inspector(page).getByRole("tab", { name: "成果与执行" })).toBeFocused();
  for (const [width, height] of [
    [360, 800],
    [390, 844],
    [768, 1024],
    [1024, 768],
    [1280, 800],
    [1440, 900],
    [1920, 1080],
  ]) {
    await page.setViewportSize({ width: width!, height: height! });
    await expect(inspector(page)).toBeVisible();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true);
    await page.screenshot({ path: resolve(evidence, `task-inspector-${width}x${height}.png`) });
    await inspector(page).getByRole("button", { name: "关闭任务详情" }).click();
    await page.screenshot({ path: resolve(evidence, `tasks-${width}x${height}.png`) });
    await page.getByRole("button", { name: title, exact: true }).click();
  }
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: resolve(evidence, "task-reduced-motion.png") });
  await page.evaluate(() => {
    document.documentElement.style.fontSize = "200%";
  });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
  await page.screenshot({ path: resolve(evidence, "task-text-200-percent.png") });
  await page.evaluate(() => {
    document.documentElement.style.fontSize = "";
  });
  await inspector(page).getByRole("button", { name: "关闭任务详情" }).click();
  await expect(page.getByRole("button", { name: title, exact: true })).toBeFocused();
  await page.getByRole("button", { name: "收件箱", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "账户收件箱" })).toBeVisible();
  await expect(inspector(page)).toHaveCount(0);
  await page.keyboard.press("Escape");
  await page.goto("/#/tasks?work=00000000-0000-0000-0000-000000000000");
  await expect(inspector(page).getByText("对象不可用。", { exact: true })).toBeVisible();
  await page.screenshot({ path: resolve(evidence, "task-unavailable-mobile.png") });
  await page.keyboard.press("Escape");
  await expect(inspector(page)).toHaveCount(0);
});
test("51st task remains available after serial pagination and a first-page refresh", async ({
  page,
}) => {
  test.setTimeout(180000);
  await page.goto("/");
  const username = `ui5-pagination-${Date.now()}`;
  await page.request.post("/auth/register", {
    data: { username, password: "pagination-password" },
  });
  await page.request.post("/auth/login", {
    data: { username, password: "pagination-password", return_to: "/" },
  });
  const me = await (await page.request.get("/api/v1/me")).json();
  const headers = { "X-CSRF-Token": me.csrf_token, Origin: new URL(page.url()).origin };
  let oldest = "";
  for (let i = 0; i < 51; i++) {
    const response = await page.request.post("/api/v1/works", {
      headers: { ...headers, "Idempotency-Key": randomUUID() },
      data: {
        title: `分页任务 ${i}`,
        requirement: {
          objective: "等待指定时间",
          capability_key: "reminder",
          spec: { schema_version: 1, content: "分页提醒", target_ref: "account_inbox" },
          timing: {
            schema_version: 1,
            kind: "once",
            timezone: "UTC",
            due_at: "2027-01-01T09:00:00Z",
          },
        },
      },
    });
    expect(response.status()).toBe(201);
    const work = (await response.json()).work;
    if (i === 0) oldest = work.work_id;
  }
  await page.goto("/#/tasks?bucket=waiting");
  await page.reload();
  await expect(page.getByRole("status").filter({ hasText: "已加载 51 项" })).toContainText(
    "本轮同步完成",
  );
  await expect(page.getByRole("article", { name: "分页任务 0", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "刷新任务", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "已加载 51 项" })).toContainText(
    "本轮同步完成",
  );
  await page.goto(`/#/tasks?bucket=attention&work=${oldest}`);
  await expect(
    inspector(page).getByRole("heading", { name: "分页任务 0", exact: true }),
  ).toBeVisible();
  await page.screenshot({ path: resolve(evidence, "tasks-paginated-desktop.png") });
});

test("network fixtures: four buckets, pausing decision, failure scope and empty filters", async ({
  page,
}) => {
  await mkdir(evidence, { recursive: true });
  await login(page);
  const title = "暂停中仍需确认发送（合成界面验收）";
  const items = [
    workFixture({
      work_id: "ui5-attention",
      title: "发送结果待确认（合成界面验收）",
      deliveries: [deliveryFixture],
    }),
    workFixture({
      work_id: "ui5-pausing",
      title,
      status: "pausing",
      deliveries: [deliveryFixture],
    }),
    workFixture({
      work_id: "ui5-waiting",
      title: "已暂停（合成界面验收）",
      status: "paused",
      continuation: { kind: "blocked", reason: "delivery_resolved" },
    }),
    workFixture({
      work_id: "ui5-ended",
      title: "已完成（合成界面验收）",
      status: "completed",
      deliveries: [{ ...deliveryFixture, requirement_revision: 1 }],
    }),
  ];
  await page.route("**/api/v1/works", async (route) => {
    if (route.request().method() === "GET")
      await route.fulfill({ json: { items, next_before: null } });
    else await route.continue();
  });
  for (const work of items) {
    await page.route(`**/api/v1/works/${work.work_id}`, (route) =>
      route.fulfill({ json: { work } }),
    );
    await page.route(`**/api/v1/works/${work.work_id}/runs`, (route) =>
      route.fulfill({ json: { items: [] } }),
    );
    await page.route(`**/api/v1/works/${work.work_id}/events/stream*`, (route) =>
      route.fulfill({ status: 503, body: "fixture feed unavailable" }),
    );
  }
  await page.getByRole("button", { name: "任务", exact: true }).click();
  for (const [width, height] of [
    [1440, 900],
    [390, 844],
  ]) {
    await page.setViewportSize({ width: width!, height: height! });
    for (const [bucket, label] of [
      ["attention", "需要我处理"],
      ["active", "进行中"],
      ["waiting", "等待或已计划"],
      ["ended", "已结束"],
    ]) {
      await page.goto(`/#/tasks?bucket=${bucket}`);
      await expect(page.getByRole("heading", { name: label, exact: true }).last()).toBeVisible();
      await expect(page.getByRole("article")).toHaveCount(1);
      await page.screenshot({ path: resolve(evidence, `fixture-bucket-${bucket}-${width}.png`) });
    }
    await page.goto("/#/tasks?bucket=active&work=ui5-pausing&tab=outputs");
    await expect(inspector(page).getByRole("button", { name: "确认未发送" })).toBeVisible();
    await inspector(page).getByRole("button", { name: "确认未发送" }).click();
    await expect(page.getByRole("dialog", { name: "确认投递决策" })).toContainText("重新尝试");
    await page.screenshot({ path: resolve(evidence, `fixture-pausing-decision-${width}.png`) });
    await page.getByRole("button", { name: "关闭确认投递决策" }).click();
    await inspector(page).getByRole("button", { name: "关闭任务详情" }).click();
    await page.getByLabel("任务类型").selectOption("reminder");
    await expect(page.getByText("当前筛选没有任务。", { exact: true })).toBeVisible();
    await page.screenshot({ path: resolve(evidence, `fixture-filter-empty-${width}.png`) });
  }
});
