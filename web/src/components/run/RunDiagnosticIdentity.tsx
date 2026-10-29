import { useEffect, useState } from "react";
import { Check, Copy } from "lucide-react";
import type { HpRunSnapshot } from "../../api/types";
import { runApi } from "../../store/runInspector";

export function RunDiagnosticIdentity({ snapshot }: { snapshot: HpRunSnapshot }) {
  const id = snapshot.run.run_id;
  const [resolved, setResolved] = useState<{ id: string; ordinal?: number } | null>(null);
  const [feedback, setFeedback] = useState("");
  const [copying, setCopying] = useState(false);
  const ordinal = snapshot.run.run_ordinal ?? (resolved?.id === id ? resolved.ordinal : undefined);
  useEffect(() => {
    if (snapshot.run.run_ordinal) return;
    const controller = new AbortController();
    void runApi
      .getRun(id, controller.signal)
      .then((data) => {
        if (!controller.signal.aborted && data.run.run_id === id)
          setResolved({ id, ordinal: data.run.run_ordinal });
      })
      .catch(() => {});
    return () => controller.abort();
  }, [id, snapshot.run.run_ordinal]);
  useEffect(() => {
    if (!feedback) return;
    const timer = setTimeout(() => setFeedback(""), 2500);
    return () => clearTimeout(timer);
  }, [feedback]);
  return (
    <header className="hp-diagnostic-identity">
      <div>
        <span className="hp-diagnostic-eyebrow">执行记录</span>
        <h3>
          {snapshot.source_kind === "work" ? "所属任务" : "本会话"}
          {ordinal && ordinal > 0 ? ` · 第 ${ordinal} 次执行` : " · 本次执行"}
        </h3>
      </div>
      <button
        type="button"
        className="hp-diagnostic-copy"
        aria-label="复制完整执行标识"
        title="复制完整执行标识"
        disabled={copying}
        onClick={async () => {
          setCopying(true);
          try {
            await navigator.clipboard.writeText(id);
            setFeedback("执行标识已复制");
          } catch {
            setFeedback("复制失败，请重试");
          } finally {
            setCopying(false);
          }
        }}
      >
        {feedback === "执行标识已复制" ? <Check size={15} /> : <Copy size={15} />}
      </button>
      <span className="hp-diagnostic-copy-feedback" role="status">
        {feedback}
      </span>
      {snapshot.source_kind === "chat" && snapshot.run.retry_of_run_id && (
        <small className="hp-diagnostic-retry">由此前执行重试创建</small>
      )}
    </header>
  );
}
