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
import { useId, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { readingFoldBlock, useReadingFold } from "../../components/mercury/useReadingFold";
import { useMercuryMotion } from "../../components/mercury/useMercuryMotion";
import { useConversationTransit } from "../../components/mercury/useConversationTransit";
import { ReadingPresentation } from "../../components/mercury/readingPresentation";
import { MercuryLayer } from "../../components/mercury/MercuryLayer";
import { emptyConversationUi, useConversationUi } from "../../store/conversationUi";
import { Button, Flex, Spinner, Text } from "@radix-ui/themes";
import { Bot, FileText, Paperclip, Send, Sparkles, Square, UserRound, X } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { HpFile, HpMessage, HpRun } from "../../api/types";
import { ExecutionBlock } from "../../components/run/ExecutionBlock";
import { useHpThreadRuntime } from "./runtime";
import { ArtifactMessageItems } from "../../components/artifact/ArtifactMessageItems";
import { deriveComposerActionPhase } from "../../components/input-action/policy";
import { useMotionAllowed } from "../../components/motion/preference";
import type { UploadAttachment } from "../../store/workbench";

export interface HpThreadProps {
  conversationKey?: string;
  readingControlsHost?: HTMLElement | null;
  readingScope?: string;
  readingOwner?: string;
  readingReady?: boolean;
  readingUnavailable?: boolean;
  readingBlockedReason?: string | null;
  composerContext?: ReactNode;
  toolbar?: ReactNode;
  composerMode?: ReactNode;
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
  /** Existing pending idempotency intent; no confirmed acceptance yet. */
  sendUncertain?: boolean;
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
  const hasTime = message.createdAt && Number.isFinite(message.createdAt.getTime());
  return (
    <MessagePrimitive.Root className="hp-msg" data-message-id={message.id} data-role={message.role}>
      <MessagePrimitive.If user>
        <span className="hp-msg__marker hp-msg__marker--user" aria-hidden="true">
          <UserRound size={22} />
        </span>
      </MessagePrimitive.If>
      <MessagePrimitive.If assistant>
        <span className="hp-msg__marker hp-msg__marker--assistant" aria-hidden="true">
          <Sparkles size={16} className="hp-assistant-glyph" aria-hidden="true" />
        </span>
      </MessagePrimitive.If>
      <div className="hp-msg__body">
        <div className="hp-msg__meta">
          <span>{message.role === "assistant" ? "HpAgent" : "你"}</span>
          {hasTime && (
            <time dateTime={message.createdAt.toISOString()}>
              {message.createdAt.toLocaleTimeString("zh-CN", {
                hour: "2-digit",
                minute: "2-digit",
              })}
            </time>
          )}
        </div>
        <div
          className={
            message.role === "assistant"
              ? `hp-answer-surface${runsByMessageId[message.id] ? " hp-answer-surface--run" : ""}`
              : "hp-message-content"
          }
        >
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
        </div>
      </div>
    </MessagePrimitive.Root>
  );
}

function fitComposerInput(input: HTMLTextAreaElement | null) {
  if (!input || !input.getClientRects().length) return;
  input.style.height = "auto";
  const limit = Number.parseFloat(getComputedStyle(input).maxHeight);
  input.style.height = `${Math.min(input.scrollHeight, limit || input.scrollHeight)}px`;
}

export function HpThread({
  conversationKey = "standalone",
  readingControlsHost,
  readingScope = conversationKey,
  readingOwner = conversationKey.split(":")[0]!,
  readingReady = true,
  readingUnavailable = false,
  readingBlockedReason = null,
  composerContext,
  toolbar,
  composerMode,
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
  sendUncertain = false,
  onFilesSelected,
  onRemoveAttachment,
  onSaveFile,
  onSend,
  onCancel,
}: HpThreadProps) {
  const draft = useConversationUi((s) => s.entries[conversationKey] ?? emptyConversationUi);
  const motionAllowed = useMotionAllowed();
  const [interaction, setInteraction] = useState({
    key: conversationKey,
    focused: false,
    pointerIntent: false,
    sendFocus: false,
    composing: false,
  });
  const currentInteraction =
    interaction.key === conversationKey
      ? interaction
      : { focused: false, pointerIntent: false, sendFocus: false, composing: false };
  const interact = (changes: Partial<typeof currentInteraction>) =>
    setInteraction((previous) => ({
      ...(previous.key === conversationKey
        ? previous
        : {
            key: conversationKey,
            focused: false,
            pointerIntent: false,
            sendFocus: false,
            composing: false,
          }),
      ...changes,
    }));
  useLayoutEffect(() => {
    const cancelIntent = () => {
      if (document.hidden)
        setInteraction((previous) => ({ ...previous, pointerIntent: false, sendFocus: false }));
    };
    // Listen to visibility itself, even when motion was already disabled by reduction.
    document.addEventListener("visibilitychange", cancelIntent);
    return () => document.removeEventListener("visibilitychange", cancelIntent);
  }, []);
  const composing = useRef(false);
  useLayoutEffect(() => {
    composing.current = false;
  }, [conversationKey]);
  const submissions = useRef(new Map<string, symbol>());
  const [submittingKeys, setSubmittingKeys] = useState<Set<string>>(() => new Set());
  const submitting = submittingKeys.has(conversationKey);
  const readingId = useId();
  const foldBlock =
    readingBlockedReason ??
    readingFoldBlock(
      activeRun,
      loadingHistory || Boolean(loadingMoreMessages) || sendUncertain || stopping || submitting,
    );
  const {
    viewport,
    region: readingRegion,
    control: readingControl,
    folded,
    reveal: revealReading,
    toggle: toggleReading,
  } = useReadingFold(readingScope, messages, foldBlock);
  const maskId = `hp-mercury-${readingId.replace(/:/g, "")}`;
  const {
    canvas: mercuryCanvas,
    maskPath: mercuryMask,
    active: mercuryActive,
    prepare: prepareMercury,
  } = useMercuryMotion({
    scope: readingScope,
    conversationKey,
    messages,
    blocked: foldBlock,
    folded,
    allowed: motionAllowed,
    region: readingRegion,
    viewport,
    maskId,
  });
  const readingFrame = useMemo(
    () => ({ key: conversationKey, messages, run: activeRun, hasMore: hasMoreMessages }),
    [conversationKey, messages, activeRun, hasMoreMessages],
  );
  const transit = useConversationTransit({
    frame: readingFrame,
    owner: readingOwner,
    ready: readingReady,
    unavailable: readingUnavailable || submitting || sendUncertain || stopping,
    folded,
    allowed: motionAllowed,
    reveal: revealReading,
    region: readingRegion,
    viewport,
    canvas: mercuryCanvas,
    maskPath: mercuryMask,
    maskId,
  });
  const readingMessages = transit.frame.messages,
    readingRun = transit.frame.run,
    readingHasMore = transit.frame.hasMore;
  const composerInput = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => fitComposerInput(composerInput.current), [draft.text, conversationKey]);
  useLayoutEffect(() => {
    const input = composerInput.current;
    if (!input || typeof ResizeObserver === "undefined") return;
    let width = input.getBoundingClientRect().width;
    const observer = new ResizeObserver(() => {
      const nextWidth = input.getBoundingClientRect().width;
      if (nextWidth === width) return;
      width = nextWidth;
      fitComposerInput(input);
    });
    observer.observe(input);
    return () => observer.disconnect();
  }, []);
  const previous = useRef<{ key: string; first?: string; count: number }>({ key: "", count: 0 });
  const restorePages = useRef(0);
  const running = Boolean(
    activeRun && !["succeeded", "failed", "cancelled"].includes(activeRun.status),
  );
  const actionPhase = deriveComposerActionPhase({
    draft: draft.text,
    sendDisabled,
    attachmentsReady: attachments.every((a) => a.status === "ready" && Boolean(a.fileId)),
    submitting,
    uncertain: sendUncertain,
    status: activeRun?.status ?? null,
    stopping,
    composing: currentInteraction.composing,
    focused: currentInteraction.focused,
    intent: currentInteraction.pointerIntent || currentInteraction.sendFocus,
  });
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
    revealReading();
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
      if (el.closest("[hidden], [inert]")) return;
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
    viewport,
    transit.busy,
  ]);
  const runtime = useHpThreadRuntime({
    messages: readingMessages,
    activeRun: readingRun,
    sendDisabled,
    onSend,
    onCancel,
  });
  const filesByMessageId = Object.fromEntries(
    readingMessages.map((message) => [message.message_id, message.files ?? []]),
  );
  const foldControl = (
    <button
      ref={readingControl}
      type="button"
      className="hp-reading-toggle"
      aria-controls={readingId}
      aria-expanded={!folded}
      aria-description={foldBlock ?? undefined}
      disabled={transit.busy || (!folded && Boolean(foldBlock))}
      aria-busy={transit.busy || undefined}
      title={
        transit.busy
          ? "正在切换阅读区"
          : (foldBlock ?? (folded ? "展开当前阅读区" : "仅收纳阅读区，保留输入与会话"))
      }
      onClick={() => {
        prepareMercury(folded ? 0 : 1);
        toggleReading(mercuryActive);
      }}
    >
      {folded ? "展开阅读区" : "收纳阅读区"}
    </button>
  );
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ThreadPrimitive.Root className="hp-thread" data-reading-folded={folded}>
        {readingControlsHost ? createPortal(foldControl, readingControlsHost) : foldControl}
        <MercuryLayer
          canvas={mercuryCanvas}
          maskPath={mercuryMask}
          maskId={maskId}
          active={mercuryActive || transit.active}
        />
        <ReadingPresentation.Provider value={{ retiring: transit.busy, run: readingRun }}>
          <div
            ref={readingRegion}
            id={readingId}
            className="hp-reading"
            data-reading-conversation={transit.frame.key}
            data-mercury-retiring={transit.retiring || undefined}
            hidden={folded && !mercuryActive && !transit.busy}
            inert={folded || mercuryActive || transit.busy}
            aria-hidden={folded || mercuryActive || transit.busy || undefined}
          >
            {readingHasMore && (
              <Flex justify="center" className="hp-loadmore">
                <Button
                  size="1"
                  variant="soft"
                  type="button"
                  onClick={onLoadMoreMessages}
                  disabled={loadingMoreMessages}
                >
                  {loadingMoreMessages ? "加载中…" : "加载更早的消息"}
                </Button>
              </Flex>
            )}
            <ThreadPrimitive.Viewport
              ref={viewport}
              className="hp-thread__viewport"
              autoScroll={false}
              scrollToBottomOnRunStart={false}
              scrollToBottomOnInitialize={false}
              scrollToBottomOnThreadSwitch={false}
              onScroll={(event) => {
                if (loadingHistory || folded || event.currentTarget.closest("[hidden], [inert]"))
                  return;
                const el = event.currentTarget;
                const rect = el.getBoundingClientRect();
                const anchor = Array.from(
                  el.querySelectorAll<HTMLElement>("[data-message-id]"),
                ).find((m) => m.getBoundingClientRect().bottom > rect.top);
                useConversationUi.getState().update(conversationKey, {
                  atBottom: el.scrollHeight - el.scrollTop - el.clientHeight < 48,
                  anchorMessageId: anchor?.dataset.messageId,
                  offset: anchor ? anchor.getBoundingClientRect().top - rect.top : 0,
                });
              }}
            >
              <ThreadPrimitive.Empty>
                <div className="hp-thread-empty">
                  <span className="hp-thread-empty-icon" aria-hidden="true">
                    <Bot size={30} />
                  </span>
                  <Text as="p" size="5" weight="bold">
                    开始新的对话吧
                  </Text>
                  <p>
                    {fileUploadEnabled
                      ? "输入问题，或添加资料与 HpAgent 一起开始。"
                      : "输入问题，与 HpAgent 一起开始。"}
                  </p>
                </div>
              </ThreadPrimitive.Empty>
              <ThreadPrimitive.Messages>
                {() => (
                  <HpMessageView
                    filesByMessageId={filesByMessageId}
                    htmlMessageIds={
                      new Set(
                        readingMessages
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
                      readingMessages
                        .filter((m) => m.role === "assistant" && m.produced_by_run_id)
                        .map((m) => [m.message_id, m.produced_by_run_id!]),
                    )}
                    onSaveFile={onSaveFile}
                  />
                )}
              </ThreadPrimitive.Messages>
              {readingRun &&
                !readingMessages.some(
                  (m) => m.role === "assistant" && m.produced_by_run_id === readingRun.run_id,
                ) && <ExecutionBlock runId={readingRun.run_id} />}
            </ThreadPrimitive.Viewport>
            {!draft.atBottom &&
              draft.anchorMessageId &&
              !readingMessages.some((m) => m.message_id === draft.anchorMessageId) &&
              readingHasMore && (
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
          </div>
        </ReadingPresentation.Provider>
        {folded && !mercuryActive && (
          <div className="hp-reading-folded" role="status">
            <p>阅读区已收纳</p>
            <p>输入与附件仍保留。发送消息或收到新内容时自动展开。</p>
          </div>
        )}
        <form
          className="hp-composer"
          data-action-phase={actionPhase}
          data-action-motion={motionAllowed}
          onFocusCapture={() => interact({ focused: true })}
          onBlurCapture={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget as Node | null))
              interact({ focused: false, pointerIntent: false, sendFocus: false });
          }}
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
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
            ref={composerInput}
            rows={1}
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
              interact({ composing: true, pointerIntent: false, sendFocus: false });
            }}
            onCompositionEnd={() => {
              composing.current = false;
              interact({ composing: false });
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
          <div className="hp-composer__actions">
            <div className="hp-composer__file-actions">
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
            </div>
            {composerContext}
            <div className="hp-composer__submit">
              {composerMode}
              <button
                type={running ? "button" : "submit"}
                className={`hp-composer__action ${running ? "hp-composer__cancel" : "hp-composer__send"}`}
                data-state={running ? "stop" : "send"}
                onPointerEnter={(event) => {
                  if (event.pointerType === "mouse") interact({ pointerIntent: true });
                }}
                onPointerLeave={() => interact({ pointerIntent: false })}
                onPointerCancel={() => interact({ pointerIntent: false })}
                onFocus={(event) =>
                  interact({ sendFocus: event.currentTarget.matches(":focus-visible") })
                }
                onBlur={() => interact({ sendFocus: false })}
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
                {running ? (
                  stopping || activeRun?.status === "cancelling" ? (
                    <Spinner size="1" />
                  ) : (
                    <Square size={16} aria-hidden="true" />
                  )
                ) : (
                  <Send size={18} aria-hidden="true" />
                )}
              </button>
            </div>
          </div>
        </form>
      </ThreadPrimitive.Root>
    </AssistantRuntimeProvider>
  );
}
