import { useWorkbench } from "../../store/workbench";
import { useShell } from "../../store/shell";
import { useApprovalFact } from "../vitality/useVitality";
import { RunStatus } from "../RunStatus";
import { useRef } from "react";
import { useRunCard } from "./useRunCard";
import { runStatusLabel } from "../../store/workbench";
import { ChevronRight } from "lucide-react";

export function ExecutionBlock({ runId, messageId }: { runId: string; messageId?: string }) {
  const active = useWorkbench((s) => s.activeRun);
  const progress = useWorkbench((s) => s.activeRunProgress);
  const degraded = useWorkbench((s) => s.degraded);
  const stopping = useWorkbench((s) => s.stopping);
  const generation = useWorkbench((s) => s.pollGeneration);
  const pending = useApprovalFact(runId) === "pending";
  const busy = useWorkbench((s) => s.activeRunError);
  const run = active?.run_id === runId ? active : null;
  const cardRef = useRef<HTMLElement>(null);
  const history = useRunCard(runId, Boolean(run), cardRef);
  const record = run ?? history;
  const timestamp = record?.finished_at ?? record?.started_at ?? record?.created_at;
  const date = timestamp ? new Date(timestamp) : null;
  const elapsed =
    record?.finished_at && record.started_at
      ? Date.parse(record.finished_at) - Date.parse(record.started_at)
      : NaN;
  const details = (
    <button
      className="hp-run-detail"
      aria-label="查看执行详情"
      title={`Run · ${runId}`}
      id={`run-open-${runId}`}
      type="button"
      onClick={() =>
        useShell.getState().openInspector({
          kind: "run",
          objectId: runId,
          origin: {
            conversationId: useWorkbench.getState().activeConversationId ?? undefined,
            messageId,
            triggerId: `run-open-${runId}`,
          },
        })
      }
    >
      <ChevronRight size={16} aria-hidden="true" />
      <span className="hp-run-reference-kind" aria-hidden="true">
        RUN
      </span>
      <span className="hp-run-reference-copy">
        <span>查看执行详情</span>
        {!run && (
          <span className="hp-run-reference-caption">
            对话执行 ·{" "}
            {record
              ? record.agent_strategy === "plan_and_execute"
                ? "计划执行"
                : "ReAct"
              : "执行记录"}
          </span>
        )}
        <code className="hp-run-reference-id">{runId}</code>
      </span>
      {!run && (
        <>
          <span className="hp-run-card-status" data-status={record?.status ?? "unknown"}>
            <span className="hp-run-card-dot" data-run-relay-status aria-hidden="true" />
            <span>{record ? runStatusLabel(record.status) : "状态待同步"}</span>
          </span>
          <span className="hp-run-card-meta">
            {date && Number.isFinite(date.getTime()) && (
              <time dateTime={timestamp!}>
                {date.toLocaleString("zh-CN", {
                  month: "2-digit",
                  day: "2-digit",
                  hour: "2-digit",
                  minute: "2-digit",
                  hour12: false,
                })}
              </time>
            )}
            {Number.isFinite(elapsed) && elapsed >= 0 && (
              <span>用时 {(elapsed / 1000).toFixed(1)} 秒</span>
            )}
            {record?.budget && (
              <span>{record.budget.model_total_tokens_used.toLocaleString()} tokens</span>
            )}
          </span>
        </>
      )}
    </button>
  );
  return (
    <section ref={cardRef} className="hp-execution-block" aria-label="消息执行状态">
      {run && (
        <RunStatus
          activeRun={run}
          generation={generation}
          progress={progress}
          degraded={degraded}
          stopping={stopping}
          busyMessage={busy}
          actions={details}
          onStop={() => {
            const state = useWorkbench.getState();
            if (state.activeRun?.run_id === runId) void state.stopRun();
          }}
          onRetry={() => {
            const state = useWorkbench.getState();
            if (state.activeRun?.run_id === runId) void state.retryRun();
          }}
        />
      )}
      {pending && <p role="status">有待处理的操作审批</p>}
      {!run && details}
    </section>
  );
}
