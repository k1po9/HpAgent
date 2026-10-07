import { StrictMode } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { api } from "../../api/client";
import { HpCommandError } from "../../api/types";
import { useWorks } from "../../store/works";
import { useShell } from "../../store/shell";
import { workFixture, deliveryFixture } from "./taskFixtures";
import { TaskOutputs } from "./TaskOutputs";
import { TaskInbox } from "./TaskInbox";
import { TaskDeliveryDecision } from "./TaskDeliveryDecision";
import { TaskEditor } from "./TaskEditor";
import { TaskBudgetDialog } from "./TaskBudgetDialog";
import { TaskScreen } from "./TaskScreen";
import { TaskSidebar } from "./TaskSidebar";
import { resetTaskQueries } from "./useTaskQuery";
afterEach(() => {
  cleanup();
  useWorks.getState().reset();
  resetTaskQueries();
  useShell.getState().reset();
  vi.restoreAllMocks();
});
it("keeps report readable when files fail, retries files independently and renders markdown", async () => {
  const work = workFixture({
    requirement: { ...workFixture().requirement, capability_key: "research_report" },
  });
  const spy = vi.spyOn(api, "request").mockImplementation(async ({ path }) => {
    if (path.endsWith("/runs"))
      return {
        items: [
          {
            run_id: "r",
            work_id: "w1",
            status: "succeeded",
            requirement_revision: 2,
            work_control_epoch: 3,
            result_json: null,
            failure_code: null,
          },
        ],
      } as never;
    if (path.endsWith("/research/report"))
      return {
        report: { report_markdown: "# Research\n\n**Verified** result<script>bad()</script>" },
      } as never;
    throw new Error("files offline");
  });
  render(<TaskOutputs work={work} onSaveFile={() => {}} />);
  await screen.findByText("读取报告与输出文件");
  fireEvent.click(screen.getByText("读取报告与输出文件"));
  await screen.findByRole("heading", { name: "Research" });
  expect(screen.getByText("Verified")).toHaveProperty("tagName", "STRONG");
  expect(document.querySelector("script")).toBeNull();
  await screen.findByText("输出文件暂不可用。");
  spy.mockImplementation(
    async () => ({ files: [{ file_id: "file", name: "report.md" }] }) as never,
  );
  fireEvent.click(screen.getByText("重试输出文件"));
  await screen.findByRole("link", { name: "report.md" });
  expect(screen.getByRole("heading", { name: "Research" })).toBeVisible();
});
it("preserves the pausing not-sent decision and requires explicit confirmation", async () => {
  const work = workFixture({ status: "pausing" });
  const spy = vi.spyOn(api, "request").mockResolvedValue({ work });
  render(<TaskDeliveryDecision work={work} delivery={deliveryFixture} />);
  expect(screen.queryByText("接受重复风险并重试投递")).toBeNull();
  fireEvent.click(screen.getByText("确认未发送"));
  expect(screen.getByText(/重新尝试/)).toBeVisible();
  expect(spy).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("确认此投递决策"));
  await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
});
it("paginates 100 notifications, deduplicates and reads payload content without invented unread state", async () => {
  const notifications = Array.from({ length: 100 }, (_, i) => ({
    notification_id: `n${i}`,
    work_id: "work",
    run_id: null,
    created_at: "2026-10-07T00:00:00Z",
    payload: { content: `通知 ${i}` },
    provider_receipt: { level: "account_inbox_committed" },
  }));
  const spy = vi
    .spyOn(api, "request")
    .mockResolvedValueOnce({ items: notifications })
    .mockResolvedValueOnce({
      items: [
        notifications[99],
        { ...notifications[0], notification_id: "older", payload: { content: "更早通知" } },
      ],
    });
  render(<TaskInbox onClose={() => {}} />);
  await screen.findByText("通知 99");
  fireEvent.click(screen.getByText("加载更多通知"));
  await screen.findByText("更早通知");
  expect(screen.getAllByText("通知 99")).toHaveLength(1);
  expect(spy.mock.calls[1]![0].path).toBe("/api/v1/notifications?before=n99");
  expect(screen.queryByText("加载更多通知")).toBeNull();
});
it("ignores notification success after unmount/account change", async () => {
  let done!: (v: unknown) => void;
  vi.spyOn(api, "request").mockImplementation(
    () =>
      new Promise((r) => {
        done = r;
      }) as never,
  );
  const view = render(<TaskInbox onClose={() => {}} />);
  await waitFor(() => expect(done).toBeDefined());
  view.unmount();
  useWorks.getState().reset();
  await act(async () =>
    done({ items: [{ notification_id: "private", payload: { content: "secret" } }] }),
  );
  expect(screen.queryByText("secret")).toBeNull();
});
it("blocks the terminal requirement editor", async () => {
  const work = workFixture({ status: "completed" });
  vi.spyOn(api, "request").mockResolvedValue({ work });
  render(<TaskEditor workId="w1" onClose={() => {}} />);
  await screen.findByText("此任务不可通过普通要求表单修改。");
  expect(screen.queryByRole("button", { name: "提交修订" })).toBeNull();
});

