import { Box, Button, Flex, Spinner, Text } from "@radix-ui/themes";
import type { HpRun } from "../api/types";
import type { RunProgress } from "../sse/runFeed";
import { isCancellableRunStatus, isRetryableRunStatus, runStatusLabel } from "../store/workbench";
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
}

/**
 * The run status strip (phase-e E-04/E-06).
 *
 * Progress/state of the active Run lives here, never inside Message content.
 * While a Run is running the composer is gated; this strip offers Stop. A
 * failed/cancelled Run offers Retry, which reuses the original user message.
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
  const retryable = activeRun !== null && isRetryableRunStatus(activeRun.status);
  const running = activeRun !== null && !isRetryableRunStatus(activeRun.status);

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
