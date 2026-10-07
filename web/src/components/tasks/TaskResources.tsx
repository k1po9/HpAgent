import { useState } from "react";
import type { HpWork } from "../../api/types";
import { useWorkspace, workspaceApi } from "../../store/workspace";
import { useShell } from "../../store/shell";
import { WorkspaceDirectoryLoader } from "../workspace/SaveToWorkspaceDialog";
import { GrantEditor } from "../workspace/GrantEditor";
import { nodePath } from "../workspace/workspacePresentation";
import { WorkInputs } from "./TaskInputs";
import { useWorkspaceQuery } from "../workspace/useWorkspaceQuery";
import { listGrants, type Subject } from "../workspace/workspaceOperations";
export function TaskResources({ work }: { work: HpWork }) {
  const [nodeId, setNodeId] = useState("");
  const tree = useWorkspace((s) => s.tree);
  const node = tree?.nodes.find((n) => n.node_id === nodeId);
  const subject: Subject = { kind: "work", id: work.work_id, title: work.title };
  const grants = useWorkspaceQuery(`work-grants:${work.work_id}`, () => listGrants(subject));
  const metadata = useWorkspaceQuery(node?.file_id ? `file:${node.file_id}` : null, () =>
    workspaceApi.getFile(node!.file_id!),
  );
  const inputFile =
    metadata.data?.file.status === "ready" && metadata.data.file.purpose === "input"
      ? (node?.file_id ?? null)
      : null;
  return (
    <div className="hp-operation-form">
      <WorkspaceDirectoryLoader />
      <h4>已授权资料</h4>
      {grants.error && (
        <p role="alert">
          权限查询失败。<button onClick={grants.retry}>重试权限</button>
        </p>
      )}
      {grants.data?.grants.length === 0 && <p>此任务暂无长期资料授权。</p>}
      {grants.data?.grants.map((g) => (
        <p key={g.grant_id}>
          {g.name} · {g.operation} · {g.recursive ? "包含子项" : "仅此项"}
          <button
            onClick={() => useShell.getState().requestResourceChange(() => setNodeId(g.node_id))}
          >
            查看与管理
          </button>
        </p>
      ))}
      <label>
        选择长期目录或文件
        <select
          value={nodeId}
          onChange={(e) => {
            const next = e.target.value;
            useShell.getState().requestResourceChange(() => setNodeId(next));
          }}
        >
          <option value="">请选择</option>
          {tree?.nodes.map((n) => (
            <option key={n.node_id} value={n.node_id}>
              {nodePath(tree, n.node_id)}
            </option>
          ))}
        </select>
      </label>
      {node && <GrantEditor node={node} subject={subject} />}
      {node?.file_id && !inputFile && (
        <p>
          只有元数据已核实为 ready 且 purpose=input
          的文件可添加输入。输出文件可通过空间读取授权使用。
        </p>
      )}
      <WorkInputs subject={subject} fileId={inputFile} />
    </div>
  );
}
