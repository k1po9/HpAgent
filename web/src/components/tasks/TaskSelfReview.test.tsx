import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { HpCommandError, type HpWork } from "../../api/types";
import { api } from "../../api/client";
import { useWorks } from "../../store/works";
import { useShell } from "../../store/shell";
import { workFixture, runFixture, artifactFixture, deliveryFixture } from "./taskFixtures";
import { TaskOutputs } from "./TaskOutputs";
import { TaskInspector } from "./TaskInspector";
import { TaskEditor } from "./TaskEditor";
import { resetTaskQueries } from "./useTaskQuery";

afterEach(() => {
  cleanup();
  useWorks.getState().reset();
  resetTaskQueries();
  useShell.getState().reset();
  vi.restoreAllMocks();
});
it("R1 refreshes an initially empty published-files result after the Run completes", async () => {
  let completed = false;
  const work = workFixture();
  const spy = vi.spyOn(api, "request").mockImplementation(async ({ path }) => {
    if (path.endsWith("/runs"))
      return { items: [{ ...runFixture, status: completed ? "succeeded" : "running" }] } as never;
    if (path.endsWith("/published-files"))
      return { files: completed ? [{ file_id: "f1", name: "completed-result.txt" }] : [] } as never;
    throw new Error(path);
  });
  const view = render(<TaskOutputs work={work} onSaveFile={() => {}} />);
  fireEvent.click(await screen.findByText("读取报告与输出文件"));
  await screen.findByText("暂无已发布文件。");
  completed = true;
  view.rerender(
    <TaskOutputs work={{ ...work, row_version: work.row_version + 1 }} onSaveFile={() => {}} />,
  );
  await screen.findByText(/执行成功/);
  await screen.findByRole("link", { name: "completed-result.txt" });
  expect(screen.getByText("收起输出")).toBeVisible();
  fireEvent.click(screen.getByText("收起输出"));
  fireEvent.click(screen.getByText("读取报告与输出文件"));
  await screen.findByRole("link", { name: "completed-result.txt" });
  expect(spy.mock.calls.filter(([r]) => r.path.endsWith("/published-files"))).toHaveLength(2);
  expect(screen.getByRole("link", { name: "completed-result.txt" })).toBeVisible();
});
it("R2 safely opens outputs for an M21 missing-continuation snapshot", async () => {
  const work = workFixture({ continuation: undefined as never });
  useWorks.getState().upsert(work);
  vi.spyOn(api, "request").mockImplementation(
    async ({ path }) => (path.endsWith("/runs") ? { items: [] } : { work }) as never,
  );
  expect(() =>
    render(
      <TaskInspector
        inspector={{ kind: "task", objectId: work.work_id, tab: "outputs" }}
        onSaveFile={() => {}}
      />,
    ),
  ).not.toThrow();
  expect(screen.getByRole("alert")).toHaveTextContent("状态待核实");
});
it("R3 preserves editor draft when retrying a failed baseline refresh", async () => {
  const work = workFixture();
  const spy = vi.spyOn(api, "request").mockResolvedValue({ work });
  const first = render(<TaskEditor workId={work.work_id} onClose={() => {}} />);
  await screen.findByLabelText("目标 / 提醒内容");
  first.unmount();
  spy.mockRejectedValueOnce(new TypeError("temporary offline"));
  render(<TaskEditor workId={work.work_id} onClose={() => {}} />);
  await screen.findByText(/temporary offline/);
  fireEvent.change(screen.getByLabelText("目标 / 提醒内容"), {
    target: { value: "must preserve my draft" },
  });
  fireEvent.change(screen.getByLabelText("修订原因"), { target: { value: "draft reason" } });
  fireEvent.click(screen.getByText("重试要求"));
  await waitFor(() =>
    expect(screen.getByLabelText("目标 / 提醒内容")).toHaveValue("must preserve my draft"),
  );
  expect(screen.getByLabelText("修订原因")).toHaveValue("draft reason");
});

