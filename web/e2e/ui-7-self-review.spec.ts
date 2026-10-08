import { expect, test, type Page } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const evidence = resolve(
  process.cwd(),
  "../artifacts/product-acceptance/ui-7/self-review-fixes/browser",
);
const now = "2026-10-08T00:00:00Z";
const conversation = {
  conversation_id: "c1",
  title: "诊断懒加载",
  status: "active",
  last_message_seq: 2,
  metadata_version: 1,
  created_at: now,
  updated_at: now,
};
const run = {
  run_id: "chat-run",
  conversation_id: "c1",
  session_id: "s1",
  trigger_message_id: "user",
  retry_of_run_id: null,
  status: "running",
  failure: null,
  version: 1,
  created_at: now,
  started_at: now,
  finished_at: null,
  updated_at: now,
  budget: null,
  agent_strategy: "react",
};
const message = {
  message_id: "assistant",
  conversation_id: "c1",
  role: "assistant",
  status: "pending",
  content: null,
  sequence: 2,
  client_request_id: null,
  produced_by_run_id: run.run_id,
  created_at: now,
  completed_at: null,
};
const terminal = {
  source_kind: "chat",
  run: { ...run, status: "succeeded", finished_at: now, version: 2 },
  assistant_message: {
    ...message,
    status: "completed",
    content: "普通聊天完成回复",
    completed_at: now,
  },
};
const work = {
  source_kind: "work",
  run: {
    source_kind: "work",
    run_id: "run-A",
    execution_id: "exec-A",
    work_id: "work-A",
    requirement_revision: 1,
    work_control_epoch: 1,
    conversation_id: null,
    session_id: null,
    status: "succeeded",
    version: 1,
    created_at: now,
    started_at: null,
    finished_at: now,
    updated_at: now,
    failure_code: null,
    failure_message: null,
    strategy_kind: "generic_agent",
    executor_key: "general",
    result_json: {},
    budget: null,
    branches: [],
  },
};
async function fixture(page: Page) {
  let releaseLookup!: () => void, releaseTerminal!: () => void;
  const lookupGate = new Promise<void>((r) => {
    releaseLookup = r;
  });
  const terminalGate = new Promise<void>((r) => {
    releaseTerminal = r;
  });
  let firstLookup = true,
    finished = false;
  const calls: Array<{ path: string; method: string }> = [];
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    calls.push({ path, method: route.request().method() });
    let json: unknown = { items: [], next_cursor: null, has_more: false };
    if (path === "/api/v1/me")
      json = {
        account: { account_id: "review", status: "active", created_at: now },
        session: {},
        csrf_token: "fixture",
        identities: { web: { username: "review" }, qq: { bound: false } },
        capabilities: { durable_agent: true, file_upload: true },
      };
    if (path === "/api/v1/workspace")
      json = {
        workspace_id: "space",
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
        ],
      };
    if (path === "/api/v1/notifications")
      json = {
        items: [
          {
            notification_id: "notice",
            payload: { content: "通知键盘回归" },
            created_at: now,
            work_id: null,
            run_id: null,
          },
        ],
      };
    if (path === "/api/v1/runs/run-A") {
      if (firstLookup) {
        firstLookup = false;
        await lookupGate;
      }
      json = work;
    }
    if (path === "/api/v1/conversations")
      json = { items: [conversation], next_cursor: null, has_more: false };
    if (path === "/api/v1/conversations/c1")
      json = { conversation, active_run: { run, assistant_message: message } };
    if (path === "/api/v1/conversations/c1/messages")
      json = {
        items: [message],
        next_cursor: null,
        has_more: false,
        conversation_last_message_seq: 2,
      };
    if (path.endsWith("/resources"))
      json = { grants: [], attachments: [], count: 0, next: null, candidates: [] };
    if (path === "/api/v1/runs/chat-run")
      json = finished ? terminal : { source_kind: "chat", run, assistant_message: message };
    if (path === "/api/v1/runs/history")
      json = { ...terminal, run: { ...terminal.run, run_id: "history" } };
    if (path.endsWith("/trace")) json = { run: null, roots: [] };
    if (path.endsWith("/model-inputs")) json = { visibility: "summary", items: [] };
    if (path === "/api/v1/runs/chat-run/events") {
      await terminalGate;
      finished = true;
      const payload = {
        schema_version: 1,
        event_id: "terminal",
        event_type: "run.succeeded",
        conversation_id: "c1",
        run_id: "chat-run",
        message_id: "assistant",
        stream_id: null,
        event_seq: null,
        occurred_at: now,
        payload: { snapshot: terminal },
      };
      return route.fulfill({
        contentType: "text/event-stream",
        body: `id: terminal\nevent: run.succeeded\ndata: ${JSON.stringify(payload)}\n\n`,
      });
    }
    await route.fulfill({ json });
  });
  return { calls, errors, releaseLookup, releaseTerminal };
}
async function record(page: Page, name: string, data: unknown) {
  await mkdir(evidence, { recursive: true });
  await writeFile(resolve(evidence, `${name}.json`), JSON.stringify(data, null, 2) + "\n");
  await page.screenshot({ path: resolve(evidence, `${name}.png`), fullPage: true });
}
for (const foreground of ["account", "resources", "sidebar"] as const) {
  test(`R1: late Header lookup preserves newer ${foreground}`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    const f = await fixture(page);
    await page.goto("/#/ai/c1");
    await page.getByText("按执行编号查询", { exact: true }).click();
    await page.getByLabel("执行编号", { exact: true }).fill("run-A");
    await page.getByRole("button", { name: "查询", exact: true }).click();
    await expect.poll(() => f.calls.some((c) => c.path === "/api/v1/runs/run-A")).toBe(true);
    if (foreground === "account")
      await page.getByRole("button", { name: "账户设置", exact: true }).click();
    if (foreground === "resources")
      await page.getByRole("button", { name: "对话资料", exact: true }).click();
    if (foreground === "sidebar")
      await page.getByRole("button", { name: "打开侧栏", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toHaveCount(1);
    const title = await dialog.getAttribute("aria-label");
    f.releaseLookup();
    await expect(page.getByRole("button", { name: "查询", exact: true })).toBeEnabled();
    await expect(dialog).toHaveAttribute("aria-label", title!);
    await expect(page.locator(".hp-inspector")).toHaveCount(0);
    await record(page, `R1-${foreground}`, { title, calls: f.calls, errors: f.errors });
    expect(f.errors).toEqual([]);
  });
}
test("R1: late parent lookup preserves a local child operation and its draft", async ({ page }) => {
  const f = await fixture(page);
  await page.goto("/e2e/ui7-surface-harness.html");
  await page.getByRole("button", { name: "打开父层" }).click();
  await page.getByText("按执行编号查询", { exact: true }).click();
  await page.getByLabel("执行编号").fill("run-A");
  await page.getByRole("button", { name: "查询", exact: true }).click();
  await expect.poll(() => f.calls.some((c) => c.path === "/api/v1/runs/run-A")).toBe(true);
  await page.getByRole("button", { name: "打开子操作" }).click();
  await page.getByLabel("子操作草稿").fill("不打断的草稿");
  f.releaseLookup();
  await expect(page.getByRole("dialog", { name: "子操作" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "原生焦点回归" })).toBeVisible();
  await expect(page.getByRole("button", { name: "查询", exact: true })).toBeEnabled();
  expect(new URL(page.url()).hash).toBe("");
  await record(page, "R1-child", { calls: f.calls, errors: f.errors });
});
test("R2: notification summary is reached by Tab and Shift+Tab and opens with Enter/Space", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await fixture(page);
  await page.goto("/#/tasks");
  await page.getByRole("button", { name: "收件箱", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "账户收件箱" });
  await expect(page.getByText("通知键盘回归", { exact: true })).toBeVisible();
  await dialog.locator("h2").focus();
  await page.keyboard.press("Tab");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "刷新收件箱" })).toBeFocused();
  await page.keyboard.press("Tab");
  const summary = dialog.locator("summary");
  await expect(summary).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(summary.locator("..")).toHaveAttribute("open", "");
  await page.keyboard.press("Space");
  await expect(summary.locator("..")).not.toHaveAttribute("open", "");
  await page.keyboard.press("Shift+Tab");
  await expect(page.getByRole("button", { name: "刷新收件箱" })).toBeFocused();
  await page.keyboard.press("Tab");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "关闭账户收件箱" })).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(summary).toBeFocused();
  await record(page, "R2-notification", { summaryReachable: true, backgroundFocus: false });
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "收件箱", exact: true })).toBeFocused();
});
test("R2: native modal includes summary, tabindex, iframe and legend; excludes hidden/disabled controls", async ({
  page,
}) => {
  await page.goto("/e2e/ui7-surface-harness.html");
  await page.getByRole("button", { name: "打开父层" }).click();
  const dialog = page.getByRole("dialog", { name: "原生焦点回归" });
  await dialog.locator("h2").focus();
  const sequence: string[] = [];
  for (let i = 0; i < 24; i++) {
    await page.keyboard.press("Tab");
    const focus = await page.evaluate(() => {
      const node = document.activeElement;
      return {
        inside: !!node?.closest('dialog[aria-label="原生焦点回归"]'),
        name:
          node?.getAttribute("aria-label") ||
          node?.getAttribute("title") ||
          node?.textContent?.trim(),
      };
    });
    expect(focus.inside).toBe(true);
    sequence.push(focus.name || "");
  }
  for (const name of [
    "开头详情",
    "末尾详情",
    "正 tabindex 操作",
    "原生 iframe",
    "首个 legend 操作",
  ])
    expect(sequence).toContain(name);
  for (const name of [
    "禁用 fieldset 操作",
    "禁用操作",
    "隐藏操作",
    "inert 操作",
    "非 Tab 操作",
    "闭合详情内输入",
    "背景操作",
  ])
    expect(sequence).not.toContain(name);
  await dialog.locator("summary").first().focus();
  await page.keyboard.press("Enter");
  await page.keyboard.press("Tab");
  await expect(page.getByLabel("闭合详情内输入")).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await page.keyboard.press("Space");
  await expect(dialog.locator("details").first()).not.toHaveAttribute("open", "");
  const frameInput = page.frameLocator('iframe[title="原生 iframe"]').getByLabel("iframe 输入");
  await frameInput.focus();
  await expect(frameInput).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(dialog.locator(".hp-run-lookup summary")).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(frameInput).toBeFocused();
  await dialog.locator("summary").last().focus();
  await page.keyboard.press("Space");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "展开详情操作" })).toBeFocused();
  await page.getByRole("button", { name: "打开子操作" }).click();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "关闭子操作" })).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(page.getByLabel("子操作草稿")).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "打开子操作" })).toBeFocused();
  await record(page, "R2-native-modal", { sequence });
});
test("R2: desktop nonmodal permits background Tab navigation and preserves Escape focus return", async ({
  page,
}) => {
  await page.goto("/e2e/ui7-surface-harness.html?desktop=1");
  await page.getByRole("button", { name: "打开父层" }).click();
  const summary = page.getByRole("complementary").locator("summary").last();
  await summary.focus();
  let reachedBackground = false;
  for (let i = 0; i < 5 && !reachedBackground; i++) {
    await page.keyboard.press("Tab");
    reachedBackground = await page
      .getByRole("button", { name: "打开父层" })
      .evaluate((node) => node === document.activeElement);
  }
  expect(reachedBackground).toBe(true);
  await summary.focus();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("complementary")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "打开父层" })).toBeFocused();
});
for (const mode of ["ordinary", "overview", "advanced", "history", "closed"] as const) {
  test(`R3: terminal Trace GET respects ${mode} diagnostics and confirms the final message`, async ({
    page,
  }) => {
    const f = await fixture(page);
    const inspect =
      mode === "ordinary"
        ? ""
        : `?inspect=run:${mode === "history" ? "history" : "chat-run"}&tab=${mode === "overview" ? "overview" : "advanced"}`;
    await page.goto(`/#/ai/c1${inspect}`);
    await expect.poll(() => f.calls.some((c) => c.path.endsWith("/events"))).toBe(true);
    if (["advanced", "history", "closed"].includes(mode)) {
      await expect(page.getByText("暂无可用诊断记录。", { exact: true })).toBeVisible();
      expect(f.calls.filter((c) => c.path.endsWith("/trace")).length).toBeGreaterThanOrEqual(1);
      if (mode === "closed") await page.getByRole("button", { name: "关闭执行详情" }).click();
    }
    const before = f.calls.filter((c) => c.path.endsWith("/trace")).length;
    const runReads = f.calls.filter((c) => c.path === "/api/v1/runs/chat-run").length;
    f.releaseTerminal();
    await expect(
      page.getByRole("region", { name: "对话页面" }).getByText("普通聊天完成回复", { exact: true }),
    ).toBeVisible();
    await expect
      .poll(() => f.calls.filter((c) => c.path === "/api/v1/runs/chat-run").length)
      .toBeGreaterThan(runReads);
    if (mode === "advanced")
      await expect
        .poll(() => f.calls.filter((c) => c.path.endsWith("/trace")).length)
        .toBe(before + 1);
    expect(f.calls.filter((c) => c.path.endsWith("/trace")).length).toBe(
      before + (mode === "advanced" ? 1 : 0),
    );
    await record(page, `R3-${mode}`, {
      before,
      after: f.calls.filter((c) => c.path.endsWith("/trace")).length,
      calls: f.calls,
      errors: f.errors,
    });
    expect(f.errors).toEqual([]);
  });
}
