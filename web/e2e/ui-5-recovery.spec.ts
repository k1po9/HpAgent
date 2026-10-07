import { expect, test, type Page } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { login } from "./helpers";
import { workFixture, runFixture, deliveryFixture } from "../src/components/tasks/taskFixtures";
const evidence = resolve(
  process.cwd(),
  "../artifacts/product-acceptance/ui-5/self-review/screenshots",
);
const inspector = (page: Page) => page.locator(".hp-inspector");

test("network fixtures: terminal output invalidation preserves expansion and empty results can refresh", async ({
  page,
}) => {
  await mkdir(evidence, { recursive: true });
  await login(page);
  let completed = false,
    fileReads = 0;
  const work = () =>
    workFixture({
      work_id: "review-output",
      title: "输出完成恢复（合成界面验收）",
      row_version: completed ? 2 : 1,
      requirement: { ...workFixture().requirement, capability_key: "research_report" },
    });
  await page.route("**/api/v1/works", (route) =>
    route.fulfill({ json: { items: [work()], next_before: null } }),
  );
  await page.route("**/api/v1/works/review-output", (route) =>
    route.fulfill({ json: { work: work() } }),
  );
  await page.route("**/api/v1/works/review-output/events/stream*", (route) =>
    route.fulfill({ status: 503, body: "fixture feed" }),
  );
  await page.route("**/api/v1/works/review-output/runs", (route) =>
    route.fulfill({
      json: {
        items: [
          { ...runFixture, work_id: "review-output", status: completed ? "succeeded" : "running" },
        ],
      },
    }),
  );
  await page.route("**/api/v1/runs/run-1/published-files", (route) => {
    fileReads++;
    return route.fulfill({
      json: { files: completed ? [{ file_id: "result", name: "completed-result.txt" }] : [] },
    });
  });
  await page.route("**/api/v1/runs/run-1/research/report", (route) =>
    route.fulfill({
      json: { report: { report_markdown: completed ? "# Final report" : "# Draft report" } },
    }),
  );
  await page.goto("/#/tasks?bucket=waiting&work=review-output&tab=outputs");
  await inspector(page).getByRole("button", { name: "读取报告与输出文件" }).click();
  await expect(inspector(page).getByText("暂无已发布文件。", { exact: true })).toBeVisible();
  await expect(inspector(page).getByRole("heading", { name: "Draft report" })).toBeVisible();
  completed = true;
  await page.getByRole("button", { name: "刷新任务", exact: true }).click();
  await expect(inspector(page).getByRole("link", { name: "completed-result.txt" })).toBeVisible();
  await expect(inspector(page).getByRole("heading", { name: "Final report" })).toBeVisible();
  await expect(inspector(page).getByRole("button", { name: "收起输出" })).toBeVisible();
  expect(fileReads).toBe(2);
  await inspector(page).getByRole("button", { name: "收起输出" }).click();
  await inspector(page).getByRole("button", { name: "读取报告与输出文件" }).click();
  await expect(inspector(page).getByRole("link", { name: "completed-result.txt" })).toBeVisible();
  expect(fileReads).toBe(2);
  await inspector(page).getByRole("button", { name: "刷新输出文件" }).click();
  await expect.poll(() => fileReads).toBe(3);
  await page.screenshot({ path: resolve(evidence, "terminal-output-refresh.png") });
});
test("network fixtures: missing/null continuation deep links stay read-only and recover by refresh", async ({
  page,
}) => {
  await mkdir(evidence, { recursive: true });
  await login(page);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  let continuation: unknown = undefined,
    version = 1;
  const work = () => ({
    ...workFixture({
      work_id: "review-invalid",
      title: "异常快照只读恢复（合成界面验收）",
      deliveries: [{ ...deliveryFixture, purpose: "fact" }],
      row_version: version,
    }),
    continuation,
  });
  await page.route("**/api/v1/works", (route) =>
    route.fulfill({ json: { items: [work()], next_before: null } }),
  );
  await page.route("**/api/v1/works/review-invalid", (route) =>
    route.fulfill({ json: { work: work() } }),
  );
  await page.route("**/api/v1/works/review-invalid/events/stream*", (route) =>
    route.fulfill({ status: 503, body: "fixture feed" }),
  );
  await page.route("**/api/v1/works/review-invalid/runs", (route) =>
    route.fulfill({ json: { items: [] } }),
  );
  for (const value of [undefined, null]) {
    continuation = value;
    version++;
    await page.goto("/#/tasks?work=review-invalid&tab=outputs");
    await page.reload();
    await expect(inspector(page).getByRole("alert")).toContainText("状态待核实");
    await expect(inspector(page).getByRole("button", { name: "确认未发送" })).toHaveCount(0);
    await inspector(page).getByRole("tab", { name: "概览" }).click();
    await inspector(page).getByRole("tab", { name: "成果与执行" }).click();
    await expect(inspector(page).getByRole("tab", { name: "成果与执行" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  }
  await page.screenshot({ path: resolve(evidence, "invalid-output-readonly.png") });
  continuation = { kind: "ready", reason: "accepted" };
  version++;
  await inspector(page).getByRole("button", { name: "刷新状态" }).click();
  await expect(inspector(page).getByRole("button", { name: "确认未发送" })).toBeVisible();
  expect(errors).toEqual([]);
});
test("real API: a failed requirement read and retry preserve the whole draft before revision", async ({
  page,
}) => {
  await mkdir(evidence, { recursive: true });
  await login(page);
  await page.getByRole("button", { name: "任务", exact: true }).click();
  await page.getByRole("button", { name: "新建任务", exact: true }).click();
  const form = page.getByRole("dialog", { name: "新建任务", exact: true });
  await form.getByLabel("工作名称").fill(`review-draft-${Date.now()}`);
  await form.getByLabel("目标 / 提醒内容").fill("原提醒内容");
  await form.getByLabel("执行时间").selectOption("once");
  await form.getByLabel("日期时间").fill("2027-01-01T09:00");
  const created = page.waitForResponse(
    (r) => new URL(r.url()).pathname === "/api/v1/works" && r.request().method() === "POST",
  );
  await form.getByRole("button", { name: "创建工作", exact: true }).click();
  const work = (await (await created).json()).work;
  await inspector(page).getByRole("button", { name: "修改要求" }).click();
  let editor = page.getByRole("dialog", { name: "修改任务要求" });
  await expect(editor.getByLabel("目标 / 提醒内容")).toHaveValue("原提醒内容");
  await expect(editor.getByRole("button", { name: "提交修订" })).toBeEnabled();
  await editor.getByRole("button", { name: "关闭修改任务要求" }).click();
  let offline = true;
  await page.route(`**/api/v1/works/${work.work_id}`, async (route) => {
    if (offline && route.request().method() === "GET") await route.abort();
    else await route.continue();
  });
  await inspector(page).getByRole("button", { name: "修改要求" }).click();
  editor = page.getByRole("dialog", { name: "修改任务要求" });
  await expect(editor.getByRole("button", { name: "重试要求" })).toBeVisible();
  await editor.getByLabel("目标 / 提醒内容").fill("保留的新提醒内容");
  await editor.getByLabel("约束（每行一条）").fill("保留约束");
  await editor.getByLabel("日期时间").fill("2027-02-01T10:00");
  await editor.getByLabel("修订原因").fill("重试后保留草稿");
  offline = false;
  await editor.getByRole("button", { name: "重试要求" }).click();
  await expect(editor.getByRole("button", { name: "提交修订" })).toBeEnabled();
  await expect(editor.getByLabel("目标 / 提醒内容")).toHaveValue("保留的新提醒内容");
  await expect(editor.getByLabel("约束（每行一条）")).toHaveValue("保留约束");
  await expect(editor.getByLabel("日期时间")).toHaveValue("2027-02-01T10:00");
  await expect(editor.getByLabel("修订原因")).toHaveValue("重试后保留草稿");
  await page.screenshot({ path: resolve(evidence, "requirement-draft-retry.png") });
  await editor.getByRole("button", { name: "提交修订" }).click();
  await expect(editor).toHaveCount(0);
  const latest = (await (await page.request.get(`/api/v1/works/${work.work_id}`)).json()).work;
  expect(latest.requirement.objective).toBe("保留的新提醒内容");
  expect(latest.requirement.constraints).toEqual(["保留约束"]);
  expect(new Date(latest.requirement.timing.due_at).toISOString()).toBe("2027-02-01T02:00:00.000Z");
});
