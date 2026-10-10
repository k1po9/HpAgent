import { useWorkbench } from "../../store/workbench";
import { useShell } from "../../store/shell";
import { useRunInspector } from "../../store/runInspector";
import { RunStatus } from "../RunStatus";
import { ChevronRight } from "lucide-react";

export function ExecutionBlock({ runId, messageId }: { runId: string; messageId?: string }) {
  const active = useWorkbench((s) => s.activeRun);
  const progress = useWorkbench((s) => s.activeRunProgress);
  const degraded = useWorkbench((s) => s.degraded);
  const stopping = useWorkbench((s) => s.stopping);
  const pending = useRunInspector(
    (s) => s.runId === runId && s.approvals.some((a) => a.status === "pending"),
  );
  const busy = useWorkbench((s) => s.activeRunError);
  const run = active?.run_id === runId ? active : null;
  const details = (
    <button
      className="hp-run-detail"
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
      查看执行详情
    </button>
  );
  return (
    <section className="hp-execution-block" aria-label="消息执行状态">
      {run && (
        <RunStatus
          activeRun={run}
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
