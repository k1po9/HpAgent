import { useCallback, useEffect } from "react";
import { Box, Button, Flex, Spinner, Text } from "@radix-ui/themes";
import { Activity } from "lucide-react";
import { HpThread } from "../adapters/assistant-ui/HpThread";
import { useWorkbench } from "../store/workbench";
import { useAuth } from "../store/auth";
import { useArtifacts } from "../store/artifacts";
import { RunStatus } from "./RunStatus";
import { useTraceStore } from "./trace/traceStore";
import { ApprovalCard } from "./ApprovalCard";

/**
 * The active conversation pane (phase-e E-03/E-04).
 *
 * Composes the assistant-ui chat surface with the run-status strip. The store
 * owns all authoritative state; this component only maps store → UI.
 */
export function ChatPane() {
  const messages = useWorkbench((s) => s.messages);
  const activeRun = useWorkbench((s) => s.activeRun);
  const activeRunError = useWorkbench((s) => s.activeRunError);
  const activeRunProgress = useWorkbench((s) => s.activeRunProgress);
  const degraded = useWorkbench((s) => s.degraded);
  const stopping = useWorkbench((s) => s.stopping);
  const sending = useWorkbench((s) => s.sending);
  const attachments = useWorkbench((s) => s.attachments);
  const addAttachments = useWorkbench((s) => s.addAttachments);
  const removeAttachment = useWorkbench((s) => s.removeAttachment);
  const agentStrategy = useWorkbench((s) => s.agentStrategy);
  const durableAgentEnabled = useAuth((s) => Boolean(s.capabilities.durable_agent));
  const fileUploadEnabled = useAuth((s) => Boolean(s.capabilities.file_upload));
  const loadingMessages = useWorkbench((s) => s.loadingMessages);
  const hasMoreMessages = useWorkbench((s) => s.hasMoreMessages);
  const loadingMoreMessages = useWorkbench((s) => s.loadingMoreMessages);
  const error = useWorkbench((s) => s.error);
  const sendMessage = useWorkbench((s) => s.sendMessage);
  const stopRun = useWorkbench((s) => s.stopRun);
  const retryRun = useWorkbench((s) => s.retryRun);
  const loadMoreMessages = useWorkbench((s) => s.loadMoreMessages);
  const clearError = useWorkbench((s) => s.clearError);
  const artifactError = useArtifacts((s) => s.error);
  const clearArtifactError = useArtifacts((s) => s.clearError);
  const setAgentStrategy = useWorkbench((s) => s.setAgentStrategy);
  const strategyLocked =
    sending ||
    Boolean(activeRun && !["completed", "failed", "cancelled"].includes(activeRun.status));
  const displayedStrategy = activeRun?.agent_strategy ?? agentStrategy;
  const traceOpen = useTraceStore((s) => s.open);
  const setTraceOpen = useTraceStore((s) => s.setOpen);
  const followTraceRun = useTraceStore((s) => s.followRun);
  const latestRunId =
    activeRun?.run_id ??
    messages
      .slice()
      .reverse()
      .find((message) => message.role === "assistant" && message.produced_by_run_id)
      ?.produced_by_run_id ??
    null;

  useEffect(() => {
    followTraceRun(latestRunId);
  }, [latestRunId, followTraceRun]);

  const handleSend = useCallback((content: string) => sendMessage(content), [sendMessage]);
  const handleFilesSelected = useCallback(
    (files: File[]) => {
      void addAttachments(files);
    },
    [addAttachments],
  );
  const handleRemoveAttachment = useCallback(
    (localId: string) => {
      void removeAttachment(localId);
    },
    [removeAttachment],
  );
  const handleCancel = useCallback(() => {
    void stopRun();
  }, [stopRun]);
  const handleRetry = useCallback(() => {
    void retryRun();
  }, [retryRun]);
  const handleLoadMore = useCallback(() => {
    void loadMoreMessages();
  }, [loadMoreMessages]);
  const handleDismissError = useCallback(() => {
    clearError();
  }, [clearError]);

  if (loadingMessages) {
    return (
      <Flex align="center" justify="center" gap="2" style={{ height: "100%" }}>
        <Spinner />
        <Text size="2" color="gray">
          正在加载对话…
        </Text>
      </Flex>
    );
  }

  return (
    <Flex direction="column" style={{ height: "100%" }}>
      <RunStatus
        activeRun={activeRun}
        busyMessage={activeRunError}
        progress={activeRunProgress}
        degraded={degraded}
        stopping={stopping}
        onStop={handleCancel}
        onRetry={handleRetry}
      />
      <ApprovalCard runId={activeRun?.run_id ?? null} />
      {error ? (
        <button type="button" className="hp-error" onClick={handleDismissError}>
          <Text size="2" color="red">
            {error}（点击关闭）
          </Text>
        </button>
      ) : null}
      {artifactError ? (
        <button type="button" className="hp-error" onClick={clearArtifactError}>
          <Text size="2" color="red">
            Artifact：{artifactError}（点击关闭）
          </Text>
        </button>
      ) : null}
      {hasMoreMessages ? (
        <Flex justify="center" className="hp-loadmore">
          <Button size="1" variant="soft" onClick={handleLoadMore} disabled={loadingMoreMessages}>
            {loadingMoreMessages ? "加载中…" : "加载更早的消息"}
          </Button>
        </Flex>
      ) : null}
      <Flex align="center" justify="between" gap="2" px="3" py="2">
        <Flex align="center" gap="2">
          <Text size="1" color="gray">
            执行模式
          </Text>
          <select
            aria-label="执行模式"
            value={displayedStrategy}
            disabled={strategyLocked}
            onChange={(event) =>
              setAgentStrategy(event.target.value as "react" | "plan_and_execute")
            }
          >
            <option value="react">对话（ReAct）</option>
            {durableAgentEnabled ? <option value="plan_and_execute">计划执行</option> : null}
          </select>
        </Flex>
        <Button
          size="1"
          variant={traceOpen ? "solid" : "soft"}
          disabled={!latestRunId}
          onClick={() => setTraceOpen(!traceOpen)}
        >
          <Activity size={14} aria-hidden="true" /> Trace
        </Button>
      </Flex>
      <Box style={{ flex: 1, minHeight: 0 }}>
        <HpThread
          messages={messages}
          activeRun={activeRun}
          attachments={attachments}
          fileUploadEnabled={fileUploadEnabled}
          sendDisabled={sending || attachments.some((attachment) => attachment.status !== "ready")}
          onFilesSelected={handleFilesSelected}
          onRemoveAttachment={handleRemoveAttachment}
          onSend={handleSend}
          onCancel={handleCancel}
        />
      </Box>
    </Flex>
  );
}
