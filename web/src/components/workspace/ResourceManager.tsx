import { useState } from "react";
import { useWorkspace, workspaceApi } from "../../store/workspace";
import { useWorkbench } from "../../store/workbench";
import { SubjectPicker } from "./SubjectPicker";
import { GrantEditor } from "./GrantEditor";
import { WorkspaceDirectoryLoader } from "./SaveToWorkspaceDialog";
import { nodePath } from "./workspacePresentation";
import { useWorkspaceQuery } from "./useWorkspaceQuery";
import {
  listGrants,
  observeRevocation,
  permissionsChanged,
  type Subject,
} from "./workspaceOperations";
import { useShell } from "../../store/shell";
import { Surface } from "../shell/Surface";
export function ResourceManager({ initialSubject }: { initialSubject?: Subject }) {
  const [subject, setSubject] = useState(initialSubject);
  const [nodeId, setNodeId] = useState("");
  const tree = useWorkspace((s) => s.tree);
  const node = tree?.nodes.find((n) => n.node_id === nodeId);
  return (
    <section className="hp-operation-form">
      <WorkspaceDirectoryLoader />
      <SubjectPicker value={subject} onChange={setSubject} />
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
              {nodePath(tree, n.node_id)}（{n.kind === "file" ? "文件" : "目录"}）
            </option>
          ))}
        </select>
      </label>
      {!subject && <p>选择对话或任务查看其权限。</p>}
      {subject && node && <GrantEditor node={node} subject={subject} />}
      {subject?.kind === "conversation" && (
        <ConversationAttachments key={subject.id} subject={subject} />
      )}
    </section>
  );
}
function ConversationAttachments({ subject }: { subject: Subject }) {
  const result = useWorkspaceQuery(`attachments:${subject.id}`, () => listGrants(subject));
  const run = useWorkbench((s) => (s.activeConversationId === subject.id ? s.activeRun : null));
  const [fileId, setFileId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const generation = useWorkspace((s) => s.generation);
  const attachments = result.data?.attachments ?? [];
  return (
    <section>
      <h3>对话附件可用性</h3>
      {attachments
        .filter((f) => f.available)
        .map((f) => (
          <p key={f.file_id}>
            {f.name}
            <button disabled={busy} onClick={() => setFileId(f.file_id)}>
              撤销附件可用性
            </button>
          </p>
        ))}
      {result.error && (
        <p role="alert">
          附件查询失败。<button onClick={result.retry}>重试附件</button>
        </p>
      )}
      {run && (
        <button
          onClick={() =>
            useShell
              .getState()
              .openInspector({ kind: "run", objectId: run.run_id, tab: "resources" })
          }
        >
          查看本次运行的候选、固定与读取资料
        </button>
      )}
      {fileId && (
        <Surface
          title="撤销附件可用性"
          onClose={() => {
            if (!busy) setFileId(null);
          }}
        >
          <p>可能停止读取该附件的执行。</p>
          <button
            disabled={busy}
            onClick={() => {
              setBusy(true);
              void workspaceApi
                .revokeConversationAttachment(subject.id, fileId)
                .then((r) => {
                  if (generation !== useWorkspace.getState().generation) return;
                  observeRevocation(r.affected_runs.map((v) => v.run_id));
                  permissionsChanged();
                  setFileId(null);
                })
                .catch(() => {
                  if (generation === useWorkspace.getState().generation)
                    setError("撤销状态待确认，请重新查询附件。");
                })
                .finally(() => {
                  if (generation === useWorkspace.getState().generation) setBusy(false);
                });
            }}
          >
            确认撤销附件
          </button>
        </Surface>
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
