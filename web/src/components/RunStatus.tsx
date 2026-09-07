import { Box, Button, Flex, Spinner, Text } from "@radix-ui/themes";
import type { HpRun } from "../api/types";
import type { RunProgress } from "../sse/runFeed";
import {
  isCancellableRunStatus,
  isRetryableRun,
  isTerminalRunStatus,
  runStatusLabel,
} from "../store/workbench";
import { phaseLabel } from "./progressLabels";
import { formatTokenCount, tokenUsagePrefix } from "../utils/tokenUsage";

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
}: RunStatusProps) {
  if (!activeRun && !busyMessage && !progress) {
    return null;
  }

  const cancellable = activeRun !== null && isCancellableRunStatus(activeRun.status);
  const retryable = activeRun !== null && isRetryableRun(activeRun);
  const running = activeRun !== null && !isTerminalRunStatus(activeRun.status);
  const unsafeSideEffect = activeRun?.status === "failed" && activeRun.failure?.retryable === false;
  const budget = activeRun?.budget;

  return (
    <Box className="hp-runstrip">
      {busyMessage ? (
        <Text size="2" color="red" role="status">
          {busyMessage}
        </Text>
      ) : null}
      {activeRun ? (
        <Flex gap="3" align="center">
          {running ? (
            <Spinner size="1" data-testid="run-spinner" />
          ) : (
            <Text size="2" color="gray" aria-hidden="true">
              ✓
            </Text>
          )}
          <Text size="2" weight="medium" data-testid="run-label">
            {runStatusLabel(activeRun.status)}
          </Text>
          {degraded && running ? (
            <Text size="2" color="orange" role="status" data-testid="run-degraded">
              连接中断，任务仍在执行…
            </Text>
          ) : null}
          {progress ? (
            <Text size="2" color="gray" data-testid="run-progress">
              {progress.summary || phaseLabel(progress.phase)}
            </Text>
          ) : null}
          {budget && budget.usage_state !== "none" ? (
            <Text size="2" color="gray" data-testid="run-token-usage">
              {tokenUsagePrefix(budget)}
              {formatTokenCount(budget.tokens.total.used)} tokens
              {budget.tokens.total.reserved > 0
                ? ` · ≤${formatTokenCount(budget.tokens.total.reserved)} 预留`
                : ""}
              {budget.model_calls.total_attempts > 0
                ? ` · ${budget.model_calls.total_attempts} 次模型请求`
                : ""}
              {budget.model_calls.unmetered > 0 ? " · 部分用量无法确认" : ""}
            </Text>
          ) : null}
          {unsafeSideEffect ? (
            <Text size="2" color="red" role="alert" data-testid="unsafe-retry-message">
              任务中存在无法确认是否已完成的外部操作，请检查结果后重新发起任务。
            </Text>
          ) : null}
          {cancellable ? (
            <Button
              size="1"
              variant="soft"
              color="red"
              onClick={onStop}
              disabled={stopping}
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
        </Flex>
      ) : null}
    </Box>
  );
}
