import { Box, Button, Flex, Spinner, Text } from "@radix-ui/themes";
import { useEffect, useState, type ReactNode } from "react";
import type { HpRun } from "../api/types";
import type { RunProgress } from "../sse/runFeed";
import {
  isCancellableRunStatus,
  isRetryableRun,
  isTerminalRunStatus,
  runStatusLabel,
} from "../store/workbench";
import { phaseLabel } from "./progressLabels";

interface RunStatusProps {
  activeRun: HpRun | null;
  busyMessage: string | null;
  /** Volatile `run.progress` hint; never written into Message content (E-06). */
  progress: RunProgress | null;
  /** SSE degraded/connection lost: recovering by querying the Run. */
  degraded: boolean;
  stopping: boolean;
  onStop: () => void;
  onRetry: () => void;
  actions?: ReactNode;
}

/**
 * The run status strip (phase-e E-04/E-06).
 *
 * Progress/state of the active Run lives here, never inside Message content.
 * While a Run is running the composer is gated; this strip offers Stop. A
 * safely retryable failed Run offers Retry, which reuses the
 * original user message. Uncertain external side effects require manual review.
 */
export function RunStatus({
  activeRun,
  busyMessage,
  progress,
  degraded,
  stopping,
  onStop,
  onRetry,
  actions,
}: RunStatusProps) {
  const [warnedRun, setWarnedRun] = useState<string | null>(null);
  const queuedRunId = activeRun?.status === "queued" ? activeRun.run_id : null;
  const queuedCreatedAt = activeRun?.status === "queued" ? activeRun.created_at : null;
  useEffect(() => {
    if (!queuedRunId || !queuedCreatedAt) return;
    const elapsed = Date.now() - Date.parse(queuedCreatedAt);
    if (!Number.isFinite(elapsed)) return;
    const delay = Math.max(0, 60_000 - elapsed);
    const timer = window.setTimeout(() => setWarnedRun(queuedRunId), delay);
    return () => window.clearTimeout(timer);
  }, [queuedRunId, queuedCreatedAt]);
  if (!activeRun && !busyMessage && !progress) {
    return null;
  }

  const cancellable = activeRun !== null && isCancellableRunStatus(activeRun.status);
  const retryable = activeRun !== null && isRetryableRun(activeRun);
  const running = activeRun !== null && !isTerminalRunStatus(activeRun.status);
  const unsafeSideEffect =
    activeRun?.status === "failed" &&
    ["tool_side_effect_uncertain", "side_effect_reconciliation_failed"].includes(
      activeRun.failure?.code ?? "",
    );

  return (
    <Box
      className="hp-runstrip"
      data-terminal={Boolean(activeRun && !running)}
      data-status={activeRun?.status}
    >
      {busyMessage ? (
        <Text className="hp-run-feedback" size="2" color="red" role="status">
          {busyMessage}
        </Text>
      ) : null}
      {activeRun ? (
        <Flex className="hp-run-content" direction="column" gap="3">
          <div className="hp-run-heading">
            {running ? (
              <Spinner size="2" data-testid="run-spinner" />
            ) : (
              <Text size="2" color="gray" aria-hidden="true">
                {activeRun.status === "succeeded" ? "✓" : activeRun.status === "failed" ? "!" : "■"}
              </Text>
            )}
            <Text size="2" weight="medium" role="status" data-testid="run-label">
              {runStatusLabel(activeRun.status)}
            </Text>
          </div>
          <div className="hp-run-description">
            {activeRun.status === "queued" && warnedRun === activeRun.run_id ? (
              <Text size="2" color="orange" role="status">
                仍在等待执行 Worker 接管
              </Text>
            ) : null}
            {degraded && running ? (
              <Text size="2" color="orange" role="status" data-testid="run-degraded">
                连接中断，正在同步执行状态
              </Text>
            ) : null}
            {progress ? (
              <Text size="2" color="gray" data-testid="run-progress">
                {progress.summary || phaseLabel(progress.phase)}
              </Text>
            ) : null}
            {unsafeSideEffect ? (
              <Text size="2" color="red" role="alert" data-testid="unsafe-retry-message">
                任务中存在无法确认是否已完成的外部操作，请检查结果后重新发起任务。
              </Text>
            ) : null}
            {activeRun.status === "failed" && activeRun.failure?.message ? (
              <Text size="2" color="red" role="status" data-testid="run-failure-message">
                {activeRun.failure.message}
              </Text>
            ) : null}
          </div>
          <div className="hp-run-actions">
            {actions}
            {cancellable ? (
              <Button
                size="1"
                variant="soft"
                color="red"
                onClick={onStop}
                disabled={stopping || activeRun.status === "cancelling"}
                data-testid="stop-run"
              >
                {stopping ? "正在停止…" : "停止"}
              </Button>
            ) : null}
            {retryable ? (
              <Button size="1" variant="soft" onClick={onRetry} data-testid="retry-run">
                重试
              </Button>
            ) : null}
          </div>
        </Flex>
      ) : null}
    </Box>
  );
}
