import { useEffect, useRef, useState } from "react";
import { HpCommandError, type HpFile } from "../../api/types";
import { useWorkspace } from "../../store/workspace";
import { useShell } from "../../store/shell";
import { runApi, useRunInspector } from "../../store/runInspector";

type Candidates = Awaited<ReturnType<typeof runApi.listRunResources>>;
export function RunResources({
  runId,
  terminal,
  runStatus,
  onSaveFile,
}: {
  runId: string;
  terminal: boolean;
  runStatus?: string;
  onSaveFile?: (file: HpFile) => void;
}) {
  const permissionRevision = useWorkspace((s) => s.revision);
  const [candidates, setCandidates] = useState<Candidates | null>(null);
  const [files, setFiles] = useState<
    Awaited<ReturnType<typeof runApi.listRunPublishedFiles>>["files"]
  >([]);
  const [error, setError] = useState<string | null>(null);
  const [outputLoading, setOutputLoading] = useState(true);
  const outputToken = useRef(0);
  const [outputError, setOutputError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const token = useRef(0);
  const mounted = useRef(false);
  const lock = useRef(false);
  async function load(append = false) {
    if (append && lock.current) return;
    const request = ++token.current;
    lock.current = true;
    setBusy(true);
    try {
      const data = await runApi.listRunResources(runId, append ? candidates?.next : null);
      if (request !== token.current || !mounted.current) return;
      setCandidates((old) => ({
        ...data,
        candidates: append
          ? [
              ...new Map(
                [...(old?.candidates ?? []), ...data.candidates].map((c) => [c.node_id, c]),
              ).values(),
            ]
          : data.candidates,
      }));
      setError(null);
    } catch (e) {
      if (request !== token.current || !mounted.current) return;
      if (e instanceof HpCommandError && [403, 404].includes(e.status)) setCandidates(null);
      setError(
        e instanceof HpCommandError && [403, 404].includes(e.status)
          ? "当前无法获取候选资料；不代表本次未使用资料。"
          : "候选资料待同步；已显示内容为先前快照，当前未验证。",
      );
    } finally {
      if (request === token.current && mounted.current) {
        lock.current = false;
        setBusy(false);
      }
    }
  }
  async function outputs() {
    const request = ++outputToken.current;
    setOutputLoading(true);
    try {
      const data = await runApi.listRunPublishedFiles(runId);
      if (mounted.current && request === outputToken.current) {
        setFiles(data.files);
        setOutputError(null);
      }
    } catch (e) {
      if (mounted.current && request === outputToken.current) {
        setOutputError("输出暂时无法同步。");
        if (e instanceof HpCommandError && [403, 404].includes(e.status)) setFiles([]);
      }
    } finally {
      if (mounted.current && request === outputToken.current) setOutputLoading(false);
    }
  }
  useEffect(() => {
    mounted.current = true;
    const start = setTimeout(() => {
      setCandidates(null);
      setError(null);
      setBusy(false);
      if (!["cancelling", "cancelled"].includes(runStatus ?? "")) void load();
    }, 0);
    return () => {
      clearTimeout(start);
      mounted.current = false;
      // Invalidate any view request on unmount.
      // eslint-disable-next-line react-hooks/exhaustive-deps
      token.current++;
    };
    // This view mounts once per selected Run and tab.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, runStatus, permissionRevision]);
  useEffect(() => {
    // Run completion can publish files after the first empty list was read.
    const start = setTimeout(() => void outputs(), 0);
    return () => {
      clearTimeout(start);
      // Supersede earlier lists before a new request starts or the view closes.
      // eslint-disable-next-line react-hooks/exhaustive-deps
      outputToken.current++;
    };
    // Re-query the same visible Run when its terminal state changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, terminal]);
  return (
    <section aria-label="使用资料与输出">
      <h3>候选资料</h3>
      {terminal && <p>当前接口不提供完整历史候选回放。</p>}
      {terminal && candidates && <p>先前候选快照，当前未验证。</p>}
      {error && <p role="status">{error}</p>}
      <button
        disabled={busy || ["cancelling", "cancelled"].includes(runStatus ?? "")}
        onClick={() => void load()}
      >
        刷新资料
      </button>
      {busy && <p role="status">正在同步候选资料…</p>}
      {candidates && !busy && !candidates.candidates.length && (
        <p>当前没有可展示的候选资料；不代表没有历史读取。</p>
      )}
      {candidates?.candidates.map((c) => (
        <p key={c.node_id}>
          <button
            type="button"
            onClick={() =>
              useShell.getState().openInspector({ kind: "file", objectId: c.node_id }, true)
            }
          >
            {c.logical_name || c.name}
          </button>{" "}
          · {c.content_type ?? "类型未知"}
          {c.size_bytes != null ? ` · ${c.size_bytes} 字节` : ""} · {c.fixed ? "固定资料" : "候选"}{" "}
          · {c.read ? "已读取" : "未确认读取"}
        </p>
      ))}
      {candidates?.next && (
        <button disabled={busy} onClick={() => void load(true)}>
          加载更多资料
        </button>
      )}
      <h3>已发布输出</h3>
      <button onClick={() => void outputs()}>刷新输出</button>
      {outputError && (
        <p role="alert">
          {outputError}
          <button onClick={() => void outputs()}>重试输出</button>
        </p>
      )}
      {outputLoading && <p role="status">正在加载输出…</p>}
      {!outputLoading && !files.length && !outputError && <p>暂无已发布输出。</p>}
      {files.map((f) => (
        <div key={f.file_id}>
          <a href={`/api/v1/files/${encodeURIComponent(f.file_id)}/content`} download>
            {f.name}
          </a>
          {onSaveFile && (
            <button
              onClick={() => {
                const selected = runId;
                void runApi
                  .getFile(f.file_id)
                  .then(({ file }) => {
                    if (mounted.current && selected === useRunInspector.getState().runId)
                      onSaveFile(file);
                  })
                  .catch(() => {
                    if (mounted.current) setOutputError("无法读取保存所需的文件信息，请重试。");
                  });
              }}
            >
              保存到空间
            </button>
          )}
        </div>
      ))}
      <RunApprovals terminal={terminal} />
    </section>
  );
}
export function RunApprovals({ terminal }: { terminal: boolean }) {
  const [now, setNow] = useState(() => Date.now());
  const state = useRunInspector();
  const load = state.loadApprovals;
  const id = state.runId;
  const pending = state.approvals.some((a) => a.status === "pending");
  useEffect(() => {
    let valid = true,
      timer: ReturnType<typeof setTimeout>,
      inFlight = false;
    const poll = async () => {
      if (!valid || inFlight || document.hidden) return;
      inFlight = true;
      await load();
      inFlight = false;
      if (valid) setNow(Date.now());
      if (valid && (!terminal || pending)) timer = setTimeout(() => void poll(), 5000);
    };
    const visible = () => {
      clearTimeout(timer);
      if (!document.hidden) void poll();
    };
    void poll();
    document.addEventListener("visibilitychange", visible);
    return () => {
      valid = false;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [id, load, terminal, pending]);
  const labels = {
    pending: "等待审批",
    approved: "已授权，等待执行状态更新",
    rejected: "已拒绝",
    expired: "已过期",
    cancelled: "已撤销",
    consumed: "授权已使用，请查看执行结果",
  };
  return (
    <section aria-label="文件操作审批">
      <h3>文件操作审批</h3>
      <button onClick={() => void load()}>同步审批</button>
      {state.approvalError && <p role="status">{state.approvalError}</p>}
      {state.approvalLoading && <p role="status">正在同步审批…</p>}
      {state.approvalLoaded && !state.approvals.length && !state.approvalError && (
        <p>暂无操作审批。</p>
      )}
      {state.approvals.map((a) => {
        const intent = state.intents[a.approval_id];
        const expired = Boolean(a.expires_at && Date.parse(a.expires_at) <= now);
        return (
          <article key={a.approval_id}>
            <strong>{a.action_summary}</strong>
            <p>{labels[a.status]}</p>
            {a.expires_at && <p>到期：{a.expires_at}</p>}
            <details>
              <summary>操作详情</summary>
              {a.tool_name} · {a.operation_id}
            </details>
            {intent?.message && <p role="alert">{intent.message}</p>}
            {a.status === "pending" &&
              (expired ? (
                <p>审批已到期，请重新同步。</p>
              ) : intent ? (
                intent.state === "unknown" &&
                intent.canReplay && (
                  <button onClick={() => void state.decide(a, intent.decision, true)}>
                    确认原{intent.decision === "approve" ? "允许" : "拒绝"}决策
                  </button>
                )
              ) : (
                <>
                  <button onClick={() => void state.decide(a, "approve")}>允许</button>
                  <button onClick={() => void state.decide(a, "reject")}>拒绝</button>
                </>
              ))}
          </article>
        );
      })}
    </section>
  );
}
