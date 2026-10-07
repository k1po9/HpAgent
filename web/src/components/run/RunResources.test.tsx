import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { RunResources } from "./RunResources";
import { useShell } from "../../store/shell";
import { runApi, useRunInspector } from "../../store/runInspector";
import { HpCommandError } from "../../api/types";
beforeEach(() => {
  useShell.getState().reset();
  useRunInspector.getState().reset();
  useRunInspector.getState().select("A");
  vi.spyOn(runApi, "listRunFileApprovals").mockResolvedValue({ approvals: [] });
  vi.spyOn(runApi, "listRunPublishedFiles").mockResolvedValue({ files: [] });
});
afterEach(() => vi.restoreAllMocks());
it("keeps terminal resource denial local and never claims no files were used", async () => {
  vi.spyOn(runApi, "listRunResources").mockRejectedValue(
    new HpCommandError(404, {
      code: "not_found",
      message: "missing",
      request_id: null,
      retryable: false,
      details: {},
    }),
  );
  render(<RunResources runId="A" terminal />);
  expect(await screen.findByText(/不代表本次未使用资料/)).toBeInTheDocument();
  expect(screen.getByText(/不提供完整历史候选回放/)).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "已发布输出" })).toBeInTheDocument();
  expect(screen.queryByText("对象不可用。")).not.toBeInTheDocument();
});
it("serializes pagination and deduplicates node IDs", async () => {
  const candidate = (id: string) => ({
    node_id: id,
    logical_name: id,
    name: id,
    content_type: "text/plain",
    size_bytes: 10,
    fixed: false,
    read: false,
  });
  let resolve!: (v: Awaited<ReturnType<typeof runApi.listRunResources>>) => void;
  const list = vi
    .spyOn(runApi, "listRunResources")
    .mockResolvedValueOnce({ count: 1, next: "cursor", candidates: [candidate("first")] })
    .mockImplementationOnce(
      () =>
        new Promise((r) => {
          resolve = r;
        }),
    );
  const view = render(<RunResources runId="A" terminal />);
  const more = await screen.findByRole("button", { name: "加载更多资料" });
  fireEvent.click(more);
  fireEvent.click(more);
  expect(list).toHaveBeenCalledTimes(2);
  await act(async () =>
    resolve({ count: 2, next: null, candidates: [candidate("first"), candidate("second")] }),
  );
  expect(screen.getAllByRole("button", { name: "first" })).toHaveLength(1);
  expect(screen.getByRole("button", { name: "second" })).toBeInTheDocument();
  view.unmount();
});
it("does not save an output after switching away while its metadata is loading", async () => {
  vi.spyOn(runApi, "listRunResources").mockResolvedValue({ count: 0, next: null, candidates: [] });
  vi.mocked(runApi.listRunPublishedFiles).mockResolvedValue({
    files: [{ file_id: "file-only", name: "output.txt", sha256: "safe" }],
  });
  let resolve!: (v: Awaited<ReturnType<typeof runApi.getFile>>) => void;
  vi.spyOn(runApi, "getFile").mockImplementation(
    () =>
      new Promise((r) => {
        resolve = r;
      }),
  );
  const save = vi.fn();
  const view = render(<RunResources runId="A" terminal onSaveFile={save} />);
  fireEvent.click(await screen.findByRole("button", { name: "保存到空间" }));
  view.unmount();
  useRunInspector.getState().select("B");
  await act(async () =>
    resolve({ file: { file_id: "file-only" } } as Awaited<ReturnType<typeof runApi.getFile>>),
  );
  await waitFor(() => expect(save).not.toHaveBeenCalled());
});
it("renders all approval states and only offers decisions for pending approvals", async () => {
  const statuses = ["pending", "approved", "rejected", "expired", "cancelled", "consumed"] as const;
  vi.mocked(runApi.listRunFileApprovals).mockResolvedValue({
    approvals: statuses.map((status) => ({
      approval_id: status,
      run_id: "A",
      operation_id: `op-${status}`,
      action_summary: `操作-${status}`,
      tool_name: "write",
      status,
      expires_at: "2099-01-01T00:00:00Z",
    })),
  });
  vi.spyOn(runApi, "listRunResources").mockResolvedValue({ count: 0, next: null, candidates: [] });
  render(<RunResources runId="A" terminal />);
  await screen.findByText("操作-consumed");
  expect(screen.getByText("授权已使用，请查看执行结果")).toBeInTheDocument();
  expect(screen.getByText("已授权，等待执行状态更新")).toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: "允许" })).toHaveLength(1);
  expect(screen.getAllByRole("button", { name: "拒绝" })).toHaveLength(1);
});

