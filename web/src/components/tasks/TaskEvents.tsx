import { useEffect, useRef, useState } from "react";
import { api } from "../../api/client";
import type { HpWorkEvent } from "../../api/types";
import { useWorks } from "../../store/works";
export function TaskEvents({ workId }: { workId: string }) {
  const [items, setItems] = useState<HpWorkEvent[]>([]),
    [error, setError] = useState("");
  const [retry, setRetry] = useState(0),
    [loading, setLoading] = useState(false);
  const cursor = useRef(0);
  const generation = useWorks((s) => s.generation);
  useEffect(() => {
    const abort = new AbortController();
    let valid = true;
    void (async () => {
      setLoading(true);
      setError("");
      try {
        let count = 0;
        do {
          const result = await api.request<{ items: HpWorkEvent[] }>({
            method: "GET",
            path: `/api/v1/works/${workId}/events?after=${cursor.current}`,
            signal: abort.signal,
          });
          if (!valid || generation !== useWorks.getState().generation) return;
          const next = result.items.at(-1)?.event_seq ?? cursor.current;
          if (result.items.length && next <= cursor.current) throw new Error("事件游标未前进。");
          setItems((old) =>
            Array.from(new Map([...old, ...result.items].map((e) => [e.event_id, e])).values()),
          );
          cursor.current = next;
          count = result.items.length;
        } while (count === 100);
      } catch {
        if (valid && generation === useWorks.getState().generation)
          setError("需求修订事件同步失败，已加载记录保留。");
      } finally {
        if (valid && generation === useWorks.getState().generation) setLoading(false);
      }
    })();
    return () => {
      valid = false;
      abort.abort();
    };
  }, [workId, retry, generation]);
  return (
    <section>
      <h4>需求修订事件</h4>
      <p>仅展示事件与修改原因；当前接口不提供完整历史要求差异。</p>
      {loading && <p role="status">正在加载修订事件…</p>}
      {items
        .filter((e) => e.event_type === "revised")
        .map((e) => (
          <p key={e.event_id}>
            r{e.requirement_revision} ·{" "}
            {typeof e.bounded_payload.reason === "string" ? e.bounded_payload.reason : "已修订"}
          </p>
        ))}
      {!loading && !items.some((e) => e.event_type === "revised") && !error && (
        <p>暂无修订事件。</p>
      )}
      {error && (
        <p role="alert">
          {error}
          <button onClick={() => setRetry((v) => v + 1)}>继续加载事件</button>
        </p>
      )}
    </section>
  );
}
