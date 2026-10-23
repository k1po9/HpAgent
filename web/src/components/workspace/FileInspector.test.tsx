import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { useWorkspace, workspaceApi } from "../../store/workspace";
import { useShell } from "../../store/shell";
import { FileInspector } from "./FileInspector";
import { FilePreview } from "./FilePreview";
import { GrantEditor } from "./GrantEditor";
import { compareNodes, canPreview, workspaceIndex } from "./workspacePresentation";
import type { HpFile, HpWorkspaceNode } from "../../api/types";
const root: HpWorkspaceNode = {
  node_id: "root",
  parent_id: null,
  name: "",
  kind: "directory",
  file_id: null,
  revision: null,
  destination_id: null,
  source: null,
};
const folder = { ...root, node_id: "dir", parent_id: "root", name: "Docs" };
const file: HpWorkspaceNode = {
  ...root,
  node_id: "node",
  parent_id: "dir",
  kind: "file",
  name: "unsafe.html",
  file_id: "f",
};
const metadata: HpFile = {
  file_id: "f",
  file_name: "unsafe.html",
  purpose: "input",
  status: "ready",
  content_type: "text/html",
  size_bytes: 20,
  encoding: "utf-8",
  sha256: "sha",
  failure_code: null,
  download_url: null,
};
beforeEach(() => {
  vi.restoreAllMocks();
  useWorkspace.getState().reset();
  useShell.getState().reset();
  vi.spyOn(workspaceApi, "getWorkspace").mockResolvedValue({
    workspace_id: "w",
    root_id: "root",
    nodes: [root, folder, file],
  });
  vi.spyOn(workspaceApi, "getFile").mockResolvedValue({ file: metadata });
  vi.spyOn(workspaceApi, "getWorkspaceTrace").mockResolvedValue({ artifact: null } as never);
  vi.spyOn(workspaceApi, "readFileText").mockResolvedValue("<script>window.unsafe = true</script>");
});
it("opens the exact persisted interactive Artifact from a saved file without executing file HTML", async () => {
  vi.mocked(workspaceApi.getWorkspaceTrace).mockResolvedValue({
    artifact: { artifact_id: "a", artifact_version_id: "v2", version: 2 },
    source_work_id: "work",
    source_run_id: "run",
  } as never);
  render(<FileInspector inspector={{ kind: "file", objectId: "node" }} />);
  fireEvent.click(await screen.findByRole("button", { name: "打开交互成果 v2" }));
  expect(useShell.getState().route.inspector).toMatchObject({
    kind: "artifact",
    objectId: "a",
    versionId: "v2",
    origin: { workId: "work", runId: "run" },
  });
  expect(document.querySelector("iframe")).toBeNull();
});
it("renders HTML as text and never creates executable elements", async () => {
  const { container } = render(<FileInspector inspector={{ kind: "file", objectId: "node" }} />);
  await screen.findByText("<script>window.unsafe = true</script>");
  expect(container.querySelector("script, iframe")).toBeNull();
});
it("only fetches metadata/preview initially and never file APIs for a directory", async () => {
  const trace = vi.spyOn(workspaceApi, "getWorkspaceTrace");
  const versions = vi.spyOn(workspaceApi, "getWorkspaceVersions");
  const retention = vi.spyOn(workspaceApi, "getFileRetention");
  render(<FileInspector inspector={{ kind: "file", objectId: "dir" }} />);
  await screen.findByText("目录：Docs");
  expect(workspaceApi.getFile).not.toHaveBeenCalled();
  expect(workspaceApi.readFileText).not.toHaveBeenCalled();
  expect(trace).not.toHaveBeenCalled();
  expect(versions).not.toHaveBeenCalled();
  expect(retention).not.toHaveBeenCalled();
});
it.each([
  { content_type: "application/pdf" },
  { content_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" },
  { size_bytes: null },
  { size_bytes: 1024 * 1024 + 1 },
  { encoding: "gbk" },
])("falls back to metadata and download for unsupported metadata %j", async (patch) => {
  vi.mocked(workspaceApi.getFile).mockResolvedValue({ file: { ...metadata, ...patch } });
  render(<FileInspector inspector={{ kind: "file", objectId: "node" }} />);
  await screen.findByText(/此文件提供元数据与下载/);
  expect(workspaceApi.readFileText).not.toHaveBeenCalled();
  expect(screen.getByRole("link", { name: "下载" })).toHaveAttribute(
    "href",
    "/api/v1/files/f/content",
  );
});
it("accepts precisely 1 MiB and ignores stale A → B → A preview responses", async () => {
  expect(canPreview({ ...metadata, size_bytes: 1024 * 1024 })).toBe(true);
  let resolve!: (v: string) => void;
  vi.mocked(workspaceApi.readFileText)
    .mockReturnValueOnce(
      new Promise((r) => {
        resolve = r;
      }),
    )
    .mockResolvedValue("fresh");
  const { rerender } = render(<FilePreview file={metadata} />);
  await waitFor(() => expect(workspaceApi.readFileText).toHaveBeenCalled());
  rerender(<FilePreview file={{ ...metadata, file_id: "B" }} />);
  await screen.findByText("fresh");
  rerender(<FilePreview file={metadata} />);
  await waitFor(() => expect(workspaceApi.readFileText).toHaveBeenCalledTimes(3));
  await act(async () => resolve("old body"));
  expect(screen.queryByText("old body")).not.toBeInTheDocument();
});
it("shows inherited grants only from recursive ancestors and defaults write permissions off", async () => {
  await useWorkspace.getState().loadTree();
  vi.spyOn(workspaceApi, "listConversationResources").mockResolvedValue({
    attachments: [],
    grants: [
      {
        grant_id: "g1",
        node_id: "dir",
        name: "Docs",
        kind: "directory",
        operation: "read_content",
        recursive: true,
      },
      {
        grant_id: "g2",
        node_id: "dir",
        name: "Docs",
        kind: "directory",
        operation: "update_content",
        recursive: false,
      },
    ],
  });
  render(<GrantEditor node={file} subject={{ kind: "conversation", id: "c", title: "A" }} />);
  await screen.findByText(/继承自目录 \/Docs/);
  expect(screen.getAllByRole("button", { name: "撤销规则" })).toHaveLength(1);
  expect(screen.getByLabelText("修改内容")).not.toBeChecked();
  expect(screen.getByLabelText("移除入口")).not.toBeChecked();
  fireEvent.click(screen.getByLabelText("修改内容"));
  useShell.getState().navigate({ screen: "workspace" });
  expect(useShell.getState().pendingRouteReason).toBe("resources");
  expect(useShell.getState().pendingRoute).not.toBeNull();
});
it("indexes large trees once and preserves folder-first stable order", () => {
  const nodes = Array.from({ length: 10000 }, (_, i) => ({
    ...file,
    node_id: String(i),
    name: String(i),
    parent_id: "root",
  }));
  const start = performance.now();
  const index = workspaceIndex({
    workspace_id: "w",
    root_id: "root",
    nodes: [root, folder, ...nodes],
  });
  expect(index.nodes.size).toBe(10002);
  expect(index.children.get("root")).toHaveLength(10001);
  expect(compareNodes(folder, file)).toBeLessThan(0);
  expect(performance.now() - start).toBeLessThan(5000);
});

it("retains the authorization DOM while cancel keeps draft and approved discard resets it", async () => {
  vi.spyOn(workspaceApi, "listConversationResources").mockResolvedValue({
    attachments: [],
    grants: [],
  });
  const subject = { kind: "conversation" as const, id: "c", title: "Draft" };
  render(<GrantEditor node={file} subject={subject} />);
  const permission = screen.getByLabelText("读取内容");
  fireEvent.click(permission);
  expect(permission).not.toBeChecked();
  const owner = useShell.getState().dirtyResourceEditor;
  act(() => useShell.getState().closeInspector());
  expect(useShell.getState().dirtyResourceEditor).toBe(owner);
  act(() => useShell.setState({ pendingRoute: null }));
  expect(screen.getByLabelText("读取内容")).toBe(permission);
  expect(permission).not.toBeChecked();
  act(() => useShell.setState({ dirtyResourceEditor: null }));
  expect(screen.getByLabelText("读取内容")).toBe(permission);
  expect(permission).toBeChecked();
});