it("R1 reloads files when a collapsed running output is reopened after completion", async () => {
  let status = "running";
  const work = workFixture();
  const spy = vi.spyOn(api, "request").mockImplementation(async ({ path }) => {
    if (path.endsWith("/runs")) return { items: [{ ...runFixture, status }] } as never;
    return {
      files: status === "running" ? [] : [{ file_id: "f1", name: "finished.txt" }],
    } as never;
  });
  const view = render(<TaskOutputs work={work} onSaveFile={() => {}} />);
  fireEvent.click(await screen.findByText("读取报告与输出文件"));
  await screen.findByText("暂无已发布文件。");
  fireEvent.click(screen.getByText("收起输出"));
  status = "succeeded";
  view.rerender(<TaskOutputs work={{ ...work, row_version: 2 }} onSaveFile={() => {}} />);
  await screen.findByText(/执行成功/);
  fireEvent.click(screen.getByText("读取报告与输出文件"));
  await screen.findByRole("link", { name: "finished.txt" });
  expect(spy.mock.calls.filter(([r]) => r.path.endsWith("/published-files"))).toHaveLength(2);
});
it("R1 updates cached report and files on terminal status and lets empty outputs refresh independently", async () => {
  let status = "running",
    refreshed = false;
  const work = workFixture({
    requirement: { ...workFixture().requirement, capability_key: "research_report" },
  });
  const spy = vi.spyOn(api, "request").mockImplementation(async ({ path }) => {
    if (path.endsWith("/runs")) return { items: [{ ...runFixture, status }] } as never;
    if (path.endsWith("/research/report"))
      return {
        report: { report_markdown: status === "running" ? "# Draft report" : "# Final report" },
      } as never;
    return { files: refreshed ? [{ file_id: "f", name: "late.txt" }] : [] } as never;
  });
  const view = render(<TaskOutputs work={work} onSaveFile={() => {}} />);
  fireEvent.click(await screen.findByText("读取报告与输出文件"));
  await screen.findByRole("heading", { name: "Draft report" });
  status = "succeeded";
  view.rerender(<TaskOutputs work={{ ...work, row_version: 2 }} onSaveFile={() => {}} />);
  await screen.findByRole("heading", { name: "Final report" });
  await waitFor(() => expect(screen.getByText("刷新输出文件")).toBeEnabled());
  const before = spy.mock.calls.filter(([r]) => r.path.endsWith("/research/report")).length;
  refreshed = true;
  fireEvent.click(screen.getByText("刷新输出文件"));
  await screen.findByRole("link", { name: "late.txt" });
  expect(spy.mock.calls.filter(([r]) => r.path.endsWith("/research/report"))).toHaveLength(before);
  fireEvent.click(screen.getByText("刷新报告"));
  await waitFor(() =>
    expect(spy.mock.calls.filter(([r]) => r.path.endsWith("/research/report"))).toHaveLength(
      before + 1,
    ),
  );
});
it("R1 keeps successful files visible through a report failure and retries only the report", async () => {
  let offline = true;
  const work = workFixture({
    requirement: { ...workFixture().requirement, capability_key: "research_report" },
  });
  const spy = vi.spyOn(api, "request").mockImplementation(async ({ path }) => {
    if (path.endsWith("/runs")) return { items: [runFixture] } as never;
    if (path.endsWith("/research/report")) {
      if (offline) throw new TypeError("report offline");
      return { report: { report_markdown: "# Recovered" } } as never;
    }
    return { files: [{ file_id: "f", name: "existing.txt" }] } as never;
  });
  render(<TaskOutputs work={work} onSaveFile={() => {}} />);
  fireEvent.click(await screen.findByText("读取报告与输出文件"));
  await screen.findByRole("link", { name: "existing.txt" });
  await screen.findByText("报告暂不可用。");
  offline = false;
  fireEvent.click(screen.getByText("重试报告"));
  await screen.findByRole("heading", { name: "Recovered" });
  expect(screen.getByRole("link", { name: "existing.txt" })).toBeVisible();
  expect(spy.mock.calls.filter(([r]) => r.path.endsWith("/published-files"))).toHaveLength(1);
});
it("R1 discards a running output read arriving after a terminal read or account reset", async () => {
  let finish!: (value: unknown) => void;
  let status = "running";
  const work = workFixture();
  vi.spyOn(api, "request").mockImplementation(async ({ path }) => {
    if (path.endsWith("/runs")) return { items: [{ ...runFixture, status }] } as never;
    if (status === "running")
      return (await new Promise<unknown>((resolve) => {
        finish = resolve;
      })) as never;
    return { files: [{ file_id: "new", name: "terminal.txt" }] } as never;
  });
  const view = render(<TaskOutputs work={work} onSaveFile={() => {}} />);
  fireEvent.click(await screen.findByText("读取报告与输出文件"));
  await waitFor(() => expect(finish).toBeDefined());
  status = "succeeded";
  view.rerender(<TaskOutputs work={{ ...work, row_version: 2 }} onSaveFile={() => {}} />);
  await screen.findByRole("link", { name: "terminal.txt" });
  await act(async () => finish({ files: [{ file_id: "old", name: "stale.txt" }] }));
  expect(screen.queryByText("stale.txt")).toBeNull();
  view.unmount();
  status = "running";
  resetTaskQueries();
  const another = render(<TaskOutputs work={work} onSaveFile={() => {}} />);
  fireEvent.click(await screen.findByText("读取报告与输出文件"));
  await waitFor(() => expect(screen.getByText("正在读取输出文件…")).toBeVisible());
  another.unmount();
  useWorks.getState().reset();
  resetTaskQueries();
  await act(async () => finish({ files: [{ file_id: "secret", name: "old-account-secret.txt" }] }));
  status = "succeeded";
  render(<TaskOutputs work={work} onSaveFile={() => {}} />);
  fireEvent.click(await screen.findByText("读取报告与输出文件"));
  await screen.findByRole("link", { name: "terminal.txt" });
  expect(screen.queryByText("old-account-secret.txt")).toBeNull();
});
function RoutedInspector() {
  const inspector = useShell((s) => s.route.inspector);
  return inspector ? <TaskInspector inspector={inspector} onSaveFile={() => {}} /> : null;
}
it.each([undefined, null])(
  "R2 allows read-only deep links/tabs and refresh recovery for continuation=%s",
  async (continuation) => {
    const valid = workFixture({
      artifacts: [artifactFixture],
      deliveries: [{ ...deliveryFixture, purpose: "fact" }],
      requirement: {
        ...workFixture().requirement,
        acceptance_criteria: [
          { id: "result", required: true, evidence_types: ["user_acceptance"] },
        ],
      },
    });
    const invalid: HpWork = { ...valid, continuation: continuation as never };
    let recovered = false;
    useWorks.getState().upsert(invalid);
    useShell.setState({
      route: {
        screen: "tasks",
        inspector: { kind: "task", objectId: valid.work_id, tab: "outputs" },
      },
    });
    vi.spyOn(api, "request").mockImplementation(async ({ path }) => {
      if (path.endsWith("/runs")) return { items: [runFixture] } as never;
      return { work: recovered ? { ...valid, row_version: 2 } : invalid } as never;
    });
    render(<RoutedInspector />);
    await screen.findByText(/执行成功/);
    expect(screen.getByRole("alert")).toHaveTextContent("当前仅可查看与刷新");
    expect(screen.queryByText("接受这份成果")).toBeNull();
    expect(screen.queryByText("确认未发送")).toBeNull();
    expect(screen.queryByText("接受重复风险并重试投递")).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: "概览" }));
    fireEvent.click(screen.getByRole("tab", { name: "成果与执行" }));
    expect(screen.getByRole("tab", { name: "成果与执行" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    fireEvent.click(screen.getByRole("tab", { name: "使用资料" }));
    expect(screen.getByText("任务状态待核实，资料管理暂不可用。")).toBeVisible();
    fireEvent.click(screen.getByRole("tab", { name: "成果与执行" }));
    recovered = true;
    fireEvent.click(screen.getByRole("button", { name: "刷新状态" }));
    await screen.findByRole("button", { name: "接受这份成果" });
    expect(screen.queryByText("状态待核实，当前仅可查看与刷新。")).toBeNull();
  },
);
it("R3 preserves every edited field through failed and successful retries and reviews the new revision", async () => {
  const work = workFixture();
  let failed = false;
  const latest = workFixture({ row_version: 3, current_requirement_revision: 4 });
  const spy = vi.spyOn(api, "request").mockResolvedValue({ work });
  const warm = render(<TaskEditor workId={work.work_id} onClose={() => {}} />);
  await screen.findByLabelText("目标 / 提醒内容");
  warm.unmount();
  spy.mockRejectedValueOnce(new TypeError("temporary offline"));
  const close = vi.fn();
  render(<TaskEditor workId={work.work_id} onClose={close} />);
  await screen.findByText(/temporary offline/);
  fireEvent.change(screen.getByLabelText("目标 / 提醒内容"), {
    target: { value: "draft objective" },
  });
  fireEvent.change(screen.getByLabelText("约束（每行一条）"), {
    target: { value: "keep constraint" },
  });
  fireEvent.change(screen.getByLabelText("执行时间"), { target: { value: "once" } });
  fireEvent.change(screen.getByLabelText("日期时间"), { target: { value: "2027-02-01T13:00" } });
  fireEvent.change(screen.getByLabelText("修订原因"), { target: { value: "draft reason" } });
  let finish!: () => void;
  spy.mockImplementation(async ({ method }) => {
    if (method === "POST") return { work: latest } as never;
    await new Promise<void>((resolve) => {
      finish = resolve;
    });
    if (!failed) {
      failed = true;
      throw new TypeError("retry offline");
    }
    return { work: latest } as never;
  });
  const checkDraft = () => {
    expect(screen.getByLabelText("目标 / 提醒内容")).toHaveValue("draft objective");
    expect(screen.getByLabelText("约束（每行一条）")).toHaveValue("keep constraint");
    expect(screen.getByLabelText("日期时间")).toHaveValue("2027-02-01T13:00");
    expect(screen.getByLabelText("修订原因")).toHaveValue("draft reason");
  };
  fireEvent.click(screen.getByText("重试要求"));
  await waitFor(() => expect(screen.getByText("正在核实当前要求…")).toBeVisible());
  checkDraft();
  expect(screen.getByText("提交修订")).toBeDisabled();
  await act(async () => finish());
  await screen.findByText(/retry offline/);
  checkDraft();
  fireEvent.click(screen.getByText("重试要求"));
  await act(async () => finish());
  await screen.findByText(/服务器已更新到 r4/);
  checkDraft();
  fireEvent.click(screen.getByRole("button", { name: "关闭修改任务要求" }));
  expect(screen.getByRole("dialog", { name: "放弃未提交的任务草稿？" })).toBeVisible();
  fireEvent.click(screen.getByText("继续编辑"));
  fireEvent.click(screen.getByText("已审阅最新要求，采用新基线提交"));
  fireEvent.click(screen.getByText("提交修订"));
  await waitFor(() => expect(close).toHaveBeenCalledTimes(1));
  const post = spy.mock.calls.find(([r]) => r.method === "POST")![0];
  expect(post.headers).toEqual({ "If-Match": '"work-w1-v3"' });
  expect(post.body).toMatchObject({
    change_reason: "draft reason",
    requirement: {
      objective: "draft objective",
      constraints: ["keep constraint"],
      timing: { due_at: "2027-02-01T13:00:00.000Z" },
    },
  });
});
it.each([403, 404])(
  "R3 clears the cached editor and draft when retry returns %s",
  async (status) => {
    const work = workFixture();
    const spy = vi.spyOn(api, "request").mockResolvedValue({ work });
    const warm = render(<TaskEditor workId="w1" onClose={() => {}} />);
    await screen.findByLabelText("目标 / 提醒内容");
    warm.unmount();
    spy.mockRejectedValueOnce(new TypeError("offline"));
    render(<TaskEditor workId="w1" onClose={() => {}} />);
    await screen.findByText(/offline/);
    fireEvent.change(screen.getByLabelText("目标 / 提醒内容"), {
      target: { value: "private draft" },
    });
    spy.mockRejectedValueOnce(
      new HpCommandError(status, {
        code: "unavailable",
        message: "no access",
        request_id: "test",
        retryable: false,
        details: {},
      }),
    );
    fireEvent.click(screen.getByText("重试要求"));
    await screen.findByText("对象不可用。");
    expect(screen.queryByLabelText("目标 / 提醒内容")).toBeNull();
    expect(screen.queryByText("private draft")).toBeNull();
    expect(useShell.getState().dirtyTaskEditor).toBeNull();
  },
);
it("R3 resets drafts on object/account changes and ignores the old baseline response", async () => {
  const work = workFixture();
  let finish!: (value: unknown) => void;
  const spy = vi.spyOn(api, "request").mockResolvedValue({ work });
  const view = render(<TaskEditor workId="w1" onClose={() => {}} />);
  await screen.findByLabelText("目标 / 提醒内容");
  fireEvent.change(screen.getByLabelText("目标 / 提醒内容"), {
    target: { value: "private draft" },
  });
  spy.mockResolvedValue({
    work: workFixture({
      work_id: "w2",
      requirement: { ...work.requirement, objective: "second object" },
    }),
  });
  view.rerender(<TaskEditor workId="w2" onClose={() => {}} />);
  await waitFor(() =>
    expect(screen.getByLabelText("目标 / 提醒内容")).toHaveValue("second object"),
  );
  expect(screen.queryByText("private draft")).toBeNull();
  fireEvent.change(screen.getByLabelText("目标 / 提醒内容"), { target: { value: "second draft" } });
  spy.mockImplementationOnce(
    () =>
      new Promise<unknown>((resolve) => {
        finish = resolve;
      }) as never,
  );
  view.rerender(<TaskEditor workId="w1" onClose={() => {}} />);
  await waitFor(() => expect(finish).toBeDefined());
  spy.mockResolvedValue({
    work: workFixture({ requirement: { ...work.requirement, objective: "new account objective" } }),
  });
  await act(async () => {
    useWorks.getState().reset();
    resetTaskQueries();
  });
  await waitFor(() =>
    expect(screen.getByLabelText("目标 / 提醒内容")).toHaveValue("new account objective"),
  );
  await act(async () => finish({ work }));
  expect(screen.getByLabelText("目标 / 提醒内容")).toHaveValue("new account objective");
  expect(screen.getByLabelText("修订原因")).toHaveValue("");
  expect(screen.queryByText("second draft")).toBeNull();
});
