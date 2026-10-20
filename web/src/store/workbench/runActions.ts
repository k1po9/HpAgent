import { newIdempotencyKey } from "../../utils/idempotency";
import { HpCommandError } from "../../api/types";
import type { WorkbenchContext } from "./context";
import type { WorkbenchState } from "./types";
import type { RunMonitor } from "./runMonitor";
import { isTerminalRunStatus, isCancellableRunStatus, isRetryableRun } from "./runStatus";
import { reconcileAssistantMessage, messageErrorText } from "./messages";

export function runActions(
  context: WorkbenchContext,
  monitor: RunMonitor,
): Pick<WorkbenchState, "setAgentStrategy" | "stopRun" | "retryRun" | "refreshActiveRun"> {
  const { api, set, get, fence } = context;
  const { startRunMonitor, refreshActiveRun } = monitor;

  return {
    setAgentStrategy: (agentStrategy) => {
      const active = get().activeRun;
      if (active && !isTerminalRunStatus(active.status)) return;
      set({ agentStrategy });
    },
    stopRun: async () => {
      const valid = fence();
      const run = get().activeRun;
      if (!run || !isCancellableRunStatus(run.status) || get().stopping) return;
      set({ stopping: true, error: null });
      try {
        await api.cancelRun(run.run_id, newIdempotencyKey());
        // The SSE monitor (or its polling fallback) observes the terminal
        // cancelled snapshot.
      } catch (err) {
        if (!valid()) return;
        if (err instanceof HpCommandError && err.code === "run_not_cancellable") {
          void refreshActiveRun();
        } else {
          set({ error: messageErrorText(err) });
        }
      } finally {
        if (valid()) set({ stopping: false });
      }
    },
    retryRun: async () => {
      const valid = fence();
      const run = get().activeRun;
      if (!run || !isRetryableRun(run)) return;
      if (get().sending || get().stopping) return;
      set({ sending: true, error: null });
      try {
        const result = await api.retryRun(run.run_id, newIdempotencyKey());
        if (!valid()) return;
        set((s) => ({
          sending: false,
          messages: reconcileAssistantMessage(s.messages, result.assistant_message),
          activeRun: result.run,
          activeRunError: null,
          activeRunProgress: null,
          degraded: false,
          pollGeneration: s.pollGeneration + 1,
        }));
        startRunMonitor(result.run.run_id);
      } catch (err) {
        if (!valid()) return;
        if (err instanceof HpCommandError && err.code === "conversation_busy") {
          set({ sending: false, activeRunError: err.error.message });
          void refreshActiveRun();
        } else if (err instanceof HpCommandError && err.code === "run_not_retryable") {
          set({ sending: false, error: "当前状态不可重试。" });
          void refreshActiveRun();
        } else if (err instanceof HpCommandError && err.code === "run_retry_not_safe") {
          set({
            sending: false,
            error: "任务中存在无法确认是否已完成的外部操作，请检查结果后重新发起任务。",
          });
          void refreshActiveRun();
        } else {
          set({ sending: false, error: messageErrorText(err) });
        }
      }
    },
    refreshActiveRun,
  };
}
