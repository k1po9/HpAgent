import { useCallback, useEffect } from "react";
import { Box, Button, Flex, Spinner, Text } from "@radix-ui/themes";
import { Activity } from "lucide-react";
import { HpThread } from "../adapters/assistant-ui/HpThread";
import { isTerminalRunStatus, useWorkbench } from "../store/workbench";
import { useAuth } from "../store/auth";
import { useArtifacts } from "../store/artifacts";
import { RunStatus } from "./RunStatus";
import { useTraceStore } from "./trace/traceStore";
import { useShell } from "../store/shell";
import { emptyConversationUi, useConversationUi } from "../store/conversationUi";
import { ConversationHeader } from "./conversation/ConversationHeader";
import { ConversationResources } from "./conversation/ConversationResources";
import type { HpFile } from "../api/types";

/**
 * The active conversation pane (phase-e E-03/E-04).
 *
 * Composes the assistant-ui chat surface with the run-status strip. The store
 * owns all authoritative state; this component only maps store → UI.
 */
export function ChatPane({
  onSaveFile,
  resourceRefresh = 0,
}: {
  onSaveFile?: (file: HpFile) => void;
  resourceRefresh?: number;
}) {
  const conversationId = useWorkbench((s) => s.activeConversationId);
  const accountId = useAuth((s) => s.account?.account_id ?? "anonymous");
  const conversationKey = `${accountId}:${conversationId ?? "new"}`;
  const ui = useConversationUi((s) => s.entries[conversationKey] ?? emptyConversationUi);
  const ensure = useCallback(async () => {
    const shell = useShell.getState();
    const token = shell.requestToken;
    const oldId = useWorkbench.getState().activeConversationId;
    const id = await useWorkbench.getState().ensureConversation();
    if (
      !id ||
      token !== useShell.getState().requestToken ||
      accountId !== (useAuth.getState().account?.account_id ?? "anonymous")
    )
      return null;
    if (!oldId) {
      useShell.getState().navigate({ screen: "ai", conversationId: id }, true);
    }
    return id;
  }, [accountId]);
  const messages = useWorkbench((s) => s.messages);
  const activeRun = useWorkbench((s) => s.activeRun);
  const activeRunError = useWorkbench((s) => s.activeRunError);
  const activeRunProgress = useWorkbench((s) => s.activeRunProgress);
  const degraded = useWorkbench((s) => s.degraded);
  const stopping = useWorkbench((s) => s.stopping);
  const sending = useWorkbench((s) => s.sending);
  const attachments = useWorkbench((s) => s.attachments);
  const fileCandidates = useWorkbench((s) => s.fileCandidates);
  const fileCandidatesNext = useWorkbench((s) => s.fileCandidatesNext);
  const loadFileCandidates = useWorkbench((s) => s.loadFileCandidates);
  const selectExistingFile = useWorkbench((s) => s.selectExistingFile);
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
  const pendingSend = useWorkbench((s) =>
    Boolean(s.activeConversationId && s.pendingSendIds.includes(s.activeConversationId)),
  );
  const creating = useWorkbench((s) => s.creatingConversation);
  const strategyLocked =
    sending || creating || Boolean(activeRun && !isTerminalRunStatus(activeRun.status));
  const displayedStrategy = strategyLocked
    ? (activeRun?.agent_strategy ?? agentStrategy)
    : durableAgentEnabled
      ? agentStrategy
      : "react";
  useEffect(() => {
    setAgentStrategy(durableAgentEnabled ? ui.strategy : "react");
  }, [conversationKey, ui.strategy, durableAgentEnabled, setAgentStrategy]);
  const loadingCandidates = useWorkbench((s) => s.loadingFileCandidates);
  const candidatesError = useWorkbench((s) => s.fileCandidatesError);
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

  const handleSend = useCallback(
    async (content: string) => {
      const id = await ensure();
      if (!id) return false;
      const key = `${accountId}:${id}`;
      const submitted = useConversationUi.getState().entries[key];
      const ok = await sendMessage(content);
      const current = useConversationUi.getState().entries[key];
      if (
        ok &&
        current &&
        submitted &&
        current.revision === submitted.revision &&
        current.text === content
      )
        useConversationUi.getState().update(key, { text: "", revision: current.revision + 1 });
      return ok;
    },
    [sendMessage, ensure, accountId],
  );
  const handleFilesSelected = useCallback(
    (files: File[]) => {
      void ensure().then((id) => {
        if (id) void addAttachments(files);
      });
    },
    [addAttachments, ensure],
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

  return (
    <Flex direction="column" style={{ height: "100%" }}>
      {loadingMessages && (
        <Flex align="center" justify="center" gap="2">
          <Spinner />
          <Text>正在加载对话…</Text>
        </Flex>
      )}
      <ConversationHeader />
      {pendingSend && !sending && (
        <button
          type="button"
          onClick={() => {
            const content = useWorkbench.getState().pendingSendContent[conversationId!];
            const submitted = useConversationUi.getState().entries[conversationKey];
            void useWorkbench
              .getState()
              .confirmPendingSend()
              .then((ok) => {
                const current = useConversationUi.getState().entries[conversationKey];
                if (
                  ok &&
                  current &&
                  current.text === content &&
                  current.revision === submitted?.revision
                )
                  useConversationUi
                    .getState()
                    .update(conversationKey, { text: "", revision: current.revision + 1 });
              });
          }}
        >
          用原提交确认上次发送
        </button>
      )}
      <RunStatus
        activeRun={activeRun}
        busyMessage={activeRunError}
        progress={activeRunProgress}
        degraded={degraded}
        stopping={stopping}
        onStop={handleCancel}
        onRetry={handleRetry}
      />
      {error ? (
        <button
          id="hp-conversation-error"
          type="button"
          className="hp-error"
          onClick={handleDismissError}
        >
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
      <Flex justify="end" px="3" py="1">
        {" "}
        <Button
          size="1"
          variant={traceOpen ? "solid" : "soft"}
          disabled={!latestRunId}
          onClick={() => {
            if (traceOpen) setTraceOpen(false);
            else if (latestRunId) {
              useTraceStore.getState().selectRun(latestRunId);
              setTraceOpen(true);
            }
          }}
        >
          <Activity size={14} aria-hidden="true" /> Trace
        </Button>
      </Flex>
      <Box style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
        <HpThread
          conversationKey={conversationKey}
          composerContext={<ConversationResources refresh={resourceRefresh} ensure={ensure} />}
          toolbar={
            <>
              {fileUploadEnabled ? (
                <details
                  className="hp-file-candidates"
                  onToggle={(event) => {
                    if (event.currentTarget.open)
                      void ensure().then((id) => {
                        if (id) void loadFileCandidates();
                      });
                  }}
                >
                  <summary>选择已有文件</summary>
                  {loadingCandidates && <p role="status">正在加载文件…</p>}
                  {candidatesError && (
                    <p role="alert">
                      {candidatesError}
                      <button onClick={() => void loadFileCandidates()}>重试</button>
                    </p>
                  )}
                  <div style={{ maxHeight: 160, overflowY: "auto" }}>
                    {fileCandidates.map((file) => (
                      <button
                        key={file.file_id}
                        type="button"
                        disabled={
                          strategyLocked ||
                          attachments.length >= 10 ||
                          attachments.some((a) => a.fileId === file.file_id)
                        }
                        onClick={() => selectExistingFile(file)}
                      >
                        {file.file_name} · {file.purpose === "output" ? "已发布输出" : "历史附件"}
                      </button>
                    ))}
                    {fileCandidatesNext ? (
                      <button
                        type="button"
                        disabled={loadingCandidates}
                        onClick={() => void loadFileCandidates(true)}
                      >
                        更多文件
                      </button>
                    ) : null}
                  </div>
                </details>
              ) : null}{" "}
              <select
                aria-label="执行模式"
                value={displayedStrategy}
                disabled={strategyLocked}
                onChange={(event) =>
                  (() => {
                    const strategy = event.target.value as "react" | "plan_and_execute";
                    setAgentStrategy(strategy);
                    useConversationUi.getState().update(conversationKey, { strategy });
                  })()
                }
              >
                <option value="react">快速</option>
                {durableAgentEnabled ||
                (strategyLocked && displayedStrategy === "plan_and_execute") ? (
                  <option value="plan_and_execute">深度</option>
                ) : null}
              </select>
            </>
          }
          error={error}
          loadingHistory={loadingMessages}
          stopping={stopping}
          hasMoreMessages={hasMoreMessages}
          loadingMoreMessages={loadingMoreMessages}
          onLoadMoreMessages={handleLoadMore}
          messages={messages}
          activeRun={activeRun}
          attachments={attachments}
          fileUploadEnabled={fileUploadEnabled}
          sendDisabled={
            loadingMessages ||
            sending ||
            creating ||
            attachments.some((attachment) => attachment.status !== "ready")
          }
          onFilesSelected={handleFilesSelected}
          onRemoveAttachment={handleRemoveAttachment}
          onSaveFile={onSaveFile}
          onSend={handleSend}
          onCancel={handleCancel}
        />
      </Box>
    </Flex>
  );
}
