import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { useWorkspace, workspaceApi } from "../../store/workspace";
import { useWorkbench } from "../../store/workbench";
import { useShell } from "../../store/shell";
import { useAuth } from "../../store/auth";
import { WorkspaceScreen } from "./WorkspaceScreen";
import { GrantEditor } from "./GrantEditor";
import { resetWorkspaceOperations, startSave, resumeSave } from "./workspaceOperations";
import type { HpWorkspaceNode, HpFile } from "../../api/types";
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
  useWorkspace.getState().reset();
  useShell.getState().reset();
  resetWorkspaceOperations();
  useAuth.setState({ capabilities: { file_upload: true } });
  vi.spyOn(workspaceApi, "getWorkspace").mockResolvedValue({
    workspace_id: "w",
    root_id: "root",
    nodes: [root, dir],
  });
  vi.spyOn(workspaceApi, "getWorkspaceSpace").mockResolvedValue({
    physical_files: 0,
    physical_bytes: 0,
    files_with_active_entry: 0,
    bytes_with_active_entry: 0,
  });
  vi.spyOn(useWorkbench.getState(), "refreshActiveRun").mockResolvedValue();
});
it("migrates directory creation to the selected formal directory and keeps its selection on refresh", async () => {
  useShell.setState({ route: { screen: "workspace", directoryId: "docs" } });
  const create = vi.spyOn(workspaceApi, "createWorkspaceDirectory").mockImplementation(async () => {
    vi.mocked(workspaceApi.getWorkspace).mockResolvedValue({
      workspace_id: "w",
      root_id: "root",
      nodes: [root, dir, { ...dir, node_id: "child", parent_id: "docs", name: "项目" }],
    });
    return { node_id: "child" };
  });
  render(<WorkspaceScreen />);
  await screen.findByRole("button", { name: "资料" });
  expect(screen.getByLabelText("新目录名称")).toHaveValue("");
  fireEvent.change(screen.getByLabelText("新目录名称"), { target: { value: "项目" } });
  fireEvent.click(screen.getByRole("button", { name: "新建目录" }));
  await screen.findByText("目录已创建。");
  expect(create).toHaveBeenCalledWith("docs", "项目");
  fireEvent.click(await screen.findByRole("button", { name: "项目" }));
  expect(useShell.getState().route.directoryId).toBe("child");
  fireEvent.click(screen.getByRole("button", { name: "刷新空间" }));
  expect(useShell.getState().route.directoryId).toBe("child");
});
it("migrates saved-upload partial success: retries authorization without reuploading or resaving", async () => {
  const ready: HpFile = {
    file_id: "new",
    file_name: "notes.txt",
    status: "ready",
    purpose: "input",
    size_bytes: 5,
    content_type: "text/plain",
    encoding: "utf-8",
    sha256: null,
    failure_code: null,
    download_url: null,
  };
  const upload = vi
    .spyOn(workspaceApi, "createWorkspaceUpload")
    .mockResolvedValue({ file: ready, content_url: "/content" });
  vi.spyOn(workspaceApi, "uploadContent").mockResolvedValue(ready);
  const save = vi.spyOn(workspaceApi, "saveWorkspaceFile").mockResolvedValue({ node_id: "saved" });
  vi.spyOn(workspaceApi, "listConversationResources").mockResolvedValue({
    grants: [],
    attachments: [],
  });
  const grant = vi
    .spyOn(workspaceApi, "grantConversationResource")
    .mockRejectedValueOnce(new Error("temporary"))
    .mockResolvedValue({ grant_ids: ["g"] });
  const id = startSave(
    new File(["hello"], "notes.txt", { type: "text/plain" }),
    "docs",
    "notes.txt",
    { kind: "conversation", id: "A", title: "A" },
  );
  await resumeSave(id);
  await resumeSave(id);
  expect(upload).toHaveBeenCalledTimes(1);
  expect(save).toHaveBeenCalledTimes(1);
  expect(grant).toHaveBeenCalledTimes(2);
  expect(grant).toHaveBeenLastCalledWith("A", "saved", ["list_metadata", "read_content"], false);
});
it("migrates authority scope fencing: a delayed A response cannot populate B, and reset fences reopened A", async () => {
  let resolve!: (v: Awaited<ReturnType<typeof workspaceApi.listConversationResources>>) => void;
  vi.spyOn(workspaceApi, "listConversationResources")
    .mockReturnValueOnce(
      new Promise((done) => {
        resolve = done;
      }),
    )
    .mockResolvedValue({ grants: [], attachments: [] });
  useWorkspace.setState({ tree: { workspace_id: "w", root_id: "root", nodes: [root, dir] } });
  const view = render(
    <GrantEditor node={dir} subject={{ kind: "conversation", id: "A", title: "A" }} />,
  );
  await waitFor(() => expect(workspaceApi.listConversationResources).toHaveBeenCalledWith("A"));
  view.rerender(<GrantEditor node={dir} subject={{ kind: "conversation", id: "B", title: "B" }} />);
  await waitFor(() => expect(workspaceApi.listConversationResources).toHaveBeenCalledWith("B"));
  act(() => useWorkspace.getState().reset());
  view.rerender(<GrantEditor node={dir} subject={{ kind: "conversation", id: "A", title: "A" }} />);
  await waitFor(() =>
    expect(
      vi.mocked(workspaceApi.listConversationResources).mock.calls.filter(([id]) => id === "A"),
    ).toHaveLength(2),
  );
  await act(async () =>
    resolve({
      grants: [
        { grant_id: "stale", node_id: "docs", operations: ["read_content"], recursive: true },
      ] as never,
      attachments: [],
    }),
  );
  expect(screen.queryByRole("button", { name: /撤销.*stale/ })).not.toBeInTheDocument();
  expect(useWorkspace.getState().cache["grants:conversation:A"]?.value).toEqual({
    grants: [],
    attachments: [],
  });
});
