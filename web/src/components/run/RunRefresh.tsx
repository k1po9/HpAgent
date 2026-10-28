import { IconButton } from "../ui";
import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { useRunInspector } from "../../store/runInspector";
import { useTraceStore } from "../trace/traceStore";
import type { Inspector } from "../../store/shell";

export function RunRefresh({ inspector }: { inspector: Inspector }) {
  const loading = useRunInspector((s) => s.loading);
  const error = useRunInspector((s) => (s.runId === inspector.objectId ? s.error : null));
  const [pending, setPending] = useState(false);
  const [feedback, setFeedback] = useState("");
  const advanced = inspector.tab === "advanced";
  const scope = advanced ? "刷新执行状态与执行轨迹" : "刷新执行状态";
  const description = `${scope}；资料、输出和审批请在各自区域刷新`;
  return (
    <span className="hp-run-refresh" data-feedback={error || feedback ? "true" : undefined}>
      <IconButton
        type="button"
        className="hp-run-refresh-button"
        aria-label={scope}
        aria-describedby="run-refresh-hint"
        title={description}
        aria-busy={pending || loading}
        disabled={pending || loading}
        onClick={async () => {
          setPending(true);
          setFeedback("");
          try {
            const jobs = [useRunInspector.getState().load()];
            if (advanced) {
              useTraceStore.getState().clearModelInputs();
              jobs.push(useTraceStore.getState().loadTrace());
            }
            await Promise.all(jobs);
            setFeedback(
              useRunInspector.getState().error || (advanced && useTraceStore.getState().error)
                ? "刷新未完成，请重试"
                : "已刷新",
            );
          } catch {
            setFeedback("刷新未完成，请重试");
          } finally {
            setPending(false);
          }
        }}
      >
        <RefreshCw size={17} aria-hidden="true" />
      </IconButton>
      <span id="run-refresh-hint" className="hp-run-refresh-hint" role="status">
        {pending ? "正在刷新…" : error ? "执行状态刷新失败，请重试" : feedback || description}
      </span>
    </span>
  );
}
