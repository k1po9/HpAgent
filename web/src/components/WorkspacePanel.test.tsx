import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { HpApi } from "../api/resources";
import { WorkspacePanel } from "./WorkspacePanel";

type Resources = Awaited<ReturnType<HpApi["listRunResources"]>>;
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
const candidates = (name: string, next: string | null = null): Resources => ({
  count: 1,
  next,
  candidates: [
    {
      node_id: name,
      logical_name: name,
      name,
      content_type: "text/plain",
      size_bytes: 1,
      fixed: false,
      read: false,
    },
  ],
});
const props = {
  accountId: "a",
  conversationId: "c1",
  currentRunId: "r1",
  candidateRunId: "r1",
  currentRunStatus: "running" as const,
  refreshSignal: 0,
  onSelectDirectory: vi.fn(),
};

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(HpApi.prototype, "getWorkspace").mockResolvedValue({
    workspace_id: "w",
    root_id: "root",
    nodes: [
      {
        node_id: "root",
        parent_id: null,
        kind: "directory",
        name: "",
        file_id: null,
        destination_id: null,
        revision: null,
        source: null,
      },
      {
        node_id: "f",
        parent_id: "root",
        kind: "file",
        name: "notes.txt",
        file_id: "file",
        destination_id: "dest",
        revision: 1,
        source: null,
      },
    ],
  });
  vi.spyOn(HpApi.prototype, "getWorkspaceSpace").mockResolvedValue({
    physical_files: 1,
    physical_bytes: 1,
    files_with_active_entry: 1,
    bytes_with_active_entry: 1,
  });
  vi.spyOn(HpApi.prototype, "listConversationResources").mockResolvedValue({
    grants: [],
    attachments: [],
  });
  vi.spyOn(HpApi.prototype, "listRunResources").mockResolvedValue(candidates("current"));
});

describe("Workspace authority lifecycle", () => {
  it("drops late candidates and failures after cancellation without losing the version Run", async () => {
    const pending = deferred<Resources>();
    const list = vi.mocked(HpApi.prototype.listRunResources).mockReturnValue(pending.promise);
    const { rerender } = render(<WorkspacePanel {...props} />);
    await waitFor(() => expect(list).toHaveBeenCalledWith("r1"));
    // cancelling and cancelled both retain currentRunId but have no selectable Run.
    rerender(<WorkspacePanel {...props} candidateRunId={null} currentRunStatus="cancelling" />);
    await screen.findByText("当前没有可选择资料的活动执行。");
    await act(async () => pending.reject(new Error("404")));
    rerender(<WorkspacePanel {...props} candidateRunId={null} currentRunStatus="cancelled" />);
    expect(screen.queryByText("资源授权加载失败")).not.toBeInTheDocument();
    expect(list).toHaveBeenCalledTimes(1);

    rerender(
      <WorkspacePanel
        {...props}
        currentRunId="completed-run"
        candidateRunId={null}
        currentRunStatus="succeeded"
      />,
    );

    vi.spyOn(HpApi.prototype, "getWorkspaceVersions").mockResolvedValue({
      current: {
        node_id: "f",
        destination_id: "dest",
        revision: 1,
        file_id: "file",
        sha256: "hash",
      },
      revisions: [],
    });
    const outputs = vi
      .spyOn(HpApi.prototype, "listRunPublishedFiles")
      .mockResolvedValue({ files: [] });
    fireEvent.click(screen.getByRole("button", { name: /notes.txt/ }));
    fireEvent.click(await screen.findByRole("button", { name: "选择 Run 已发布输出" }));
    await waitFor(() => expect(outputs).toHaveBeenCalledWith("completed-run"));
  });

  it("does not restore old candidates after leaving a running Run", async () => {
    const pending = deferred<Resources>();
    const list = vi.mocked(HpApi.prototype.listRunResources).mockReturnValue(pending.promise);
    const { rerender } = render(<WorkspacePanel {...props} />);
    await waitFor(() => expect(list).toHaveBeenCalled());
    rerender(<WorkspacePanel {...props} candidateRunId={null} />);
    await act(async () => pending.resolve(candidates("old-candidate")));
    expect(screen.queryByText(/old-candidate/)).not.toBeInTheDocument();
  });

  it("fences delayed authority responses across A → B → A", async () => {
    type ConversationResources = Awaited<ReturnType<HpApi["listConversationResources"]>>;
    const old = deferred<ConversationResources>();
    const conversations = vi
      .mocked(HpApi.prototype.listConversationResources)
      .mockReturnValueOnce(old.promise);
    const { rerender } = render(<WorkspacePanel {...props} />);
    await waitFor(() => expect(conversations).toHaveBeenCalledTimes(1));
    rerender(<WorkspacePanel {...props} conversationId="c2" candidateRunId="r2" />);
    await waitFor(() => expect(conversations).toHaveBeenCalledWith("c2"));
    rerender(<WorkspacePanel {...props} />);
    await waitFor(() => expect(conversations).toHaveBeenCalledTimes(3));
    await act(async () =>
      old.resolve({
        grants: [],
        attachments: [{ file_id: "stale", name: "old-attachment", available: true }],
      }),
    );
    expect(screen.queryByText("old-attachment")).not.toBeInTheDocument();
    expect(HpApi.prototype.listRunResources).toHaveBeenCalledTimes(2);
  });

  it("keeps an unknown active-run 404 visible", async () => {
    vi.mocked(HpApi.prototype.listRunResources).mockRejectedValue(new Error("404"));
    render(<WorkspacePanel {...props} />);
    expect(await screen.findByText("资源授权加载失败")).toBeInTheDocument();
  });

  it("refreshes candidates when the same queued Run starts running", async () => {
    const list = vi
      .mocked(HpApi.prototype.listRunResources)
      .mockRejectedValueOnce(new Error("snapshot not ready"))
      .mockResolvedValue(candidates("ready-candidate"));
    const { rerender } = render(<WorkspacePanel {...props} currentRunStatus="queued" />);
    await screen.findByText("资源授权加载失败");
    rerender(<WorkspacePanel {...props} currentRunStatus="running" />);
    await screen.findByText(/ready-candidate/);
    expect(screen.queryByText("资源授权加载失败")).not.toBeInTheDocument();
    expect(list).toHaveBeenCalledTimes(2);
  });

  it("drops a late candidate page after a conversation switch", async () => {
    const page = deferred<Resources>();
    const list = vi
      .mocked(HpApi.prototype.listRunResources)
      .mockResolvedValueOnce(candidates("first", "cursor"))
      .mockReturnValueOnce(page.promise);
    const { rerender } = render(<WorkspacePanel {...props} />);
    fireEvent.click(await screen.findByRole("button", { name: "加载更多候选" }));
    await waitFor(() => expect(list).toHaveBeenCalledWith("r1", "cursor"));
    rerender(<WorkspacePanel {...props} conversationId="c2" candidateRunId={null} />);
    await act(async () => page.resolve(candidates("late-page")));
    expect(screen.queryByText(/late-page/)).not.toBeInTheDocument();
  });
});

