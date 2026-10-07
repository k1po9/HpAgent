import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { useWorkspace, workspaceApi } from "../../store/workspace";
import { HpCommandError, type HpWorkspaceNode } from "../../api/types";
import { WorkspaceMutationDialog } from "./WorkspaceMutationDialog";
import { FileVersions } from "./FileVersions";
import { SaveToWorkspaceDialog } from "./SaveToWorkspaceDialog";
import { UseInConversationDialog } from "./UseInConversationDialog";
import { resetWorkspaceOperations } from "./workspaceOperations";
import { useWorkbench } from "../../store/workbench";
const node: HpWorkspaceNode = {
  node_id: "n",
  parent_id: "root",
  kind: "file",
  name: "notes.txt",
  file_id: "f",
  destination_id: "dest",
  revision: 1,
  source: null,
};
const root: HpWorkspaceNode = {
  ...node,
  node_id: "root",
  parent_id: null,
  kind: "directory",
  name: "",
  file_id: null,
  destination_id: null,
  revision: null,
};
function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { resolve, promise };
}
beforeEach(() => {
  vi.restoreAllMocks();
  useWorkspace.getState().reset();
  resetWorkspaceOperations();
  vi.spyOn(workspaceApi, "getWorkspace").mockResolvedValue({
    workspace_id: "w",
    root_id: "root",
    nodes: [root, node],
  });
  vi.spyOn(useWorkbench.getState(), "loadConversations").mockResolvedValue();
  vi.spyOn(useWorkbench.getState(), "refreshActiveRun").mockResolvedValue();
});
it("does not authorize a late impact preview after any parameter changes", async () => {
  await useWorkspace.getState().loadTree();
  const old = deferred<{ preview_token: string; potentially_affected_runs: string[] }>();
  vi.spyOn(workspaceApi, "previewWorkspaceNode").mockReturnValue(old.promise);
  render(<WorkspaceMutationDialog node={node} onClose={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "预览改名或移动影响" }));
  // Inputs are disabled during the preview; a tree change independently invalidates it.
  useWorkspace.getState().invalidateQueries();
  await act(async () => old.resolve({ preview_token: "old", potentially_affected_runs: [] }));
  expect(screen.queryByRole("button", { name: "确认改名或移动" })).not.toBeInTheDocument();
});
it("clears impact on name/action changes and never automatically retries an expired token", async () => {
  await useWorkspace.getState().loadTree();
  const read = vi
    .spyOn(workspaceApi, "previewWorkspaceNode")
    .mockResolvedValue({ preview_token: "old", potentially_affected_runs: [] });
  const move = vi.spyOn(workspaceApi, "moveWorkspaceNode").mockRejectedValue(
    new HpCommandError(409, {
      code: "workspace_preview_expired",
      message: "过期",
      request_id: null,
      retryable: true,
      details: {},
    }),
  );
  render(<WorkspaceMutationDialog node={node} onClose={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "预览改名或移动影响" }));
  await screen.findByRole("button", { name: "确认改名或移动" });
  fireEvent.change(screen.getByLabelText("Workspace 名称"), { target: { value: "new.txt" } });
  expect(screen.queryByRole("button", { name: "确认改名或移动" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "预览改名或移动影响" }));
  fireEvent.click(await screen.findByRole("button", { name: "确认改名或移动" }));
  await screen.findByText(/过期/);
  expect(move).toHaveBeenCalledTimes(1);
  expect(read).toHaveBeenCalledTimes(2);
  expect(screen.queryByRole("button", { name: "确认改名或移动" })).not.toBeInTheDocument();
});
it("rejects late published outputs for a different Run and exposes real save on CAS conflict", async () => {
  vi.spyOn(workspaceApi, "getWorkspaceVersions").mockResolvedValue({
    current: { node_id: "n", destination_id: "dest", revision: 1, file_id: "f", sha256: "hash" },
    revisions: [],
  });
  const old = deferred<{ files: Array<{ file_id: string; name: string; sha256: string }> }>();
  vi.spyOn(workspaceApi, "listRunPublishedFiles")
    .mockReturnValueOnce(old.promise)
    .mockResolvedValueOnce({ files: [{ file_id: "new", name: "new.txt", sha256: "newhash" }] });
  const update = vi.spyOn(workspaceApi, "updateWorkspaceFile").mockRejectedValue(
    new HpCommandError(409, {
      code: "workspace_version_conflict",
      message: "版本冲突",
      request_id: null,
      retryable: false,
      details: {},
    }),
  );
  render(<FileVersions node={node} />);
  const input = await screen.findByLabelText("编辑 Run ID");
  fireEvent.change(input, { target: { value: "A" } });
  fireEvent.click(screen.getByRole("button", { name: "选择 Run 已发布输出" }));
  await waitFor(() => expect(workspaceApi.listRunPublishedFiles).toHaveBeenCalledWith("A"));
  fireEvent.change(input, { target: { value: "B" } });
  fireEvent.click(screen.getByRole("button", { name: "选择 Run 已发布输出" }));
  await screen.findByRole("option", { name: "new.txt" });
  await act(async () =>
    old.resolve({ files: [{ file_id: "old", name: "old.txt", sha256: "old" }] }),
  );
  expect(screen.queryByRole("option", { name: "old.txt" })).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Run 已发布输出"), { target: { value: "new" } });
  fireEvent.click(screen.getByRole("button", { name: "提交新版本" }));
  fireEvent.click(await screen.findByRole("button", { name: "将所选输出另存到空间" }));
  await screen.findByRole("dialog", { name: "保存到长期文件" });
  expect(screen.getByLabelText("保存名称")).toHaveValue("new.txt");
  expect(update).toHaveBeenCalledWith("n", "B", "new", 1, "hash", expect.any(String));
});
it("cannot close or notify the new save view with an old source's completion", async () => {
  await useWorkspace.getState().loadTree();
  const old = deferred<{ node_id: string }>();
  vi.spyOn(workspaceApi, "saveWorkspaceFile").mockReturnValue(old.promise);
  const close = vi.fn(),
    saved = vi.fn();
  const view = render(
    <SaveToWorkspaceDialog
      file={{ file_id: "A", file_name: "A.txt" }}
      onClose={close}
      onSaved={saved}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "确认保存" }));
  view.rerender(
    <SaveToWorkspaceDialog
      file={{ file_id: "B", file_name: "B.txt" }}
      onClose={close}
      onSaved={saved}
    />,
  );
  await act(async () => old.resolve({ node_id: "saved-A" }));
  expect(close).not.toHaveBeenCalled();
  expect(saved).not.toHaveBeenCalled();
  expect(screen.getByLabelText("保存名称")).toHaveValue("B.txt");
});
it("creates a new conversation once even when authorization fails", async () => {
  const create = vi.spyOn(workspaceApi, "createConversation").mockResolvedValue({
    conversation: {
      conversation_id: "new",
      title: "New",
      status: "active",
      last_message_seq: 0,
      metadata_version: 1,
      created_at: "",
      updated_at: "",
    },
  });
  vi.spyOn(workspaceApi, "listConversationResources").mockResolvedValue({
    grants: [],
    attachments: [],
  });
  const grant = vi
    .spyOn(workspaceApi, "grantConversationResource")
    .mockRejectedValue(new Error("offline"));
  render(<UseInConversationDialog node={node} onClose={vi.fn()} />);
  fireEvent.click(screen.getByLabelText("创建新对话"));
  fireEvent.click(screen.getByRole("button", { name: "确认读取授权并进入对话" }));
  await screen.findByText("offline");
  fireEvent.click(screen.getByRole("button", { name: "确认读取授权并进入对话" }));
  await waitFor(() => expect(grant).toHaveBeenCalledTimes(2));
  expect(create).toHaveBeenCalledTimes(1);
});
