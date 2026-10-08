/**
 * assistant-ui chat surface (contract §14.1).
 *
 * The single place that composes assistant-ui's primitives into the chat UI.
 * Everything else in the app treats this as a black box that renders the Hp
 * message history and reports user intent through `onSend` / `onCancel`.
 *
 * regenerate/branch are not surfaced (no onReload/onEdit wiring): a failed or
 * cancelled Run is retried from the run-status area (phase-e E-04).
 */
import {
  AssistantRuntimeProvider,
  MessagePrimitive,
  ThreadPrimitive,
  useAuiState,
} from "@assistant-ui/react";
import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { emptyConversationUi, useConversationUi } from "../../store/conversationUi";
import { Flex, Spinner, Text } from "@radix-ui/themes";
import { FileText, Paperclip, X } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { HpFile, HpMessage, HpRun } from "../../api/types";
import { ExecutionBlock } from "../../components/run/ExecutionBlock";
import { useHpThreadRuntime } from "./runtime";
import { ArtifactMessageItems } from "../../components/artifact/ArtifactMessageItems";
import type { UploadAttachment } from "../../store/workbench";

export interface HpThreadProps {
  conversationKey?: string;
  composerContext?: ReactNode;
  toolbar?: ReactNode;
  error?: string | null;
  loadingHistory?: boolean;
  stopping?: boolean;
  hasMoreMessages?: boolean;
  loadingMoreMessages?: boolean;
  onLoadMoreMessages?: () => void;
  messages: HpMessage[];
  activeRun: HpRun | null;
  attachments: UploadAttachment[];
  fileUploadEnabled: boolean;
  sendDisabled: boolean;
  onFilesSelected: (files: File[]) => void;
  onRemoveAttachment: (localId: string) => void;
  onSaveFile?: (file: HpFile) => void;
  onSend: (content: string) => boolean | Promise<boolean>;
  onCancel: () => void;
}

/**
 * Text part renderer: assistant replies carry Markdown (fenced code, lists,
 * emphasis) and must render as such. react-markdown emits no raw HTML by
 * default, so the text stays inert.
 */
function HpTextPart({ text }: { text: string }) {
  return (
    <div className="hp-text-part">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  );
}

function HpMessageView({
  filesByMessageId,
  runsByMessageId,
  htmlMessageIds,
  onSaveFile,
}: {
  filesByMessageId: Record<string, HpFile[]>;
  runsByMessageId: Record<string, string>;
  htmlMessageIds: Set<string>;
  onSaveFile?: (file: HpFile) => void;
}) {
  const message = useAuiState((s) => s.message);
  const files = filesByMessageId[message.id] ?? [];
  const canBuild = htmlMessageIds.has(message.id);
  return (
    <MessagePrimitive.Root className="hp-msg" data-message-id={message.id}>
      <MessagePrimitive.If user>
        <span className="hp-msg__marker hp-msg__marker--user" aria-hidden="true" />
      </MessagePrimitive.If>
      <MessagePrimitive.If assistant>
        <span className="hp-msg__marker hp-msg__marker--assistant" aria-hidden="true" />
      </MessagePrimitive.If>
      <MessagePrimitive.Parts components={{ Text: HpTextPart }} />
      {message.role === "assistant" && runsByMessageId[message.id] && (
        <ExecutionBlock runId={runsByMessageId[message.id]!} messageId={message.id} />
      )}
      {files.length ? (
        <div className="hp-msg__files" aria-label="消息附件">
          {files.map((file) => (
            <span key={file.file_id}>
              <a
                className="hp-msg__file"
                href={file.download_url ?? undefined}
                aria-disabled={!file.download_url}
              >
                <FileText size={14} aria-hidden="true" />
                <span>{file.file_name}</span>
              </a>
              {file.status === "ready" && onSaveFile ? (
                <button type="button" onClick={() => onSaveFile(file)}>
                  保存到 Workspace
                </button>
              ) : null}
            </span>
          ))}
        </div>
      ) : null}
      {canBuild && <ArtifactMessageItems messageId={message.id} />}
    </MessagePrimitive.Root>
  );
}

