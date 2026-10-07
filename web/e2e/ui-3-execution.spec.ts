import { expect, test } from "@playwright/test";
import { createConversation, expectReply, login, sendMessage } from "./helpers";
import { mkdir } from "node:fs/promises";
const evidence = "../artifacts/product-acceptance/ui-3";
const id = "33333333-3333-4333-8333-333333333333";
const workId = "44444444-4444-4444-8444-444444444444";

test("real API: message-bound execution, tab navigation and terminal resource boundary", async ({
  page,
}) => {
  await login(page);
  await createConversation(page);
  await sendMessage(page, "UI-3 执行状态验收");
  await expect(page.locator(".hp-msg .hp-execution-block")).toHaveCount(1);
  await mkdir(evidence, { recursive: true });
  await page.screenshot({ path: `${evidence}/chat-running-desktop.png`, fullPage: true });
  await expectReply(page);
  let gets = 0;
  page.on("request", (request) => {
    if (/\/runs\/[^/]+$/.test(new URL(request.url()).pathname) && request.method() === "GET")
      gets++;
  });
  await page.getByRole("button", { name: "查看执行详情", exact: true }).click();
  await expect(page.getByRole("tab", { name: "概览", exact: true })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(page.getByRole("tabpanel")).toContainText("已完成");
  await page.screenshot({ path: `${evidence}/overview-chat-desktop.png`, fullPage: true });
  expect(gets).toBe(0); // Reuse the authoritative current Chat Run.
  await page.getByRole("tab", { name: "使用资料与输出", exact: true }).click();
  await expect(page.getByRole("tabpanel")).toContainText("当前接口不提供完整历史候选回放");
  await expect(page.getByRole("tabpanel")).toContainText("不代表本次未使用资料");
  await page.screenshot({ path: `${evidence}/resources-terminal-desktop.png`, fullPage: true });
  await page.getByRole("tab", { name: "高级诊断", exact: true }).click();
  await expect(page.getByRole("tabpanel")).toContainText("Model Input");
  await page.getByRole("button", { name: "关闭执行详情" }).click();
  await expect(page.getByRole("button", { name: "查看执行详情", exact: true })).toBeFocused();
  await page.getByPlaceholder("输入消息，Enter 发送").fill("保留草稿");
  await page.getByRole("button", { name: "查看执行详情", exact: true }).click();
  await page.getByRole("button", { name: "关闭执行详情" }).click();
  await expect(page.getByPlaceholder("输入消息，Enter 发送")).toHaveValue("保留草稿");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "查看执行详情", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "执行详情", exact: true })).toBeVisible();
  await page.screenshot({ path: `${evidence}/overview-chat-mobile.png`, fullPage: true });
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "查看执行详情", exact: true })).toBeFocused();
});

