import { Button } from "../ui";
import { Folder, Files } from "lucide-react";
import { useEffect, useMemo } from "react";
import { useWorkspace } from "../../store/workspace";
import { useShell } from "../../store/shell";
import type { HpWorkspaceNode } from "../../api/types";
import { workspaceIndex } from "./workspacePresentation";
export function WorkspaceSidebar() {
  const tree = useWorkspace((s) => s.tree);
  const expanded = useWorkspace((s) => s.expanded);
  const allFiles = useWorkspace((s) => s.allFiles);
  const error = useWorkspace((s) => s.error);
  const route = useShell((s) => s.route);
  useEffect(() => {
    void useWorkspace
      .getState()
      .loadTree()
      .catch(() => {});
  }, []);
  const index = useMemo(() => (tree ? workspaceIndex(tree) : null), [tree]);
  function go(id: string) {
    useShell.getState().requestResourceChange(() => {
      if (useShell.getState().navigate({ screen: "workspace", directoryId: id }))
        useWorkspace.setState({ allFiles: false, searchMode: false });
    });
  }
  function directory(node: HpWorkspaceNode, seen: Set<string>) {
    if (seen.has(node.node_id)) return null;
    const next = new Set(seen).add(node.node_id);
    const children = index?.children.get(node.node_id)?.filter((n) => n.kind === "directory") ?? [];
    const open = expanded[node.node_id] ?? node.parent_id === null;
    return (
      <li key={node.node_id}>
        <div className="hp-tree-row">
          {children.length > 0 && (
            <Button
              type="button"
              aria-label={`${open ? "收起" : "展开"}${node.name || "根目录"}`}
              aria-expanded={open}
              onClick={() =>
                useWorkspace.setState({ expanded: { ...expanded, [node.node_id]: !open } })
              }
            >
              {open ? "−" : "+"}
            </Button>
          )}
          <Button
            type="button"
            aria-current={
              !allFiles && (route.directoryId ?? tree?.root_id) === node.node_id
                ? "location"
                : undefined
            }
            onClick={() => go(node.node_id)}
          >
            <Folder size={16} aria-hidden="true" />
            {node.parent_id === null ? "根目录" : node.name}
          </Button>
        </div>
        {open && children.length > 0 && <ul>{children.map((n) => directory(n, next))}</ul>}
      </li>
    );
  }
  return (
    <nav className="hp-workspace-sidebar" aria-label="空间目录">
      <h2>空间</h2>
      <Button
        type="button"
        aria-pressed={allFiles}
        onClick={() => {
          useShell.getState().requestResourceChange(() => {
            useWorkspace.setState({ allFiles: true, searchMode: false });
            useShell.setState({ sidebarOpen: false });
          });
        }}
      >
        <Files size={16} aria-hidden="true" /> 全部文件
      </Button>
      {!tree && !error && <p role="status">正在加载目录…</p>}
      {tree && index?.nodes.get(tree.root_id) && (
        <ul>{directory(index.nodes.get(tree.root_id)!, new Set())}</ul>
      )}
      {error && (
        <p role="alert">
          {error}
          <Button
            type="button"
            onClick={() =>
              void useWorkspace
                .getState()
                .loadTree(true)
                .catch(() => {})
            }
          >
            重试
          </Button>
        </p>
      )}
    </nav>
  );
}