it("creates inside the selected directory with a separate empty name and preserves selection", async () => {
  const base = await HpApi.prototype.getWorkspace();
  const directory = { ...base.nodes[0]!, node_id: "docs", parent_id: "root", name: "资料" };
  const child = { ...directory, node_id: "child", parent_id: "docs", name: "项目" };
  vi.mocked(HpApi.prototype.getWorkspace).mockResolvedValue({
    ...base,
    nodes: [...base.nodes, directory],
  });
  const create = vi
    .spyOn(HpApi.prototype, "createWorkspaceDirectory")
    .mockImplementation(async () => {
      vi.mocked(HpApi.prototype.getWorkspace).mockResolvedValue({
        ...base,
        nodes: [...base.nodes, directory, child],
      });
      return { node_id: "child" };
    });
  const { rerender } = render(<WorkspacePanel {...props} />);
  fireEvent.click(await screen.findByRole("button", { name: "📁 资料" }));
  expect(screen.getByLabelText("新目录名称")).toHaveValue("");
  expect(screen.getByLabelText("Workspace 名称")).toHaveValue("资料");
  fireEvent.change(screen.getByLabelText("新目录名称"), { target: { value: "项目" } });
  fireEvent.click(screen.getByRole("button", { name: "新建目录" }));
  await screen.findByText("所选位置：/资料/项目");
  expect(create).toHaveBeenCalledWith("docs", "项目");
  rerender(<WorkspacePanel {...props} refreshSignal={1} />);
  await waitFor(() => expect(screen.getByLabelText("Workspace 名称")).toHaveValue("项目"));
});

it("keeps a saved upload after authorization failure and retries only the authorization", async () => {
  const ready = {
    file_id: "new-file",
    file_name: "uploaded.txt",
    status: "ready" as const,
    purpose: "input" as const,
    size_bytes: 5,
    content_type: "text/plain",
    encoding: "utf-8",
    sha256: null,
    failure_code: null,
    download_url: null,
  };
  const upload = vi
    .spyOn(HpApi.prototype, "createWorkspaceUpload")
    .mockResolvedValue({ file: ready, content_url: "/content" });
  vi.spyOn(HpApi.prototype, "uploadContent").mockResolvedValue(ready);
  const save = vi
    .spyOn(HpApi.prototype, "saveWorkspaceFile")
    .mockResolvedValue({ node_id: "uploaded" });
  const grant = vi
    .spyOn(HpApi.prototype, "grantConversationResource")
    .mockRejectedValueOnce(new Error("temporary"))
    .mockResolvedValue({ grant_ids: ["g"] });
  render(<WorkspacePanel {...props} />);
  await screen.findByRole("button", { name: "根目录" });
  fireEvent.change(screen.getByLabelText("上传用途"), { target: { value: "conversation" } });
  fireEvent.change(screen.getByLabelText("上传到 Workspace"), {
    target: { files: [new File(["hello"], "uploaded.txt", { type: "text/plain" })] },
  });
  await screen.findByText(/已保存，授权未完成/);
  fireEvent.click(screen.getByRole("button", { name: "补授权：uploaded.txt" }));
  await screen.findByText("文件已授权，下一次执行可读取。");
  expect(upload).toHaveBeenCalledTimes(1);
  expect(save).toHaveBeenCalledTimes(1);
  expect(grant).toHaveBeenCalledTimes(2);
  expect(grant).toHaveBeenLastCalledWith(
    "c1",
    "uploaded",
    ["list_metadata", "read_content"],
    false,
  );
});