it("opens only real candidate node IDs and preserves the Run return stack", async () => {
  useShell.getState().openInspector({ kind: "run", objectId: "A", tab: "resources" });
  vi.spyOn(runApi, "listRunResources").mockResolvedValue({
    count: 1,
    next: null,
    candidates: [
      {
        node_id: "real-node",
        logical_name: "真实候选",
        name: "候选",
        content_type: null,
        size_bytes: null,
        fixed: false,
        read: false,
      },
    ],
  });
  render(<RunResources runId="A" terminal />);
  fireEvent.click(await screen.findByRole("button", { name: "真实候选" }));
  expect(useShell.getState().route.inspector).toMatchObject({
    kind: "file",
    objectId: "real-node",
  });
  expect(useShell.getState().backStack).toEqual([{ kind: "run", objectId: "A", tab: "resources" }]);
  act(() => useShell.getState().back());
  expect(useShell.getState().route.inspector).toMatchObject({
    kind: "run",
    objectId: "A",
    tab: "resources",
  });
});

it("refreshes an initially empty output list when the same Run completes", async () => {
  vi.spyOn(runApi, "listRunResources").mockResolvedValue({ count: 0, next: null, candidates: [] });
  vi.mocked(runApi.listRunPublishedFiles)
    .mockResolvedValueOnce({ files: [] })
    .mockResolvedValue({ files: [{ file_id: "result", name: "result.txt", sha256: "safe" }] });
  const view = render(<RunResources runId="A" terminal={false} />);
  await screen.findByText("暂无已发布输出。");
  view.rerender(<RunResources runId="A" terminal />);
  expect(await screen.findByRole("link", { name: "result.txt" })).toHaveAttribute(
    "href",
    "/api/v1/files/result/content",
  );
  expect(runApi.listRunPublishedFiles).toHaveBeenCalledTimes(2);
});
it("ignores an old output list arriving after the terminal refresh", async () => {
  vi.spyOn(runApi, "listRunResources").mockResolvedValue({ count: 0, next: null, candidates: [] });
  let resolve!: (v: Awaited<ReturnType<typeof runApi.listRunPublishedFiles>>) => void;
  vi.mocked(runApi.listRunPublishedFiles)
    .mockImplementationOnce(
      () =>
        new Promise((r) => {
          resolve = r;
        }),
    )
    .mockResolvedValue({ files: [{ file_id: "new", name: "new.txt", sha256: "safe" }] });
  const view = render(<RunResources runId="A" terminal={false} />);
  await waitFor(() => expect(runApi.listRunPublishedFiles).toHaveBeenCalledTimes(1));
  view.rerender(<RunResources runId="A" terminal />);
  await screen.findByRole("link", { name: "new.txt" });
  await act(async () => resolve({ files: [] }));
  expect(screen.getByRole("link", { name: "new.txt" })).toBeInTheDocument();
  expect(screen.queryByText("暂无已发布输出。")).not.toBeInTheDocument();
});
it("retains outputs on a network error and recovers through explicit refresh", async () => {
  vi.spyOn(runApi, "listRunResources").mockResolvedValue({ count: 0, next: null, candidates: [] });
  vi.mocked(runApi.listRunPublishedFiles)
    .mockResolvedValueOnce({ files: [{ file_id: "old", name: "old.txt", sha256: "safe" }] })
    .mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValue({ files: [{ file_id: "new", name: "new.txt", sha256: "safe" }] });
  render(<RunResources runId="A" terminal />);
  await screen.findByRole("link", { name: "old.txt" });
  fireEvent.click(screen.getByRole("button", { name: "刷新输出" }));
  await screen.findByText("输出暂时无法同步。");
  expect(screen.getByRole("link", { name: "old.txt" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "重试输出" }));
  await screen.findByRole("link", { name: "new.txt" });
  expect(screen.queryByText("输出暂时无法同步。")).not.toBeInTheDocument();
});