test("network fixtures: Work snapshot, missing Trace, approvals and responsive inspector", async ({
  page,
}) => {
  await login(page);
  let posts = 0,
    status = "pending";
  const approval = () => ({
    approval_id: "55555555-5555-4555-8555-555555555555",
    run_id: id,
    operation_id: "synthetic-write-1",
    action_summary: "将合成验收文件保存到指定目录",
    tool_name: "write_file",
    status,
    expires_at: "2099-01-01T00:00:00Z",
  });
  await page.route(`**/api/v1/runs/${id}`, (route) =>
    route.fulfill({
      json: {
        source_kind: "work",
        run: {
          run_id: id,
          execution_id: id,
          source_kind: "work",
          work_id: workId,
          requirement_revision: 1,
          work_control_epoch: 1,
          conversation_id: null,
          session_id: null,
          status: "succeeded",
          version: 2,
          created_at: "2026-10-07T00:00:00Z",
          started_at: "2026-10-07T00:00:01Z",
          finished_at: "2026-10-07T00:00:02Z",
          updated_at: "2026-10-07T00:00:02Z",
          failure_code: null,
          failure_message: null,
          budget: null,
          result_json: { summary: "这是合成 Work 执行结果，用于诊断界面验收。" },
          branches: [],
        },
      },
    }),
  );
  await page.route(`**/api/v1/runs/${id}/resources`, (route) =>
    route.fulfill({
      status: 404,
      json: { error: { code: "resource_not_found", message: "unavailable" } },
    }),
  );
  await page.route(`**/api/v1/runs/${id}/trace`, (route) =>
    route.fulfill({
      status: 404,
      json: { error: { code: "resource_not_found", message: "no tree" } },
    }),
  );
  await page.route(`**/api/v1/runs/${id}/published-files`, (route) =>
    route.fulfill({ json: { files: [] } }),
  );
  await page.route(`**/api/v1/runs/${id}/file-action-approvals`, (route) =>
    route.fulfill({ json: { approvals: [approval()] } }),
  );
  await page.route("**/api/v1/file-action-approvals/*/approve", async (route) => {
    posts++;
    status = "approved";
    await route.abort("connectionfailed");
  });
  await page.route(`**/api/v1/runs/${id}/model-inputs`, (route) =>
    route.fulfill({
      json: {
        visibility: "summary",
        items: [
          {
            snapshot_id: "synthetic-snapshot",
            content_hash: "abc123",
            model_call_id: "call-1",
            provider: "synthetic",
            model: "safe-test",
            phase: "decision",
            fallback_attempt: 0,
            message_count: 1,
            tool_count: 0,
          },
        ],
      },
    }),
  );
  await page.route("**/api/v1/model-inputs/synthetic-snapshot", (route) =>
    route.fulfill({
      json: {
        visibility: "summary",
        model_input: {
          snapshot_id: "synthetic-snapshot",
          content_hash: "abc123",
          provider: "synthetic",
          model: "safe-test",
          message_count: 1,
          tool_count: 0,
        },
      },
    }),
  );
  await page.goto(`/#/tasks?inspect=run:${id}`);
  await expect(page.getByRole("button", { name: "查看所属任务", exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "使用资料与输出", exact: true }).click();
  await expect(page.getByRole("button", { name: "允许", exact: true })).toBeVisible();
  await mkdir(evidence, { recursive: true });
  await page.screenshot({ path: `${evidence}/approval-pending-desktop.png`, fullPage: true });
  await page.getByRole("button", { name: "允许", exact: true }).click();
  await expect(page.getByRole("tabpanel")).toContainText("已授权，等待执行状态更新");
  await expect(page.getByRole("button", { name: "拒绝", exact: true })).toHaveCount(0);
  expect(posts).toBe(1);
  await page.getByRole("tab", { name: "高级诊断", exact: true }).click();
  await expect(page.getByRole("tabpanel")).toContainText("暂无可用诊断记录");
  await page.getByRole("button", { name: /查看模型记录/ }).click();
  await expect(page.getByTestId("model-input-summary")).toContainText("synthetic");
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
    await expect(page.getByRole("tab", { name: "高级诊断", exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
    await page.screenshot({ path: `${evidence}/advanced-${width}x${height}.png`, fullPage: true });
  }
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.getByRole("tab", { name: "概览", exact: true }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "使用资料与输出", exact: true })).toBeFocused();
  await page.getByRole("tab", { name: "概览", exact: true }).click();
  await page.getByRole("button", { name: "查看所属任务", exact: true }).click();
  await expect(page.locator(".hp-inspector")).toHaveCount(0);
  await expect(page.locator(".hp-work-panel")).toBeVisible();
  await page.goto("/#/tasks?inspect=run:66666666-6666-4666-8666-666666666666");
  await expect(page.getByRole("alert")).toContainText("对象不可用");
  await page.screenshot({ path: `${evidence}/run-unavailable-desktop.png`, fullPage: true });
  await page.getByRole("button", { name: "回到所属页面", exact: true }).click();
  await expect(page.locator(".hp-inspector")).toHaveCount(0);
});
