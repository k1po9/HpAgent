import { useEffect, useRef, useState } from "react";
import { api } from "../../api/client";
import type { HpNotification } from "../../api/types";
import { useWorks } from "../../store/works";
import { useShell } from "../../store/shell";
import { Surface } from "../shell/Surface";
import { commandError } from "../../utils/commands";
function summary(payload: unknown) {
  if (typeof payload === "string") return payload;
  if (payload && typeof payload === "object") {
    const p = payload as Record<string, unknown>;
    for (const k of ["content", "summary", "title"])
      if (typeof p[k] === "string") return p[k] as string;
  }
  return "已收到通知，内容格式暂不支持摘要。";
}
export function TaskInbox({ onClose }: { onClose: () => void }) {
  const [items, setItems] = useState<HpNotification[]>([]);
  const [busy, setBusy] = useState(false),
    [loaded, setLoaded] = useState(false),
    [more, setMore] = useState(false),
    [error, setError] = useState("");
  const flight = useRef(0),
    alive = useRef(false),
    lock = useRef(false),
    cursors = useRef(new Set<string>());
  const generation = useWorks((s) => s.generation);
  async function load(before?: string) {
    if (lock.current) return;
    lock.current = true;
    const token = ++flight.current;
    setBusy(true);
    setError("");
    const valid = () =>
      alive.current && flight.current === token && generation === useWorks.getState().generation;
    try {
      const result = await api.request<{ items: HpNotification[] }>({
        method: "GET",
        path: `/api/v1/notifications${before ? `?before=${encodeURIComponent(before)}` : ""}`,
      });
      if (!valid()) return;
      const next = result.items.at(-1)?.notification_id;
      const stuck = Boolean(
        before &&
        result.items.length === 100 &&
        (!next || next === before || cursors.current.has(next)),
      );
      if (next) cursors.current.add(next);
      setItems((old) =>
        Array.from(
          new Map(
            [...(before ? old : []), ...result.items].map((n) => [n.notification_id, n]),
          ).values(),
        ),
      );
      setLoaded(true);
      setMore(!stuck && result.items.length === 100);
      if (stuck) setError("通知分页未前进，请刷新后重试。");
    } catch (e) {
      if (valid()) setError(commandError(e));
    } finally {
      if (valid()) {
        lock.current = false;
        setBusy(false);
      }
    }
  }
  useEffect(() => {
    alive.current = true;
    void Promise.resolve().then(() => {
      if (alive.current) return load();
    });
    return () => {
      alive.current = false;
      lock.current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [generation]);
  return (
    <Surface title="账户收件箱" onClose={onClose}>
      <button
        disabled={busy}
        onClick={() => {
          cursors.current.clear();
          void load();
        }}
      >
        刷新收件箱
      </button>
      {busy && <p role="status">正在加载通知…</p>}
      {error && (
        <p role="alert">
          {error}
          <button
            disabled={busy}
            onClick={() => void load(more ? items.at(-1)?.notification_id : undefined)}
          >
            重试通知
          </button>
        </p>
      )}
      {loaded && !items.length && !error && <p>暂无已存入收件箱的通知。</p>}
      {items.map((item) => (
        <article className="hp-task-notification" key={item.notification_id}>
          <p>{summary(item.payload)}</p>
          <p className="hp-muted">
            {new Date(item.created_at).toLocaleString()} · 已存入账户收件箱，不代表已读
          </p>
          {item.work_id && (
            <button
              onClick={() => {
                onClose();
                useShell.getState().openInspector({ kind: "task", objectId: item.work_id! });
              }}
            >
              查看任务
            </button>
          )}
          {item.run_id && (
            <button
              onClick={() => {
                onClose();
                useShell.getState().openInspector({
                  kind: "run",
                  objectId: item.run_id!,
                  origin: item.work_id ? { workId: item.work_id } : undefined,
                });
              }}
            >
              查看执行
            </button>
          )}
          <details>
            <summary>通知详情</summary>
            <pre>{JSON.stringify(item.payload, null, 2)}</pre>
          </details>
        </article>
      ))}
      {more && (
        <button disabled={busy} onClick={() => void load(items.at(-1)?.notification_id)}>
          加载更多通知
        </button>
      )}
    </Surface>
  );
}
