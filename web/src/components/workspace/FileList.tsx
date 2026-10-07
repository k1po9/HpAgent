import type { HpWorkspaceNode, HpWorkspaceSearchItem } from "../../api/types";
import { useShell } from "../../store/shell";
import { useWorkspace } from "../../store/workspace";
import { formatBytes, nodePath } from "./workspacePresentation";
export function FileList({
  nodes,
  search,
}: {
  nodes: HpWorkspaceNode[];
  search?: HpWorkspaceSearchItem[];
}) {
  const tree = useWorkspace((s) => s.tree);
  const rows = search ?? nodes;
  return (
    <table className="hp-file-table" aria-label="空间文件">
      <thead>
        <tr>
          <th scope="col">名称</th>
          <th scope="col">类型</th>
          <th scope="col">大小</th>
          <th scope="col">来源</th>
          <th scope="col">操作</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => {
          const node = "kind" in row ? row : tree?.nodes.find((n) => n.node_id === row.node_id);
          const isDirectory = node?.kind === "directory";
          const open = () => {
            if (isDirectory) {
              useWorkspace.setState({ allFiles: false, searchMode: false });
              useShell.getState().navigate({ screen: "workspace", directoryId: row.node_id });
            } else
              useShell.getState().openInspector({
                kind: "file",
                objectId: row.node_id,
                origin: { directoryId: useShell.getState().route.directoryId },
              });
          };
          const purpose = "purpose" in row ? row.purpose : node?.source?.purpose;
          return (
            <tr key={row.node_id}>
              <td>
                <button
                  id={`workspace-node-${row.node_id}`}
                  className="hp-file-name"
                  onClick={open}
                >
                  {isDirectory ? "📁 " : "📄 "}
                  {row.name}
                </button>
                {search && <small>{node ? nodePath(tree, row.node_id) : "位置待同步"}</small>}
              </td>
              <td>
                {isDirectory
                  ? "目录"
                  : "content_type" in row && row.content_type
                    ? row.content_type
                    : `${row.name.includes(".") ? row.name.split(".").pop()?.toUpperCase() : "未知"}（名称提示）`}
              </td>
              <td>
                {isDirectory
                  ? "—"
                  : formatBytes("size_bytes" in row ? row.size_bytes : node?.source?.size_bytes)}
              </td>
              <td>{purpose === "output" ? "AI 输出" : purpose === "input" ? "上传" : "—"}</td>
              <td>
                <button
                  aria-label={`查看${row.name}详情`}
                  onClick={() =>
                    useShell.getState().openInspector({
                      kind: "file",
                      objectId: row.node_id,
                      origin: { directoryId: useShell.getState().route.directoryId },
                    })
                  }
                >
                  详情
                </button>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
