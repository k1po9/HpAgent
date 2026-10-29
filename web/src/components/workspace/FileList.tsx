import { Button, Table, type TableColumnsType } from "@lobehub/ui";
import { Folder, FileText } from "lucide-react";
import type { ComponentProps } from "react";
import type { HpWorkspaceNode, HpWorkspaceSearchItem } from "../../api/types";
import { useShell } from "../../store/shell";
import { useWorkspace } from "../../store/workspace";
import { formatBytes, nodePath } from "./workspacePresentation";
type Row = HpWorkspaceNode | HpWorkspaceSearchItem;
function FileTable(props: ComponentProps<"table">) {
  return <table {...props} aria-label="空间文件" />;
}
const components = { table: FileTable };
export function FileList({
  nodes,
  search,
}: {
  nodes: HpWorkspaceNode[];
  search?: HpWorkspaceSearchItem[];
}) {
  const selected = useShell((s) => s.route.inspector);
  const tree = useWorkspace((s) => s.tree);
  const nodeOf = (row: Row) =>
    "kind" in row ? row : tree?.nodes.find((node) => node.node_id === row.node_id);
  const inspect = (row: Row, triggerId: string) =>
    useShell.getState().openInspector({
      kind: "file",
      objectId: row.node_id,
      origin: { directoryId: useShell.getState().route.directoryId, triggerId },
    });
  const columns: TableColumnsType<Row> = [
    {
      key: "name",
      title: "名称",
      className: "hp-file-column-name",
      render: (_, row) => {
        const node = nodeOf(row),
          directory = node?.kind === "directory";
        return (
          <>
            <Button
              htmlType="button"
              type="text"
              id={`workspace-node-${row.node_id}`}
              className="hp-file-name"
              onClick={() => {
                if (directory)
                  useShell.getState().requestResourceChange(() => {
                    if (
                      useShell
                        .getState()
                        .navigate({ screen: "workspace", directoryId: row.node_id })
                    )
                      useWorkspace.setState({ allFiles: false, searchMode: false });
                  });
                else inspect(row, `workspace-node-${row.node_id}`);
              }}
            >
              {directory ? (
                <Folder className="hp-folder-icon" size={18} aria-hidden="true" />
              ) : (
                <FileText size={18} aria-hidden="true" />
              )}
              <span>{row.name}</span>
            </Button>
            {search && <small>{node ? nodePath(tree, row.node_id) : "位置待同步"}</small>}
          </>
        );
      },
    },
    {
      key: "type",
      title: "类型",
      className: "hp-file-column-type",
      render: (_, row) =>
        nodeOf(row)?.kind === "directory"
          ? "目录"
          : "content_type" in row && row.content_type
            ? row.content_type
            : `${row.name.includes(".") ? row.name.split(".").pop()?.toUpperCase() : "未知"}（名称提示）`,
    },
    {
      key: "size",
      title: "大小",
      render: (_, row) =>
        nodeOf(row)?.kind === "directory"
          ? "—"
          : formatBytes("size_bytes" in row ? row.size_bytes : nodeOf(row)?.source?.size_bytes),
    },
    {
      key: "source",
      title: "来源",
      className: "hp-file-column-source",
      render: (_, row) => {
        const purpose = "purpose" in row ? row.purpose : nodeOf(row)?.source?.purpose;
        return purpose === "output" ? "AI 输出" : purpose === "input" ? "上传" : "—";
      },
    },
    {
      key: "actions",
      title: "操作",
      width: 82,
      render: (_, row) => (
        <Button
          htmlType="button"
          type="text"
          className="hp-file-details"
          id={`workspace-details-${row.node_id}`}
          aria-label={`查看${row.name}详情`}
          onClick={() => inspect(row, `workspace-details-${row.node_id}`)}
        >
          详情
        </Button>
      ),
    },
  ];
  return (
    <Table<Row>
      className="hp-file-table hp-workspace-file-table hp-lobe-scope"
      components={components}
      columns={columns}
      dataSource={search ?? nodes}
      rowKey="node_id"
      pagination={false}
      tableLayout="fixed"
      variant="borderless"
      locale={{ emptyText: search ? "没有匹配文件。" : "此处暂无文件或目录。" }}
      onRow={(row) => ({
        className: "hp-workspace-file-row",
        "data-selected": selected?.kind === "file" && selected.objectId === row.node_id,
      })}
    />
  );
}
