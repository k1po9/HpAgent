import { useEffect, useRef, useState } from "react";
import { HpApi } from "../../api/resources";
import { api } from "../../api/client";
import { useAuth } from "../../store/auth";
import { useWorkbench } from "../../store/workbench";
import { ResourcePicker } from "../workspace/ResourcePicker";
import { useWorkspace } from "../../store/workspace";
import { observeRevocation, invalidateResourceViews } from "../workspace/workspaceOperations";
import { useShell } from "../../store/shell";
import { Surface } from "../shell/Surface";

type Grant = Awaited<ReturnType<HpApi["listConversationResources"]>>["grants"][number];
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
  useEffect(() => {
    const update = () => setRevision((v) => v + 1);
    window.addEventListener("workspace-permissions-changed", update);
    return () => window.removeEventListener("workspace-permissions-changed", update);
  }, []);
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
              const accountGeneration = useWorkspace.getState().generation;
              const currentAccount = () =>
                owner.account !== null &&
                useAuth.getState().status === "signedIn" &&
                useAuth.getState().account?.account_id === owner.account.account_id &&
                useWorkspace.getState().generation === accountGeneration;
              const targetRules = [...removing];
              const targetIds = new Set(targetRules.map((rule) => rule.grant_id));
              void (async () => {
                const results = await Promise.allSettled(
                  targetRules.map((g) =>
                    resourcesApi
                      .revokeConversationResource(owner.conversationId, g.grant_id)
                      .then((result) => {
                        if (currentAccount()) {
                          observeRevocation(result.affected_runs.map((run) => run.run_id));
                          invalidateResourceViews();
                        }
                        return result;
                      }),
                  ),
                );
                const failed = results.filter((r) => r.status === "rejected").length;
                const affected = results.flatMap((r) =>
                  r.status === "fulfilled" ? r.value.affected_runs : [],
                );
                if (!currentAccount()) return;
                const page = await resourcesApi.listConversationResources(owner.conversationId);
                if (!currentAccount()) return;
                invalidateResourceViews();
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
