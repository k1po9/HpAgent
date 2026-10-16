import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { useWorkspace, workspaceApi } from "../../store/workspace";
import { useShell } from "../../store/shell";
import { WorkspaceSidebar } from "./WorkspaceSidebar";
import { FileList } from "./FileList";
import { WorkspaceMutationDialog } from "./WorkspaceMutationDialog";
import type { HpWorkspaceNode } from "../../api/types";
const root: HpWorkspaceNode = {
  node_id: "root",
  parent_id: null,
  kind: "directory",
  name: "",
  file_id: null,
  destination_id: null,
  revision: null,
  source: null,
};
const dir = { ...root, node_id: "docs", parent_id: "root", name: "资料" };
beforeEach(() => {
  vi.restoreAllMocks();
  useShell.getState().reset();
  useWorkspace.getState().reset();
  vi.spyOn(workspaceApi, "getWorkspace").mockResolvedValue({
    workspace_id: "w",
    root_id: "root",
    nodes: [root, dir],
  });
  useShell.setState({
    route: { screen: "workspace", directoryId: "root" },
    dirtyResourceEditor: "grant",
  });
  useWorkspace.setState({
    tree: { workspace_id: "w", root_id: "root", nodes: [root, dir] },
    allFiles: true,
    searchMode: true,
  });
});
it("keeps directory filters until sidebar navigation is approved", async () => {
  render(<WorkspaceSidebar />);
  fireEvent.click(screen.getByRole("button", { name: "资料" }));
  expect(useWorkspace.getState().allFiles).toBe(true);
  expect(useWorkspace.getState().searchMode).toBe(true);
  expect(useShell.getState().route.directoryId).toBe("root");
  const resume = useShell.getState().pendingResourceChange;
  expect(resume).toBeTypeOf("function");
  act(() => {
    useShell.setState({ dirtyResourceEditor: null, pendingResourceChange: null });
    resume?.();
  });
  expect(useWorkspace.getState().allFiles).toBe(false);
  expect(useShell.getState().route.directoryId).toBe("docs");
});
it("keeps list filters until directory navigation is approved", () => {
  render(<FileList nodes={[dir]} />);
  fireEvent.click(screen.getByRole("button", { name: "资料" }));
  expect(useWorkspace.getState().allFiles).toBe(true);
  expect(useWorkspace.getState().searchMode).toBe(true);
  expect(useShell.getState().route.directoryId).toBe("root");
});
it("requires an explicit discard when closing a renamed entry", async () => {
  const close = vi.fn();
  render(<WorkspaceMutationDialog node={dir} onClose={close} />);
  fireEvent.change(screen.getByLabelText("Workspace 名称"), { target: { value: "新资料" } });
  fireEvent.click(screen.getByRole("button", { name: "关闭管理空间入口" }));
  expect(close).not.toHaveBeenCalled();
  expect(await screen.findByRole("dialog", { name: "放弃未提交的空间修改？" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "继续编辑" }));
  expect(screen.getByLabelText("Workspace 名称")).toHaveValue("新资料");
});

it("blocks route navigation for an unsaved directory and preserves it on cancel", async () => {
  const { WorkspaceScreen } = await import("./WorkspaceScreen");
  vi.spyOn(workspaceApi, "getWorkspaceSpace").mockResolvedValue({
    physical_files: 0,
    physical_bytes: 0,
    files_with_active_entry: 0,
    bytes_with_active_entry: 0,
  });
  useShell.setState({ dirtyResourceEditor: null });
  useWorkspace.setState({ searchMode: false });
  render(<WorkspaceScreen />);
  fireEvent.change(screen.getByLabelText("新目录名称"), { target: { value: "未保存目录" } });
  act(() => {
    useShell.getState().navigate({ screen: "tasks" });
  });
  expect(useShell.getState().route.screen).toBe("workspace");
  expect(useShell.getState().pendingRouteReason).toBe("directory");
  act(() => {
    useShell.setState({ pendingRoute: null });
  });
  expect(screen.getByLabelText("新目录名称")).toHaveValue("未保存目录");
  act(() => {
    const discard = useShell.getState().dirtyDirectoryEditor;
    useShell.setState({ dirtyDirectoryEditor: null });
    discard?.();
    useShell.getState().navigate({ screen: "tasks" });
  });
  expect(useShell.getState().route.screen).toBe("tasks");
});

it("protects a selected upload before any operation is submitted", async () => {
  const { UploadToWorkspaceDialog } = await import("./UploadToWorkspaceDialog");
  const { useAuth } = await import("../../store/auth");
  useAuth.setState({ capabilities: { file_upload: true } });
  const close = vi.fn();
  render(<UploadToWorkspaceDialog directoryId="root" onClose={close} />);
  fireEvent.change(screen.getByLabelText("上传长期文件"), {
    target: { files: [new File(["draft"], "draft.txt", { type: "text/plain" })] },
  });
  fireEvent.click(screen.getByRole("button", { name: "关闭上传到空间" }));
  expect(close).not.toHaveBeenCalled();
  expect(await screen.findByRole("dialog", { name: "放弃未提交的上传选择？" })).toBeInTheDocument();
});
it("protects changed save choices before submission", async () => {
  const { SaveToWorkspaceDialog } = await import("./SaveToWorkspaceDialog");
  const close = vi.fn();
  render(
    <SaveToWorkspaceDialog
      file={{ file_id: "new", file_name: "notes.txt" }}
      onClose={close}
      onSaved={vi.fn()}
    />,
  );
  fireEvent.change(screen.getByLabelText("保存名称"), { target: { value: "changed.txt" } });
  fireEvent.click(screen.getByRole("button", { name: "关闭保存到长期文件" }));
  expect(close).not.toHaveBeenCalled();
  expect(await screen.findByRole("dialog", { name: "放弃未提交的保存选择？" })).toBeInTheDocument();
});
