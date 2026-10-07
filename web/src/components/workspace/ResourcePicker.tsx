import { useEffect, useRef, useState } from "react";
import { workspaceApi as resourcesApi, useWorkspace } from "../../store/workspace";
import { useAuth } from "../../store/auth";
import { useWorkbench } from "../../store/workbench";
import { useShell } from "../../store/shell";
import { Surface } from "../shell/Surface";
import type { HpWorkspaceNode as Node } from "../../api/types";
import { permissionsChanged } from "./workspaceOperations";
export function ResourcePicker({
  ensure,
  onClose,
  onSaved,
  onRefresh,
}: {
  ensure: () => Promise<string | null>;
  onClose: () => void;
  onSaved: () => void;
  onRefresh: () => void;
}) {
  const [nodes, setNodes] = useState<Node[] | null>(null);
  const [selection, setSelection] = useState<Record<string, boolean>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const alive = useRef(true);
  const origin = useRef({
    account: useAuth.getState().account,
    id: useWorkbench.getState().activeConversationId,
  });
  useEffect(() => {
    alive.current = true;
    void useWorkspace
      .getState()
      .loadTree(revision > 0)
      .then((tree) => {
        if (alive.current) {
          setNodes(tree.nodes.filter((n) => n.parent_id !== null));
          setError("");
        }
      })
      .catch(() => {
        if (alive.current) setError("资料树加载失败。");
      });
    return () => {
      alive.current = false;
    };
  }, [revision]);
  return (
    <Surface
      title="使用长期资料"
      onClose={() => {
        if (!busy) onClose();
      }}
    >
      <p>仅授权列出与读取内容。目录是否包含子项由你选择；新增资料下一轮可用。</p>
      {!useWorkbench.getState().activeConversationId && <p>确认时将为新对话准备资料。</p>}
      {!nodes && !error && <p>正在加载资料…</p>}
      {nodes?.length === 0 && <p>空间暂无可用资料，可先在空间保存文件。</p>}
      <div className="hp-resource-picker-list">
        {nodes?.map((node) => (
          <div key={node.node_id}>
            <label>
              <input
                type="checkbox"
                checked={node.node_id in selection}
                disabled={busy}
                onChange={(e) =>
                  setSelection((prev) => {
                    const next = { ...prev };
                    if (e.target.checked) next[node.node_id] = false;
                    else delete next[node.node_id];
                    return next;
                  })
                }
              />
              {node.name} · {node.kind === "directory" ? "目录" : "文件"}
            </label>
            {node.kind === "directory" && node.node_id in selection && (
              <label>
                <input
                  type="checkbox"
                  checked={selection[node.node_id]}
                  disabled={busy}
                  onChange={(e) =>
                    setSelection((prev) => ({ ...prev, [node.node_id]: e.target.checked }))
                  }
                />
                包含子目录与文件
              </label>
            )}
          </div>
        ))}
      </div>
      {error && (
        <p role="alert">
          {error}{" "}
          <button type="button" disabled={busy} onClick={() => setRevision((v) => v + 1)}>
            重新加载资料
          </button>
        </p>
      )}
      <button
        type="button"
        disabled={busy || !Object.keys(selection).length}
        onClick={() => {
          setBusy(true);
          setError("");
          void (async () => {
            if (
              useAuth.getState().account !== origin.current.account ||
              useWorkbench.getState().activeConversationId !== origin.current.id
            )
              throw new Error("对话已切换，请关闭后重新选择资料。");
            const id = await ensure();
            if (!id) throw new Error("创建对话失败或已离开，请重试。");
            origin.current.id = id;
            const valid = () =>
              alive.current &&
              useAuth.getState().account === origin.current.account &&
              useWorkbench.getState().activeConversationId === id &&
              useShell.getState().route.screen === "ai";
            // Read on every confirmation: partial success only retries missing rules.
            const page = await resourcesApi.listConversationResources(id);
            for (const [nodeId, recursive] of Object.entries(selection)) {
              const operations = (["list_metadata", "read_content"] as const).filter(
                (op) =>
                  !page.grants.some(
                    (g) => g.node_id === nodeId && g.operation === op && g.recursive === recursive,
                  ),
              );
              if (!valid()) throw new Error("对话已切换，请重新选择资料。");
              if (operations.length)
                await resourcesApi.grantConversationResource(id, nodeId, operations, recursive);
            }
            if (valid()) {
              permissionsChanged(false);
              onSaved();
              onClose();
            }
          })()
            .catch((err: unknown) => {
              if (alive.current) {
                setError(err instanceof Error ? err.message : "授权失败，请重试未完成规则。");
                if (
                  useAuth.getState().account === origin.current.account &&
                  useWorkbench.getState().activeConversationId === origin.current.id
                )
                  onRefresh();
              }
            })
            .finally(() => {
              if (alive.current) setBusy(false);
            });
        }}
      >
        {busy ? "授权中…" : "确认读取授权"}
      </button>
    </Surface>
  );
}
