import { useEffect, useRef, useState } from "react";
import { HpApi } from "../../api/resources";
import { api } from "../../api/client";
import { useAuth } from "../../store/auth";
import { useWorkbench } from "../../store/workbench";
import { useShell } from "../../store/shell";
import { Surface } from "../shell/Surface";

type Grant = Awaited<ReturnType<HpApi["listConversationResources"]>>["grants"][number];
type Node = Awaited<ReturnType<HpApi["getWorkspace"]>>["nodes"][number];
type AccountSession = ReturnType<typeof useAuth.getState>["account"];

interface ResourceOwner {
  account: AccountSession;
  conversationId: string;
  key: string;
}

interface RevokeOperation {
  owner: ResourceOwner;
  token: symbol;
}

const resourcesApi = new HpApi(api);
const ownerKey = (account: AccountSession, conversationId: string) =>
  `${account?.account_id ?? "anonymous"}:${conversationId}`;

export function ConversationResources({
  refresh,
  ensure,
}: {
  refresh: number;
  ensure: () => Promise<string | null>;
}) {
  const id = useWorkbench((s) => s.activeConversationId);
  const account = useAuth((s) => s.account);
  const currentOwnerKey = id ? ownerKey(account, id) : null;
  const [revision, setRevision] = useState(0);
  const [data, setData] = useState<{ id: string; grants: Grant[] } | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [picker, setPicker] = useState(false);
  const [removal, setRemoval] = useState<{
    id: string;
    account: AccountSession;
    rules: Grant[];
  } | null>(null);
  const removing =
    removal?.id === id && removal.account === account && removal.rules.length > 0
      ? removal.rules
      : null;
  const setRemoving = (rules: Grant[] | null) =>
    setRemoval(rules && rules.length > 0 && id ? { id, account, rules } : null);
  const [operations, setOperations] = useState<RevokeOperation[]>([]);
  const operationsRef = useRef<RevokeOperation[]>([]);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const busy = Boolean(
    currentOwnerKey &&
    operations.some(
      (operation) => operation.owner.key === currentOwnerKey && operation.owner.account === account,
    ),
  );
  const beginOperation = (owner: ResourceOwner) => {
    const token = Symbol("resource-revoke");
    operationsRef.current = [...operationsRef.current, { owner, token }];
    setOperations(operationsRef.current);
    return token;
  };
  const finishOperation = (owner: ResourceOwner, token: symbol) => {
    operationsRef.current = operationsRef.current.filter(
      (operation) => operation.owner.key !== owner.key || operation.token !== token,
    );
    if (mounted.current) setOperations(operationsRef.current);
  };
  const canWriteOwnerView = (owner: ResourceOwner) =>
    mounted.current &&
    useAuth.getState().account === owner.account &&
    useWorkbench.getState().activeConversationId === owner.conversationId;
  const [notice, setNotice] = useState("");
  const generation = useRef(0);
  const [feedbackOwner, setFeedbackOwner] = useState({ id, account });
  if (feedbackOwner.id !== id || feedbackOwner.account !== account) {
    setFeedbackOwner({ id, account });
    setError("");
    setNotice("");
    setLoading(false);
  }
  useEffect(() => {
    const token = ++generation.current;
    if (!id) return;
    let alive = true;
    void resourcesApi
      .listConversationResources(id)
      .then((page) => {
        if (alive && token === generation.current) {
          setData({ id, grants: page.grants });
          setRemoval((current) => {
            if (current?.id !== id || current.account !== account) return current;
            const currentIds = new Set(current.rules.map((rule) => rule.grant_id));
            const remaining = page.grants.filter((rule) => currentIds.has(rule.grant_id));
            return remaining.length > 0 ? { id, account, rules: remaining } : null;
          });
          setError("");
          setLoading(false);
        }
      })
      .catch(() => {
        if (alive && token === generation.current) {
          setError("长期资料加载失败，请重试。");
          setLoading(false);
        }
      });
    return () => {
      alive = false;
      generation.current = token + 1;
    };
  }, [id, account, refresh, revision]);
  const grants = data?.id === id ? data.grants : [];
  const grouped = [...new Set(grants.map((g) => g.node_id))].map((nodeId) =>
    grants.filter((g) => g.node_id === nodeId),
  );
  return (
    <div className="hp-conversation-resources">
      <div className="hp-resource-chips" aria-label="长期授权资料">
        {id && data?.id !== id && !error && <span>正在加载长期资料…</span>}
        {id && data?.id === id && !grants.length && <span>暂无长期资料</span>}
        {grouped.map((rules) => (
          <span className="hp-resource-chip" key={rules[0]!.node_id}>
            {rules[0]!.name} ·{" "}
            {rules.some((g) => g.operation === "read_content") ? "可读取" : "其他权限"} ·{" "}
            {rules.some((g) => g.recursive) ? "含子目录" : "仅此项"}
            <button
              type="button"
              title="撤销授权"
              aria-label={`撤销 ${rules[0]!.name} 的授权`}
              onClick={() => setRemoving(rules)}
            >
              ×
            </button>
          </span>
        ))}
        <button
          type="button"
          disabled={busy}
          onClick={() => {
            setNotice("");
            setPicker(true);
          }}
        >
          使用资料
        </button>
        {id && (
          <button type="button" onClick={() => useShell.setState({ modal: "resources" })}>
            高级资料管理
          </button>
        )}
      </div>
      {error && (
        <p role="alert">
          {error}{" "}
          <button
            type="button"
            disabled={loading}
            onClick={() => {
              setLoading(true);
              setRevision((v) => v + 1);
            }}
          >
            重试资料查询
          </button>
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {picker && (
        <ResourcePicker
          key={account?.account_id ?? "anonymous"}
          ensure={ensure}
          onClose={() => setPicker(false)}
          onRefresh={() => setRevision((v) => v + 1)}
          onSaved={() => {
            setRevision((v) => v + 1);
            setNotice("新增资料下一轮可用。");
          }}
        />
      )}
      {removing?.[0] && (
        <Surface
          title="撤销长期资料授权"
          onClose={() => {
            if (!busy) setRemoving(null);
          }}
        >
          <p>将撤销 {removing[0].name} 的以下规则，可能停止使用这些资料的执行：</p>
          <ul>
            {removing.map((g) => (
              <li key={g.grant_id}>
                {g.operation} · {g.recursive ? "递归" : "仅此项"}
              </li>
            ))}
          </ul>
          <button
            type="button"
            disabled={busy}
            onClick={() => {
              if (!id || busy) return;
              const owner = {
                account: useAuth.getState().account,
                conversationId: id,
                key: ownerKey(useAuth.getState().account, id),
              };
              if (
                operationsRef.current.some(
                  (operation) =>
                    operation.owner.key === owner.key && operation.owner.account === owner.account,
                )
              )
                return;
              const operationToken = beginOperation(owner);
              const targetRules = [...removing];
              const targetIds = new Set(targetRules.map((rule) => rule.grant_id));
              void (async () => {
                const results = await Promise.allSettled(
                  targetRules.map((g) =>
                    resourcesApi.revokeConversationResource(owner.conversationId, g.grant_id),
                  ),
                );
                const failed = results.filter((r) => r.status === "rejected").length;
                const affected = results.flatMap((r) =>
                  r.status === "fulfilled" ? r.value.affected_runs : [],
                );
                const page = await resourcesApi.listConversationResources(owner.conversationId);
                if (!canWriteOwnerView(owner)) return;
                const remaining = page.grants.filter((rule) => targetIds.has(rule.grant_id));
                setData({ id: owner.conversationId, grants: page.grants });
                setRemoving(remaining.length > 0 ? remaining : null);
                setError(
                  remaining.length > 0
                    ? `${remaining.length} 条规则尚未撤销，可重试仍存在的规则。`
                    : "",
                );
                setNotice(
                  remaining.length === 0 && affected.length === 0
                    ? "授权已撤销。"
                    : affected
                        .map(
                          (run) =>
                            `${run.run_id}：${run.stop_state === "stopping" ? "正在停止" : "已停止"}`,
                        )
                        .join("；") || (failed ? "仍有规则待撤销。" : "授权已更新。"),
                );
                void useWorkbench.getState().refreshActiveRun();
              })()
                .catch(() => {
                  if (canWriteOwnerView(owner)) setError("状态待确认，请重新查询授权。");
                })
                .finally(() => {
                  finishOperation(owner, operationToken);
                });
            }}
          >
            确认撤销
          </button>
        </Surface>
      )}
    </div>
  );
}
function ResourcePicker({
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
    void resourcesApi
      .getWorkspace()
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
