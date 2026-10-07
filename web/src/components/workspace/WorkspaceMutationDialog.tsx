import { useState } from "react";
import { useWorkspace, workspaceApi } from "../../store/workspace";
import { useShell } from "../../store/shell";
import type { HpWorkspaceNode } from "../../api/types";
import { nodePath, ancestors } from "./workspacePresentation";
import { Surface } from "../shell/Surface";
import { commandError } from "../../utils/commands";
export function WorkspaceMutationDialog({
  node,
  onClose,
}: {
  node: HpWorkspaceNode;
  onClose: () => void;
}) {
  const tree = useWorkspace((s) => s.tree);
  const revision = useWorkspace((s) => s.revision);
  const generation = useWorkspace((s) => s.generation);
  const [name, setName] = useState(node.name);
  const [parentId, setParentId] = useState(node.parent_id ?? "");
  const [action, setAction] = useState<"move" | "remove">("move");
  const [preview, setPreview] = useState<{
    fingerprint: string;
    token: string;
    runs: string[];
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [unknown, setUnknown] = useState(false);
  const fingerprint = JSON.stringify({
    generation,
    revision,
    nodeId: node.node_id,
    name,
    parentId,
    action,
  });
  const confirmed = preview?.fingerprint === fingerprint ? preview : null;
  return (
    <Surface
      title="管理空间入口"
      onClose={() => {
        if (!busy) onClose();
      }}
    >
      <div className="hp-operation-form">
        <p>{nodePath(tree, node.node_id)}</p>
        <label>
          操作
          <select
            disabled={busy}
            value={action}
            onChange={(e) => {
              setAction(e.target.value as typeof action);
              setPreview(null);
            }}
          >
            <option value="move">改名或移动</option>
            <option value="remove">移除入口</option>
          </select>
        </label>
        {action === "move" && (
          <>
            <label>
              名称
              <input
                disabled={busy}
                aria-label="Workspace 名称"
                value={name}
                onChange={(e) => {
                  setName(e.target.value);
                  setPreview(null);
                }}
              />
            </label>
            <label>
              目标目录
              <select
                disabled={busy}
                aria-label="Workspace 目标目录"
                value={parentId}
                onChange={(e) => {
                  setParentId(e.target.value);
                  setPreview(null);
                }}
              >
                {tree?.nodes
                  .filter(
                    (n) =>
                      n.kind === "directory" &&
                      !ancestors(tree, n.node_id).some(
                        (ancestor) => ancestor.node_id === node.node_id,
                      ),
                  )
                  .map((n) => (
                    <option key={n.node_id} value={n.node_id}>
                      {nodePath(tree, n.node_id)}
                    </option>
                  ))}
              </select>
            </label>
          </>
        )}
        <p>移除的是空间入口，不等于物理文件立即回收。非空目录和绑定的输出目录由服务器检查。</p>
        {unknown && (
          <p role="status">上次提交结果待确认；先刷新空间核实原结果。此操作不自动重放。</p>
        )}
        {error && <p role="alert">{error}</p>}
        {!unknown && (
          <button
            disabled={
              busy || node.parent_id === null || (action === "move" && (!name.trim() || !parentId))
            }
            onClick={() => {
              setBusy(true);
              setError("");
              const submitted = fingerprint;
              void workspaceApi
                .previewWorkspaceNode(node.node_id)
                .then((p) => {
                  if (generation === useWorkspace.getState().generation)
                    setPreview({
                      fingerprint: submitted,
                      token: p.preview_token,
                      runs: p.potentially_affected_runs,
                    });
                })
                .catch((e) => {
                  if (generation === useWorkspace.getState().generation) setError(commandError(e));
                })
                .finally(() => {
                  if (generation === useWorkspace.getState().generation) setBusy(false);
                });
            }}
          >
            预览{action === "move" ? "改名或移动" : "移除"}影响
          </button>
        )}
        {confirmed && !unknown && (
          <>
            <p>可能影响 {confirmed.runs.length} 个活动执行。改变参数后须重新预览。</p>
            {confirmed.runs.map((id) => (
              <button
                disabled={busy}
                key={id}
                onClick={() => {
                  onClose();
                  useShell.getState().openInspector({ kind: "run", objectId: id }, true);
                }}
              >
                查看执行 {id}
              </button>
            ))}
            <button
              disabled={busy}
              onClick={() => {
                setBusy(true);
                setError("");
                const command =
                  action === "move"
                    ? workspaceApi.moveWorkspaceNode(
                        node.node_id,
                        parentId,
                        name.trim(),
                        confirmed.token,
                      )
                    : workspaceApi.removeWorkspaceNode(node.node_id, confirmed.token);
                void command
                  .then(() => {
                    if (generation !== useWorkspace.getState().generation) return;
                    useWorkspace.getState().invalidate();
                    onClose();
                    if (action === "remove") useShell.getState().closeInspector();
                  })
                  .catch((e) => {
                    if (generation === useWorkspace.getState().generation) {
                      setPreview(null);
                      setError(commandError(e));
                      setUnknown(!(e && typeof e === "object" && "status" in e));
                    }
                  })
                  .finally(() => {
                    if (generation === useWorkspace.getState().generation) setBusy(false);
                  });
              }}
            >
              确认{action === "move" ? "改名或移动" : "移除"}
            </button>
          </>
        )}
        {unknown && (
          <button
            disabled={busy}
            onClick={() => {
              useWorkspace.getState().invalidate();
              onClose();
            }}
          >
            刷新空间并核实
          </button>
        )}
      </div>
    </Surface>
  );
}
