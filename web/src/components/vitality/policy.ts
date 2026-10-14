import type { HpRunStatus } from "../../api/types";
import type { RunProgress } from "../../sse/runFeed";
import { phaseLabel } from "../progressLabels";
import { runStatusLabel } from "../../store/workbench";
export type VitalityState =
  | "running"
  | "thinking"
  | "tool"
  | "waiting"
  | "succeeded"
  | "failed"
  | "cancelling"
  | "cancelled"
  | "idle";
export type ApprovalFact = "unknown" | "none" | "pending";
export const PROGRESS_FRESH_MS = 30_000;
export interface VitalityView {
  state: VitalityState;
  runText: string;
  caption: string;
  phase: string | null;
  progressText: string | null;
  terminal: boolean;
  approvals: ApprovalFact;
}
export function deriveVitality(
  run: { run_id: string; status: HpRunStatus } | null,
  context: {
    progress?: RunProgress | null;
    generation?: number;
    now: number;
    degraded?: boolean;
    approvals?: ApprovalFact;
  },
): VitalityView {
  if (!run)
    return {
      state: "idle",
      runText: "暂无执行",
      caption: "阶段未提供",
      phase: null,
      progressText: null,
      terminal: false,
      approvals: "unknown",
    };
  const terminal = ["succeeded", "failed", "cancelled"].includes(run.status);
  const hint = context.progress,
    scope = hint?.scope;
  const age = scope ? context.now - scope.receivedAt : Infinity;
  const trusted =
    run.status === "running" &&
    !context.degraded &&
    scope?.runId === run.run_id &&
    scope.generation === context.generation &&
    age >= 0 &&
    age < PROGRESS_FRESH_MS;
  const phase = trusted ? hint!.phase : null;
  const progressText = phase ? hint!.summary || phaseLabel(phase) : null;
  const approvals = context.approvals ?? "unknown";
  let state: VitalityState = "running",
    caption = progressText ?? "正在持续执行";
  if (terminal) {
    state = run.status as "succeeded" | "failed" | "cancelled";
    caption =
      run.status === "succeeded"
        ? "本次执行已完成"
        : run.status === "failed"
          ? "本次执行失败"
          : "执行已停止";
  } else if (run.status === "cancelling") {
    state = "cancelling";
    caption = "等待停止确认";
  } else if (run.status === "queued") {
    state = "waiting";
    caption = "等待执行资源";
  } else if (approvals === "pending") {
    state = "waiting";
    caption = "等待操作审批";
  } else if (phase === "waiting_for_account_execution") {
    state = "waiting";
    caption = phaseLabel(phase);
  } else if (["calling_model", "planning", "replanning", "synthesizing"].includes(phase ?? ""))
    state = "thinking";
  else if (["selecting_tools", "executing_tool"].includes(phase ?? "")) state = "tool";
  return {
    state,
    runText: runStatusLabel(run.status),
    caption,
    phase,
    progressText,
    terminal,
    approvals,
  };
}
