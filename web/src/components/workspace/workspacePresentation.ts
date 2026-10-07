import type { HpWorkspace, HpWorkspaceNode, HpFile } from "../../api/types";
const indexes = new WeakMap<
  HpWorkspace,
  { nodes: Map<string, HpWorkspaceNode>; children: Map<string, HpWorkspaceNode[]> }
>();
export function workspaceIndex(tree: HpWorkspace) {
  const existing = indexes.get(tree);
  if (existing) return existing;
  const nodes = new Map(tree.nodes.map((n) => [n.node_id, n]));
  const children = new Map<string, HpWorkspaceNode[]>();
  for (const node of tree.nodes) {
    if (node.parent_id) {
      const list = children.get(node.parent_id) ?? [];
      list.push(node);
      children.set(node.parent_id, list);
    }
  }
  for (const list of children.values()) list.sort(compareNodes);
  const index = { nodes, children };
  indexes.set(tree, index);
  return index;
}
export function compareNodes(a: HpWorkspaceNode, b: HpWorkspaceNode) {
  return (
    (a.kind === b.kind ? 0 : a.kind === "directory" ? -1 : 1) ||
    a.name.localeCompare(b.name, "zh-CN") ||
    a.node_id.localeCompare(b.node_id)
  );
}
export function ancestors(tree: HpWorkspace, nodeId: string): HpWorkspaceNode[] {
  const index = workspaceIndex(tree).nodes;
  const result: HpWorkspaceNode[] = [];
  const seen = new Set<string>();
  let node = index.get(nodeId);
  while (node && !seen.has(node.node_id)) {
    seen.add(node.node_id);
    result.unshift(node);
    node = node.parent_id ? index.get(node.parent_id) : undefined;
  }
  return result;
}
export function nodePath(tree: HpWorkspace | null, id: string) {
  return tree
    ? ancestors(tree, id)
        .map((n) => (n.parent_id === null ? "" : n.name))
        .join("/") || "/"
    : "位置待同步";
}
export function canPreview(file: HpFile) {
  return (
    file.status === "ready" &&
    ["text/plain", "text/markdown", "text/x-log", "text/html"].includes(file.content_type ?? "") &&
    ["utf-8", "utf8"].includes(file.encoding?.toLowerCase() ?? "") &&
    file.size_bytes !== null &&
    file.size_bytes <= 1024 * 1024
  );
}
export function formatBytes(value: number | null | undefined) {
  return value == null
    ? "—"
    : value < 1024
      ? `${value} B`
      : value < 1024 * 1024
        ? `${(value / 1024).toFixed(1)} KiB`
        : `${(value / 1024 / 1024).toFixed(1)} MiB`;
}
