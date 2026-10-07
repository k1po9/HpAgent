import { useEffect, useId, useState } from "react";
import type { HpWorkspaceNode } from "../../api/types";
import { useShell } from "../../store/shell";
import { useWorkspace } from "../../store/workspace";
import { useWorkspaceQuery } from "./useWorkspaceQuery";
import { ancestors, nodePath } from "./workspacePresentation";
import {
  grantMissing,
  listGrants,
  revokeRules,
  useAffectedRuns,
  type Grant,
  type Permission,
  type Subject,
} from "./workspaceOperations";
import { Surface } from "../shell/Surface";
import { commandError } from "../../utils/commands";
const labels: Record<Permission, string> = {
  list_metadata: "发现资料",
  read_content: "读取内容",
  create_child: "创建子项",
  update_content: "修改内容",
  delete_entry: "移除入口",
};
export function GrantEditor({ node, subject }: { node: HpWorkspaceNode; subject: Subject }) {
  return (
    <Editor key={`${subject.kind}:${subject.id}:${node.node_id}`} node={node} subject={subject} />
  );
}
function Editor({ node, subject }: { node: HpWorkspaceNode; subject: Subject }) {
  const tree = useWorkspace((s) => s.tree);
  const query = useWorkspaceQuery(`grants:${subject.kind}:${subject.id}`, () =>
    listGrants(subject),
  );
  const [operations, setOperations] = useState<Permission[]>(["list_metadata", "read_content"]);
  const [recursive, setRecursive] = useState(false);
  const [busy, setBusy] = useState(false);
  const [removing, setRemoving] = useState<Grant[] | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const states = useAffectedRuns((s) => s.states);
  const parentIds = new Set(
    tree
      ? ancestors(tree, node.node_id)
          .slice(0, -1)
          .map((n) => n.node_id)
      : [],
  );
  const applicable =
    query.data?.grants.filter(
      (g) => g.node_id === node.node_id || (g.recursive && parentIds.has(g.node_id)),
    ) ?? [];
  const generation = useWorkspace((s) => s.generation);
  const viewId = useId();
  const editorKey = `${subject.kind}:${subject.id}:${node.node_id}:${viewId}`;
  const markDirty = () => useShell.setState({ dirtyResourceEditor: editorKey });
  useEffect(
    () => () => {
      if (useShell.getState().dirtyResourceEditor === editorKey)
        useShell.setState({ dirtyResourceEditor: null });
    },
    [editorKey],
  );
  function toggle(op: Permission, enabled: boolean) {
    markDirty();
    setOperations(enabled ? [...operations, op] : operations.filter((v) => v !== op));
  }
  return (
    <section className="hp-operation-form" aria-label="主体权限编辑">
      <p>
        管理 {subject.kind === "work" ? "任务" : "对话"}「{subject.title}」对{" "}
        {node.name || "根目录"} 的权限。
      </p>
      <p>账户可浏览空间；AI 仅按所选主体授权使用。新增授权下一轮生效。</p>
      {query.loading && <p role="status">正在读取权限…</p>}
      {query.error && (
        <p role="alert">
          权限状态待确认。<button onClick={query.retry}>重新查询授权</button>
        </p>
      )}
      {query.data && applicable.length === 0 && <p>所选主体暂无适用规则。</p>}
      {applicable.map((g) => (
        <p key={g.grant_id}>
          {labels[g.operation]} ·{" "}
          {g.node_id === node.node_id ? "直接授权" : `继承自目录 ${nodePath(tree, g.node_id)}`} ·{" "}
          {g.recursive ? "包含后代" : "仅此项"}
          <button disabled={busy} onClick={() => setRemoving([g])}>
            撤销规则
          </button>
        </p>
      ))}
      <fieldset disabled={busy}>
        <legend>新增权限</legend>
        {(["list_metadata", "read_content"] as const).map((op) => (
          <label key={op}>
            <input
              type="checkbox"
              checked={operations.includes(op)}
              onChange={(e) => toggle(op, e.target.checked)}
            />
            {labels[op]}
          </label>
        ))}
        <details>
          <summary>高级写入权限</summary>
          {(["create_child", "update_content", "delete_entry"] as const)
            .filter((op) => op !== "create_child" || node.kind === "directory")
            .map((op) => (
              <label key={op}>
                <input
                  type="checkbox"
                  checked={operations.includes(op)}
                  onChange={(e) => toggle(op, e.target.checked)}
                />
                {labels[op]}
              </label>
            ))}
        </details>
        {node.kind === "directory" && (
          <label>
            <input
              type="checkbox"
              checked={recursive}
              onChange={(e) => {
                markDirty();
                setRecursive(e.target.checked);
              }}
            />
            包含此目录的所有子目录与文件
          </label>
        )}
      </fieldset>
      <button
        disabled={busy || !query.data || !operations.length}
        onClick={() => {
          setBusy(true);
          setError("");
          void grantMissing(
            subject,
            node.node_id,
            operations,
            node.kind === "directory" && recursive,
          )
            .then(() => {
              if (generation === useWorkspace.getState().generation) {
                if (useShell.getState().dirtyResourceEditor === editorKey)
                  useShell.setState({ dirtyResourceEditor: null });
                setNotice("授权已生效，下一轮可用。");
              }
            })
            .catch((e) => {
              if (generation === useWorkspace.getState().generation) setError(commandError(e));
            })
            .finally(() => {
              if (generation === useWorkspace.getState().generation) setBusy(false);
            });
        }}
      >
        确认授予所选权限
      </button>
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {Object.entries(states).map(([id, state]) => (
        <p role="status" key={id}>
          {id}：{state}
        </p>
      ))}
      {removing && (
        <Surface
          title="确认撤销资料授权"
          onClose={() => {
            if (!busy) setRemoving(null);
          }}
        >
          <p>撤销以下规则可能停止受影响执行。继承规则的撤销作用于源目录及其其他后代。</p>
          {removing.map((g) => (
            <p key={g.grant_id}>
              {nodePath(tree, g.node_id)} · {labels[g.operation]}
            </p>
          ))}
          <button
            disabled={busy}
            onClick={() => {
              setBusy(true);
              setError("");
              void revokeRules(subject, removing)
                .then((remaining) => {
                  if (generation !== useWorkspace.getState().generation) return;
                  setRemoving(remaining.length ? remaining : null);
                  setNotice(
                    remaining.length
                      ? `${remaining.length} 条规则尚未撤销，可重试。`
                      : "规则已撤销，受影响执行状态另行确认。",
                  );
                })
                .catch(() => {
                  if (generation === useWorkspace.getState().generation)
                    setError("状态待确认，请重新查询授权。");
                })
                .finally(() => {
                  if (generation === useWorkspace.getState().generation) setBusy(false);
                });
            }}
          >
            确认撤销
          </button>
        </Surface>
      )}
    </section>
  );
}
