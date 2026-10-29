import { Alert, Button, List, Tree, type TreeDataNode } from "@lobehub/ui";
import { Folder, Files } from "lucide-react";
import { useEffect, useMemo } from "react";
import { useWorkspace } from "../../store/workspace";
import { useShell } from "../../store/shell";
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
  const directories = useMemo(() => {
    if (!tree) return [];
    const index = workspaceIndex(tree);
    function directory(id: string, seen: Set<string>): TreeDataNode[] {
      const node = index.nodes.get(id);
      if (!node || node.kind !== "directory" || seen.has(id)) return [];
      const next = new Set(seen).add(id);
      return [
        {
          key: id,
          title: node.parent_id === null ? "根目录" : node.name,
          icon: <Folder size={16} aria-hidden="true" />,
          children: (index.children.get(id) ?? [])
            .filter((child) => child.kind === "directory")
            .flatMap((child) => directory(child.node_id, next)),
        },
      ];
    }
    return directory(tree.root_id, new Set());
  }, [tree]);
  return (
    <nav
      className="hp-workspace-sidebar hp-workspace-sidebar--lobe hp-lobe-scope"
      aria-label="空间目录"
    >
      <h2>空间</h2>
      <List
        className="hp-workspace-all-files"
        selectable
        activeKey={allFiles ? "all" : null}
        items={[
          {
            key: "all",
            icon: Files,
            label: "全部文件",
            extra: tree ? (
              <span className="hp-workspace-file-count" aria-hidden="true">
                {tree.nodes.filter((node) => node.kind === "file").length}
              </span>
            ) : undefined,
            onClick: () =>
              useShell.getState().requestResourceChange(() => {
                useWorkspace.setState({ allFiles: true, searchMode: false });
                useShell.setState({ sidebarOpen: false });
              }),
          },
        ]}
      />
      <p className="hp-workspace-directory-heading">目录</p>
      {!tree && !error && <p role="status">正在加载目录…</p>}
      {tree && (
        <Tree
          className="hp-workspace-directory-tree"
          treeData={directories}
          showIcon
          blockNode
          expandedKeys={tree.nodes
            .filter(
              (node) =>
                node.kind === "directory" && (expanded[node.node_id] ?? node.parent_id === null),
            )
            .map((node) => node.node_id)}
          selectedKeys={allFiles ? [] : [route.directoryId ?? tree.root_id]}
          onExpand={(keys) => {
            const open = new Set(keys);
            useWorkspace.setState({
              expanded: Object.fromEntries(
                tree.nodes
                  .filter((node) => node.kind === "directory")
                  .map((node) => [node.node_id, open.has(node.node_id)]),
              ),
            });
          }}
          onSelect={(_, { node }) =>
            useShell.getState().requestResourceChange(() => {
              if (useShell.getState().navigate({ screen: "workspace", directoryId: node.key }))
                useWorkspace.setState({ allFiles: false, searchMode: false });
            })
          }
        />
      )}
      {error && (
        <Alert
          type="error"
          title={error}
          action={
            <Button
              onClick={() =>
                void useWorkspace
                  .getState()
                  .loadTree(true)
                  .catch(() => {})
              }
            >
              重试
            </Button>
          }
        />
      )}
    </nav>
  );
}
