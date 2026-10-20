import { type HpRun, type HpRunStatus } from "../../api/types";

const TERMINAL_RUN_STATUS = new Set<HpRunStatus>(["succeeded", "failed", "cancelled"]);
export function isTerminalRunStatus(status: HpRunStatus): boolean {
  return TERMINAL_RUN_STATUS.has(status);
}

export function isCancellableRunStatus(status: HpRunStatus): boolean {
  return status === "queued" || status === "running";
}

export function isRetryableRun(run: HpRun): boolean {
  return (
    run.status === "failed" &&
    run.failure?.retryable !== false &&
    !["tool_side_effect_uncertain", "side_effect_reconciliation_failed"].includes(
      run.failure?.code ?? "",
    )
  );
}

/** Human-readable label for the run status area (progress lives here, not in content). */
export function runStatusLabel(status: HpRunStatus): string {
  switch (status) {
    case "queued":
      return "排队中…";
    case "running":
      return "运行中…";
    case "cancelling":
      return "正在停止…";
    case "succeeded":
      return "已完成";
    case "failed":
      return "运行失败";
    case "cancelled":
      return "已停止";
  }
}
