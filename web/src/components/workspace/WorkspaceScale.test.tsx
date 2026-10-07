import { act, render, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import type { HpWorkspaceNode } from "../../api/types";
import { useWorkspace, workspaceApi } from "../../store/workspace";
import { WorkspaceSidebar } from "./WorkspaceSidebar";
import { FileList } from "./FileList";
import { workspaceIndex } from "./workspacePresentation";
beforeEach(() => {
  vi.restoreAllMocks();
  useWorkspace.getState().reset();
});
it.each([1000, 10000])(
  "indexes and browses %i nodes without per-row metadata or duplicated trees",
  async (size) => {
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
    const directories = Array.from({ length: 100 }, (_, i) => ({
      ...root,
      node_id: `dir${i}`,
      parent_id: "root",
      name: `目录 ${i}`,
    }));
    const files = Array.from({ length: size }, (_, i) => ({
      ...root,
      node_id: `file${i}`,
      parent_id: `dir${i % 100}`,
      name: `资料 ${i}.txt`,
      kind: "file" as const,
      file_id: `stored${i}`,
    }));
    const tree = { workspace_id: "w", root_id: "root", nodes: [root, ...directories, ...files] };
    const getTree = vi.spyOn(workspaceApi, "getWorkspace").mockResolvedValue(tree);
    const metadata = vi.spyOn(workspaceApi, "getFile");
    const start = performance.now();
    const index = workspaceIndex(tree);
    const indexMs = performance.now() - start;
    expect(workspaceIndex(tree)).toBe(index);
    const rendering = performance.now();
    const sidebar = render(<WorkspaceSidebar />);
    await act(async () => {
      await useWorkspace.getState().loadTree();
    });
    const listing = render(<FileList nodes={index.children.get("dir0")!} />);
    await waitFor(() =>
      expect(listing.container.querySelectorAll("tbody tr")).toHaveLength(size / 100),
    );
    expect(getTree).toHaveBeenCalledTimes(1);
    expect(metadata).not.toHaveBeenCalled();
    console.info(
      JSON.stringify({
        nodes: tree.nodes.length,
        files: size,
        indexMs: Number(indexMs.toFixed(2)),
        initialNavigationAndDirectoryRenderMs: Number((performance.now() - rendering).toFixed(2)),
        visibleFileRows: size / 100,
        treeRequests: getTree.mock.calls.length,
        metadataRequests: metadata.mock.calls.length,
      }),
    );
    sidebar.unmount();
    listing.unmount();
  },
  20000,
);