export function HpThread({
  conversationKey = "standalone",
  composerContext,
  toolbar,
  loadingHistory = false,
  error,
  stopping = false,
  hasMoreMessages,
  loadingMoreMessages,
  onLoadMoreMessages,
  messages,
  activeRun,
  attachments,
  fileUploadEnabled,
  sendDisabled,
  onFilesSelected,
  onRemoveAttachment,
  onSaveFile,
  onSend,
  onCancel,
}: HpThreadProps) {
  const draft = useConversationUi((s) => s.entries[conversationKey] ?? emptyConversationUi);
  const composing = useRef(false);
  const submissions = useRef(new Map<string, symbol>());
  const [submittingKeys, setSubmittingKeys] = useState<Set<string>>(() => new Set());
  const viewport = useRef<HTMLDivElement>(null);
  const previous = useRef<{ key: string; first?: string; count: number }>({ key: "", count: 0 });
  const restorePages = useRef(0);
  const running = Boolean(
    activeRun && !["succeeded", "failed", "cancelled"].includes(activeRun.status),
  );
  const submitting = submittingKeys.has(conversationKey);
  const submit = async () => {
    if (
      composing.current ||
      submissions.current.has(conversationKey) ||
      submitting ||
      running ||
      sendDisabled ||
      !draft.text.trim()
    )
      return;
    const key = conversationKey,
      text = draft.text,
      revision = draft.revision;
    const token = Symbol("submit");
    submissions.current.set(key, token);
    setSubmittingKeys((current) => new Set(current).add(key));
    try {
      const ok = await Promise.resolve(onSend(text)).catch(() => false);
      const current = useConversationUi.getState().entries[key];
      if (ok && current?.revision === revision && current.text === text)
        useConversationUi.getState().update(key, { text: "", revision: revision + 1 });
    } finally {
      if (submissions.current.get(key) === token) {
        submissions.current.delete(key);
        setSubmittingKeys((current) => {
          const next = new Set(current);
          next.delete(key);
          return next;
        });
      }
    }
  };
  useLayoutEffect(() => {
    const el = viewport.current;
    if (!el || loadingHistory) return;
    // The external runtime publishes its new message DOM after this parent's
    // layout effect. Restore only once that DOM reflects the requested page.
    const align = () => {
      const rendered = Array.from(el.querySelectorAll<HTMLElement>("[data-message-id]"));
      if (
        rendered.length !== messages.length ||
        rendered[0]?.dataset.messageId !== messages[0]?.message_id
      )
        return;
      const saved = useConversationUi.getState().entries[conversationKey] ?? emptyConversationUi;
      const switched = previous.current.key !== conversationKey;
      if (switched) restorePages.current = 0;
      const prepended = !switched && previous.current.first !== messages[0]?.message_id;
      if (switched || prepended) {
        const anchor = rendered.find((m) => m.dataset.messageId === saved.anchorMessageId);
        if (!saved.atBottom && anchor)
          el.scrollTop +=
            anchor.getBoundingClientRect().top - el.getBoundingClientRect().top - saved.offset;
        else if (
          !saved.atBottom &&
          saved.anchorMessageId &&
          hasMoreMessages &&
          !loadingMoreMessages &&
          restorePages.current < 5
        ) {
          restorePages.current++;
          onLoadMoreMessages?.();
        } else if (saved.atBottom) el.scrollTop = el.scrollHeight;
      } else if (saved.atBottom) el.scrollTop = el.scrollHeight;
      previous.current = {
        key: conversationKey,
        first: messages[0]?.message_id,
        count: messages.length,
      };
    };
    const observer = new MutationObserver(align);
    observer.observe(el, { childList: true, subtree: true, characterData: true });
    const frame = requestAnimationFrame(align);
    return () => {
      observer.disconnect();
      cancelAnimationFrame(frame);
    };
  }, [
    conversationKey,
    loadingHistory,
    messages,
    hasMoreMessages,
    loadingMoreMessages,
    onLoadMoreMessages,
  ]);
  const runtime = useHpThreadRuntime({ messages, activeRun, sendDisabled, onSend, onCancel });
  const filesByMessageId = Object.fromEntries(
    messages.map((message) => [message.message_id, message.files ?? []]),
  );
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ThreadPrimitive.Root className="hp-thread">
        <ThreadPrimitive.Viewport
          ref={viewport}
          className="hp-thread__viewport"
          autoScroll={false}
          scrollToBottomOnRunStart={false}
          scrollToBottomOnInitialize={false}
          scrollToBottomOnThreadSwitch={false}
          onScroll={(event) => {
            if (loadingHistory) return;
            const el = event.currentTarget;
            const rect = el.getBoundingClientRect();
            const anchor = Array.from(el.querySelectorAll<HTMLElement>("[data-message-id]")).find(
              (m) => m.getBoundingClientRect().bottom > rect.top,
            );
            useConversationUi.getState().update(conversationKey, {
              atBottom: el.scrollHeight - el.scrollTop - el.clientHeight < 48,
              anchorMessageId: anchor?.dataset.messageId,
              offset: anchor ? anchor.getBoundingClientRect().top - rect.top : 0,
            });
          }}
        >
          <ThreadPrimitive.Empty>
            <Flex align="center" justify="center" style={{ height: "100%", padding: 24 }}>
              <Text size="3" color="gray">
                开始新的对话吧
              </Text>
            </Flex>
          </ThreadPrimitive.Empty>
          <ThreadPrimitive.Messages>
            {() => (
              <HpMessageView
                filesByMessageId={filesByMessageId}
                htmlMessageIds={
                  new Set(
                    messages
                      .filter(
                        (m) =>
                          m.role === "assistant" &&
                          m.status === "completed" &&
                          Boolean(m.content?.trim()),
                      )
                      .map((m) => m.message_id),
                  )
                }
                runsByMessageId={Object.fromEntries(
                  messages
                    .filter((m) => m.role === "assistant" && m.produced_by_run_id)
                    .map((m) => [m.message_id, m.produced_by_run_id!]),
                )}
                onSaveFile={onSaveFile}
              />
            )}
          </ThreadPrimitive.Messages>
          {activeRun &&
            !messages.some(
              (m) => m.role === "assistant" && m.produced_by_run_id === activeRun.run_id,
            ) && <ExecutionBlock runId={activeRun.run_id} />}
        </ThreadPrimitive.Viewport>
        {!draft.atBottom &&
          draft.anchorMessageId &&
          !messages.some((m) => m.message_id === draft.anchorMessageId) &&
          hasMoreMessages && (
            <button type="button" disabled={loadingMoreMessages} onClick={onLoadMoreMessages}>
              原阅读位置尚未加载，继续加载历史
            </button>
          )}
        {!draft.atBottom && (
          <button
            className="hp-thread-bottom"
            type="button"
            onClick={() => {
              if (viewport.current) viewport.current.scrollTop = viewport.current.scrollHeight;
              useConversationUi.getState().update(conversationKey, { atBottom: true });
            }}
          >
            有新消息 / 回到底部
          </button>
        )}
        <form
          className="hp-composer"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          {composerContext}
          {attachments.length ? (
            <div className="hp-composer__attachments" aria-label="待发送附件">
              {attachments.map((attachment) => (
                <div
                  className={`hp-attachment hp-attachment--${attachment.status}`}
                  key={attachment.localId}
                  title={attachment.error ?? attachment.name}
                >
                  <FileText size={14} aria-hidden="true" />
                  <span className="hp-attachment__name">{attachment.name}</span>
                  {attachment.status === "uploading" ? <Spinner size="1" /> : null}
                  {attachment.status === "ready" ? (
                    <span className="hp-attachment__status">已就绪</span>
                  ) : null}
                  {attachment.status === "failed" ? (
                    <span className="hp-attachment__status">上传失败</span>
                  ) : null}
                  {attachment.error && (
                    <span role="alert" className="hp-attachment__error">
                      {attachment.error}
                    </span>
                  )}
                  <button
                    type="button"
                    className="hp-attachment__remove"
                    aria-label={`移除 ${attachment.name}`}
                    title={`移除 ${attachment.name}`}
                    onClick={() => onRemoveAttachment(attachment.localId)}
                  >
                    <X size={13} aria-hidden="true" />
                  </button>
                </div>
              ))}
            </div>
          ) : null}
          <textarea
            className="hp-composer__input"
            aria-label="消息输入"
            aria-describedby={
              error
                ? "hp-conversation-error"
                : attachments.some((a) => a.status !== "ready")
                  ? "hp-composer-feedback"
                  : undefined
            }
            placeholder="输入消息，Enter 发送"
            value={draft.text}
            onChange={(event) =>
              useConversationUi
                .getState()
                .update(conversationKey, { text: event.target.value, revision: draft.revision + 1 })
            }
            onCompositionStart={() => {
              composing.current = true;
            }}
            onCompositionEnd={() => {
              composing.current = false;
            }}
            onKeyDown={(event) => {
              if (
                event.key === "Enter" &&
                !event.shiftKey &&
                !event.nativeEvent.isComposing &&
                event.nativeEvent.keyCode !== 229 &&
                !composing.current
              ) {
                event.preventDefault();
                void submit();
              }
            }}
          />
          {attachments.some((a) => a.status !== "ready") && (
            <p id="hp-composer-feedback" className="hp-composer-feedback">
              请等待附件就绪，或移除上传失败的附件。
            </p>
          )}
          <Flex gap="2" align="center" className="hp-composer__actions">
            {fileUploadEnabled ? (
              <label className="hp-composer__attach" aria-label="添加附件">
                <Paperclip size={16} aria-hidden="true" />
                <span>附件</span>
                <input
                  type="file"
                  multiple
                  accept=".txt,.log,.md,.pdf,.docx,.xlsx,.pptx"
                  disabled={sendDisabled || running}
                  onChange={(event) => {
                    onFilesSelected(Array.from(event.currentTarget.files ?? []));
                    event.currentTarget.value = "";
                  }}
                />
              </label>
            ) : null}
            {toolbar}
            <button
              type={running ? "button" : "submit"}
              className={`hp-composer__action ${running ? "hp-composer__cancel" : "hp-composer__send"}`}
              data-state={running ? "stop" : "send"}
              aria-label={
                running
                  ? stopping || activeRun?.status === "cancelling"
                    ? "正在停止…"
                    : "停止"
                  : "发送"
              }
              title={running ? "停止当前执行" : "发送消息"}
              disabled={
                running
                  ? stopping || activeRun?.status === "cancelling"
                  : sendDisabled || submitting || !draft.text.trim()
              }
              onClick={running ? onCancel : undefined}
            >
              <span className="hp-composer__action-size" aria-hidden="true">
                正在停止…
              </span>
              <span>
                {running
                  ? stopping || activeRun?.status === "cancelling"
                    ? "正在停止…"
                    : "停止"
                  : "发送"}
              </span>
            </button>
          </Flex>
        </form>
      </ThreadPrimitive.Root>
    </AssistantRuntimeProvider>
  );
}