it("keeps a rejected revision draft open and submits only after reviewing the new baseline", async () => {
  const work = workFixture();
  const latest = workFixture({ row_version: 2, current_requirement_revision: 3 });
  let writes = 0;
  const spy = vi.spyOn(api, "request").mockImplementation(async ({ method }) => {
    if (method === "GET") return { work: writes ? latest : work } as never;
    writes++;
    if (writes === 1)
      throw new HpCommandError(409, {
        code: "work_version_conflict",
        message: "conflict",
        request_id: "test",
        retryable: false,
        details: {},
      });
    return { work: { ...latest, row_version: 3 } } as never;
  });
  const close = vi.fn();
  render(<TaskEditor workId="w1" onClose={close} />);
  const objective = await screen.findByLabelText("目标 / 提醒内容");
  fireEvent.change(objective, { target: { value: "我的未提交要求" } });
  fireEvent.change(screen.getByLabelText("修订原因"), { target: { value: "审阅修订" } });
  fireEvent.click(screen.getByRole("button", { name: "提交修订" }));
  await screen.findByRole("button", { name: "已审阅最新要求，采用新基线提交" });
  expect(close).not.toHaveBeenCalled();
  expect(objective).toHaveValue("我的未提交要求");
  fireEvent.click(screen.getByRole("button", { name: "已审阅最新要求，采用新基线提交" }));
  fireEvent.click(screen.getByRole("button", { name: "提交修订" }));
  await waitFor(() => expect(close).toHaveBeenCalledTimes(1));
  const posts = spy.mock.calls.filter(([r]) => r.method === "POST");
  expect(posts[1]![0].headers).toEqual({ "If-Match": '"work-w1-v2"' });
  expect(posts[1]![0].idempotencyKey).not.toBe(posts[0]![0].idempotencyKey);
});

it("retains one-dimensional budget input through a version conflict", async () => {
  const work = workFixture();
  const latest = workFixture({ row_version: 2, budget: { ...work.budget!, version: 2 } });
  let writes = 0;
  const spy = vi.spyOn(api, "request").mockImplementation(async ({ method }) => {
    if (method === "GET") return { work: latest } as never;
    writes++;
    if (writes === 1)
      throw new HpCommandError(409, {
        code: "budget_version_conflict",
        message: "conflict",
        request_id: "test",
        retryable: false,
        details: {},
      });
    return { work: latest } as never;
  });
  useWorks.setState({ items: [work] });
  const close = vi.fn();
  render(<TaskBudgetDialog work={work} onClose={close} />);
  const value = screen.getByRole("spinbutton", { name: /模型 Token 上限/ });
  fireEvent.change(value, { target: { value: "2000" } });
  fireEvent.submit(value.closest("form")!);
  await screen.findByRole("button", { name: "已审阅新预算，采用新基线" });
  expect(value).toHaveValue(2000);
  expect(close).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "已审阅新预算，采用新基线" }));
  fireEvent.submit(value.closest("form")!);
  await waitFor(() => expect(close).toHaveBeenCalledTimes(1));
  const posts = spy.mock.calls.filter(([r]) => r.method === "POST");
  expect(posts[1]![0].body).toEqual({ limits: { model_total_tokens: 2000 }, budget_version: 2 });
});

it("requires a separate duplicate-risk confirmation before retrying an eligible delivery", async () => {
  const work = workFixture({
    continuation: {
      kind: "awaiting_delivery",
      reason: "delivery_uncertain",
      receipt_ref: "notification-1",
    },
  });
  const spy = vi.spyOn(api, "request").mockResolvedValue({ work });
  render(<TaskDeliveryDecision work={work} delivery={deliveryFixture} />);
  fireEvent.click(screen.getByRole("button", { name: "接受重复风险并重试投递" }));
  expect(spy).not.toHaveBeenCalled();
  expect(screen.getByRole("dialog", { name: "确认投递决策" })).toHaveTextContent("重复通知");
  fireEvent.click(screen.getByRole("button", { name: "确认承担重复风险并重试" }));
  await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
  expect(spy.mock.calls[0]![0].body).toEqual({ outcome: "retry_accepting_duplicate_risk" });
});
it("filters counts by business type and keeps a focused task in place when it moves bucket", () => {
  const work = workFixture();
  const ended = workFixture({
    work_id: "w2",
    title: "已完成研究",
    status: "completed",
    requirement: { ...work.requirement, capability_key: "research_report" },
  });
  useShell.setState({ route: { screen: "tasks", bucket: "waiting", type: "all" } });
  useWorks.setState({ items: [work, ended], loadState: "complete" });
  render(
    <StrictMode>
      <TaskSidebar />
      <TaskScreen />
    </StrictMode>,
  );
  const title = screen.getByRole("button", { name: "测试任务" });
  fireEvent.focus(title);
  act(() => useWorks.getState().upsert({ ...work, status: "completed", row_version: 2 }));
  expect(screen.getByRole("article", { name: "测试任务" })).toBeVisible();
  expect(screen.getByText(/当前查看：此任务已归入/)).toBeVisible();
  fireEvent.change(screen.getByLabelText("任务类型"), { target: { value: "research" } });
  expect(screen.queryByRole("article", { name: "测试任务" })).toBeNull();
  expect(screen.getByRole("button", { name: /已结束/ })).toHaveTextContent("1");
});

it("keeps a committed snapshot but does not navigate after the editor was closed", async () => {
  let finish!: (value: unknown) => void;
  vi.spyOn(api, "request").mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }) as never,
  );
  const close = vi.fn();
  const view = render(<TaskEditor onClose={close} />);
  fireEvent.change(screen.getByLabelText("工作名称"), { target: { value: "late-create" } });
  fireEvent.change(screen.getByLabelText("目标 / 提醒内容"), {
    target: { value: "late objective" },
  });
  fireEvent.click(screen.getByRole("button", { name: "创建工作" }));
  await waitFor(() => expect(finish).toBeDefined());
  view.unmount();
  useShell.setState({ route: { screen: "workspace" } });
  await act(async () => finish({ work: workFixture({ work_id: "late" }) }));
  expect(useWorks.getState().items.some((w) => w.work_id === "late")).toBe(true);
  expect(useShell.getState().route.screen).toBe("workspace");
  expect(close).not.toHaveBeenCalled();
});
