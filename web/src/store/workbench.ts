/**
 * Workbench state (phase-e E-03/E-04/E-06).
 *
 * Single source of truth for the conversation list, the active conversation's
 * message history, and the active Run. Backend IDs are the stable keys: after a
 * refresh the whole view is rebuilt from the API, never from an assistant-ui
 * local cache.
 *
 * - `send` generates one Idempotency-Key per user intent, shows a temp user
 *   message, then replaces it with the authoritative user/assistant message ids
 *   and Run from the POST (contract API-013).
 * - While a Run is active the conversation is busy: the composer is gated and
 *   double-sends are ignored, but the backend 409 remains the final arbiter.
 * - `stop`/`retry` call the cancel/retry APIs; only a safely retryable failed
 *   Run can be retried, reusing the original user message.
 * - Run state is watched live over the SSE subscription (E-06). Deltas stream
 *   into the pending assistant Message as a volatile buffer; `run.progress`
 *   lives only in the run-status area. On degraded/connect loss the feed stops
 *   permanently and the store recovers by querying the Run, polling with
 *   backoff (1s/2s/3s/5s) while it stays active. A terminal SSE snapshot always
 *   replaces the local delta buffer.
 */
import { create } from "zustand";
import { api as defaultApi } from "../api/client";
import { HpApi } from "../api/resources";
import { openRunFeed, type RunFeed, type RunProgress } from "../sse/runFeed";
import { useConversationUi } from "./conversationUi";
import { useAuth } from "./auth";
import { newIdempotencyKey } from "../utils/idempotency";
import { useTraceStore } from "../components/trace/traceStore";
import {
  HpCommandError,
  type AgentStrategy,
  type HpConversation,
  type HpMessage,
  type HpFile,
  type HpRun,
  type HpRunStatus,
} from "../api/types";

const TERMINAL_RUN_STATUS = new Set<HpRunStatus>(["succeeded", "failed", "cancelled"]);

export function isTerminalRunStatus(status: HpRunStatus): boolean {
  return TERMINAL_RUN_STATUS.has(status);
}

export function isCancellableRunStatus(status: HpRunStatus): boolean {
  return status === "queued" || status === "running";
}

export function isRetryableRun(run: HpRun): boolean {
  return run.status === "failed" && run.failure?.retryable !== false;
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

const POLL_DELAYS = [1000, 2000, 3000, 5000] as const;

export interface WorkbenchDeps {
  api: HpApi;
  /** fetch override for the SSE subscription (tests swap this for a mock). */
  fetchImpl?: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
  /** Session invalidated mid-stream: defaults to flipping the app to login. */
  onAuthExpired?: () => void;
}

export type UploadAttachmentStatus = "uploading" | "ready" | "failed";

export interface UploadAttachment {
  localId: string;
  name: string;
  size: number;
  status: UploadAttachmentStatus;
  fileId: string | null;
  file: HpFile | null;
  error: string | null;
  existing?: boolean;
}

export interface WorkbenchState {
  // Conversation list
  conversations: HpConversation[];
  conversationsLoaded: boolean;
  loadingConversations: boolean;
  conversationCursor: string | null;
  hasMoreConversations: boolean;
  loadingMoreConversations: boolean;
  activeConversation: HpConversation | null;
  loadingFileCandidates: boolean;
  fileCandidatesError: string | null;
  creatingConversation: boolean;
  conversationsError: string | null;

  // Active conversation
  activeConversationId: string | null;
  messages: HpMessage[];
  messageCursor: string | null;
  hasMoreMessages: boolean;
  loadingMessages: boolean;
  loadingMoreMessages: boolean;
  attachments: UploadAttachment[];
  fileCandidates: HpFile[];
  fileCandidatesNext: string | null;

  // Active Run
  activeRun: HpRun | null;
  activeRunError: string | null;
  /** Volatile `run.progress` hint; shown only in the run-status area (E-06). */
  activeRunProgress: RunProgress | null;
  /** True when the SSE stream degraded/connection was lost (recovering by poll). */
  degraded: boolean;
  polling: boolean;
  sending: boolean;
  stopping: boolean;
  agentStrategy: AgentStrategy;

  // Transient UI error (dismissible)
  error: string | null;

  // Bumped whenever a new Run becomes authoritative so stale pollers stop.
  pollGeneration: number;

  loadConversations: () => Promise<void>;
  loadMoreConversations: () => Promise<void>;
  ensureConversation: () => Promise<string | null>;
  leaveConversation: () => void;
  renameActiveConversation: (title: string) => Promise<boolean>;
  createConversation: () => Promise<void>;
  selectConversation: (id: string, refresh?: boolean) => Promise<void>;
  loadMoreMessages: () => Promise<void>;
  addAttachments: (files: File[]) => Promise<void>;
  selectExistingFile: (file: HpFile) => void;
  loadFileCandidates: (more?: boolean) => Promise<void>;
  removeAttachment: (localId: string) => Promise<void>;
  pendingSendIds: string[];
  pendingSendContent: Record<string, string>;
  confirmPendingSend: () => Promise<boolean>;
  sendMessage: (content: string, confirm?: boolean) => Promise<boolean>;
  setAgentStrategy: (strategy: AgentStrategy) => void;
  stopRun: () => Promise<void>;
  retryRun: () => Promise<void>;
  refreshActiveRun: () => Promise<void>;
  clearError: () => void;
  reset: () => void;
}

/** Replace the temp user message with authoritative ids; append the assistant msg. */
function replaceTempMessage(
  messages: HpMessage[],
  tempId: string,
  userMessage: HpMessage,
  assistantMessage: HpMessage,
): HpMessage[] {
  const next = messages.filter((m) => m.message_id !== tempId);
  return [
    ...next.filter(
      (m) =>
        m.message_id !== userMessage.message_id && m.message_id !== assistantMessage.message_id,
    ),
    userMessage,
    assistantMessage,
  ];
}

/** Update the assistant message for a Run in place (by id, then by run). */
function reconcileAssistantMessage(messages: HpMessage[], assistant: HpMessage): HpMessage[] {
  const byId = messages.findIndex((m) => m.message_id === assistant.message_id);
  if (byId >= 0) {
    const next = messages.slice();
    next[byId] = assistant;
    return next;
  }
  const byRun = messages.findIndex(
    (m) => m.role === "assistant" && m.produced_by_run_id === assistant.produced_by_run_id,
  );
  if (byRun >= 0) {
    const next = messages.slice();
    next[byRun] = assistant;
    return next;
  }
  return [...messages, assistant];
}

/**
 * Append a volatile SSE delta to the pending assistant Message (E-06).
 *
 * Deltas are an in-memory display buffer only: they are never written to the
 * backend, and a terminal snapshot fully replaces them. Matching is by
 * message_id first, then by the Run that produced the pending assistant Message.
 */
function appendDelta(
  messages: HpMessage[],
  runId: string,
  messageId: string,
  delta: string,
): HpMessage[] {
  const idx = messages.findIndex(
    (m) => m.message_id === messageId || (m.role === "assistant" && m.produced_by_run_id === runId),
  );
  if (idx < 0) return messages;
  const next = messages.slice();
  const current = next[idx];
  if (!current) return messages;
  next[idx] = { ...current, content: `${current.content ?? ""}${delta}` };
  return next;
}

function messageErrorText(err: unknown): string {
  if (err instanceof HpCommandError) {
    return err.error.message;
  }
  if (err instanceof Error) {
    return err.message;
  }
  return "发生未知错误，请重试。";
}

export function createWorkbenchStore(
  deps: WorkbenchDeps = {
    api: new HpApi(defaultApi),
    onAuthExpired: () => useAuth.getState().expire(),
  },
) {
  const { api } = deps;

  return create<WorkbenchState>()((set, get) => {
    /** The live SSE feed for the current Run, if any; closed on switch/stop. */
    let activeFeed: RunFeed | null = null;
    let budgetRefreshTimer: ReturnType<typeof setTimeout> | null = null;
    let conversationSelectionGeneration = 0;
    let accountGeneration = 0;
    let listGeneration = 0;
    let candidateGeneration = 0;
    let createKey: string | null = null;
    let createPromise: Promise<string | null> | null = null;
    const localConversations = new Map<string, HpConversation>();
    const intents = new Map<
      string,
      { key: string; content: string; fileIds: string[]; strategy: AgentStrategy }
    >();
    const upsert = (conversation: HpConversation) => {
      const known = localConversations.get(conversation.conversation_id);
      if (known && known.metadata_version > conversation.metadata_version) return;
      localConversations.set(conversation.conversation_id, conversation);
      listGeneration += 1;
      set({
        loadingConversations: false,
        loadingMoreConversations: false,
        conversationCursor: null,
        hasMoreConversations: false,
      });
      set((state) => ({
        conversations: [
          conversation,
          ...state.conversations.filter((c) => c.conversation_id !== conversation.conversation_id),
        ],
        activeConversation:
          state.activeConversationId === conversation.conversation_id
            ? conversation
            : state.activeConversation,
      }));
    };
    const pendingSleeps = new Map<ReturnType<typeof setTimeout>, () => void>();
    const pause = (ms: number) =>
      new Promise<void>((resolve) => {
        const timer = setTimeout(() => {
          pendingSleeps.delete(timer);
          resolve();
        }, ms);
        pendingSleeps.set(timer, resolve);
      });
    const fence = () => {
      const account = accountGeneration;
      const selection = conversationSelectionGeneration;
      return () => account === accountGeneration && selection === conversationSelectionGeneration;
    };

    const closeFeed = (): void => {
      if (activeFeed) {
        activeFeed.close();
        activeFeed = null;
      }
      if (budgetRefreshTimer) {
        clearTimeout(budgetRefreshTimer);
        budgetRefreshTimer = null;
      }
    };

    async function refreshRunBudgetOnly(runId: string, generation: number): Promise<void> {
      try {
        const snapshot = await api.getRun(runId);
        if (snapshot.source_kind !== "chat") return;
        const latest = get();
        if (latest.pollGeneration !== generation || latest.activeRun?.run_id !== runId) return;
        set({ activeRun: { ...latest.activeRun, budget: snapshot.run.budget } });
      } catch {
        // Terminal confirmation or degraded polling will provide the next refresh.
      }
    }

    function scheduleBudgetRefresh(runId: string, generation: number, delay: number): void {
      if (budgetRefreshTimer) clearTimeout(budgetRefreshTimer);
      budgetRefreshTimer = setTimeout(() => {
        budgetRefreshTimer = null;
        void refreshRunBudgetOnly(runId, generation);
      }, delay);
    }
    /** Poll a Run until terminal; stops when a newer Run supersedes it. */
    function startPolling(runId: string): void {
      const generation = get().pollGeneration;
      void (async () => {
        let attempt = 0;
        for (;;) {
          const delay = POLL_DELAYS[Math.min(attempt, POLL_DELAYS.length - 1)] ?? 5000;
          await pause(delay);
          attempt += 1;
          const current = get();
          if (current.pollGeneration !== generation) return;
          if (current.activeRun?.run_id !== runId) return;
          if (current.activeRun && isTerminalRunStatus(current.activeRun.status)) {
            set({ polling: false, degraded: false });
            return;
          }
          try {
            const snapshot = await api.getRun(runId);
            if (snapshot.source_kind !== "chat") return;
            const latest = get();
            if (latest.pollGeneration !== generation) return;
            const run = snapshot.run;
            const terminal = isTerminalRunStatus(run.status);
            set({
              activeRun: run,
              messages: reconcileAssistantMessage(latest.messages, snapshot.assistant_message),
              ...(terminal ? { activeRunError: null } : {}),
              polling: !terminal,
              degraded: terminal ? false : get().degraded,
            });
            if (terminal) return;
          } catch {
            // Transient fetch error: keep polling with the current backoff.
          }
        }
      })();
    }

    /** One authoritative GET after a terminal SSE event (contract §12.4). */
    async function confirmRun(runId: string, generation: number): Promise<void> {
      try {
        const snapshot = await api.getRun(runId);
        if (snapshot.source_kind !== "chat") return;
        const latest = get();
        if (latest.pollGeneration !== generation || latest.activeRun?.run_id !== runId) return;
        set({
          activeRun: snapshot.run,
          messages: reconcileAssistantMessage(latest.messages, snapshot.assistant_message),
        });
      } catch {
        // The SSE snapshot is already authoritative; nothing to correct.
      }
    }

    /**
     * Watch a Run live over SSE (E-06). Deltas stream into the pending Message;
     * progress lands in the run-status area; a terminal snapshot replaces the
     * buffer. On degraded/connect loss the feed stops permanently and recovery
     * queries the Run, polling while it stays active (contract §13.2).
     */
    function startRunMonitor(runId: string): void {
      useTraceStore.getState().followRun(runId);
      const generation = get().pollGeneration;
      closeFeed();

      const current = get();
      if (current.activeRun && isTerminalRunStatus(current.activeRun.status)) {
        set({ polling: false, degraded: false, activeRunProgress: null });
        return;
      }
      set({ polling: true, degraded: false, activeRunProgress: null });

      const feedNodeTypes = new Map<string, string>();
      const stale = (): boolean => {
        const latest = get();
        return latest.pollGeneration !== generation || latest.activeRun?.run_id !== runId;
      };

      try {
        const feed = openRunFeed(
          runId,
          {
            onSnapshot: (snapshot) => {
              if (stale()) return;
              // Only a terminal Run dismisses a `conversation_busy` notice;
              // while a foreign Run is still executing the notice stays up.
              const terminal = isTerminalRunStatus(snapshot.run.status);
              set({
                activeRun: snapshot.run,
                messages: reconcileAssistantMessage(get().messages, snapshot.assistant_message),
                ...(terminal ? { activeRunError: null } : {}),
              });
              if (terminal) {
                set({ polling: false, degraded: false, activeRunProgress: null });
              }
            },
            onDelta: (messageId, delta) => {
              if (stale()) return;
              set({ messages: appendDelta(get().messages, runId, messageId, delta) });
            },
            onProgress: (progress) => {
              if (stale()) return;
              set({ activeRunProgress: progress });
            },
            onStatus: (status) => {
              if (stale()) return;
              set((s) => (s.activeRun ? { activeRun: { ...s.activeRun, status } } : {}));
            },
            onTrace: (event) => {
              if (stale()) return;
              const trace = useTraceStore.getState();
              if (trace.runId === runId) trace.applyEvent(runId, event);
              if (event.nodeType) {
                if (feedNodeTypes.size >= 512)
                  feedNodeTypes.delete(feedNodeTypes.keys().next().value!);
                feedNodeTypes.set(event.nodeId, event.nodeType);
              }
              if ((event.nodeType ?? feedNodeTypes.get(event.nodeId)) === "llm") {
                scheduleBudgetRefresh(runId, generation, event.action === "start" ? 150 : 0);
              }
            },
            onTerminal: (snapshot) => {
              if (stale()) return;
              // The committed snapshot overrides the volatile delta buffer, then
              // a single GET corrects any drift (contract §12.4 run.succeeded).
              set({
                activeRun: snapshot.run,
                messages: reconcileAssistantMessage(get().messages, snapshot.assistant_message),
                activeRunError: null,
                activeRunProgress: null,
                polling: false,
                degraded: false,
              });
              void confirmRun(runId, generation);
              const trace = useTraceStore.getState();
              void trace.refreshSelectedRun(runId);
              feedNodeTypes.clear();
            },
            onDegraded: () => {
              if (stale()) return;
              // Delta assembly is permanently stopped; recover by querying the
              // Run and, while active, polling with backoff (contract §13).
              set({ degraded: true, activeRunProgress: null });
              startPolling(runId);
            },
            onAuthExpired: () => {
              if (!stale()) deps.onAuthExpired?.();
            },
          },
          { fetchImpl: deps.fetchImpl },
        );
        activeFeed = feed;
        void feed.done.then(() => {
          if (activeFeed === feed) activeFeed = null;
        });
      } catch {
        // Synchronous construction failure (never expected): poll instead.
        set({ degraded: true });
        startPolling(runId);
      }
    }

    /** Re-fetch the active Run from the conversation detail (busy recovery). */
    async function refreshActiveRun(): Promise<void> {
      const valid = fence();
      const conversationId = get().activeConversationId;
      if (!conversationId) {
        set({ activeRun: null, activeRunError: null });
        return;
      }
      const generation = get().pollGeneration;
      try {
        const detail = await api.getConversationDetail(conversationId);
        if (!valid()) return;
        const active = detail.active_run;
        const current = get().activeRun;
        const sameRun = active && current?.run_id === active.run.run_id;
        if (generation !== get().pollGeneration && !sameRun) return;
        set((s) => ({
          activeRun:
            sameRun && current.version >= active.run.version ? current : (active?.run ?? null),
          // A `conversation_busy` message is deliberately preserved: learning
          // that a foreign Run is active is not a reason to hide the notice —
          // it clears when that Run reaches terminal (see the run monitor).
          activeRunProgress: sameRun ? s.activeRunProgress : null,
          degraded: sameRun ? s.degraded : false,
          pollGeneration: active && !sameRun ? s.pollGeneration + 1 : s.pollGeneration,
        }));
        if (active && !sameRun) {
          startRunMonitor(active.run.run_id);
        }
      } catch {
        // Keep whatever Run we had; a later poll or user action will re-sync.
      }
    }

    return {
      conversationCursor: null,
      hasMoreConversations: false,
      loadingMoreConversations: false,
      activeConversation: null,
      loadingFileCandidates: false,
      fileCandidatesError: null,
      pendingSendIds: [],
      pendingSendContent: {},
      conversations: [],
      conversationsLoaded: false,
      loadingConversations: false,
      creatingConversation: false,
      conversationsError: null,
      activeConversationId: null,
      messages: [],
      messageCursor: null,
      hasMoreMessages: false,
      loadingMessages: false,
      loadingMoreMessages: false,
      attachments: [],
      fileCandidates: [],
      fileCandidatesNext: null,
      activeRun: null,
      activeRunError: null,
      activeRunProgress: null,
      degraded: false,
      polling: false,
      sending: false,
      stopping: false,
      agentStrategy: "react",
      error: null,
      pollGeneration: 0,

      loadConversations: async () => {
        const account = accountGeneration;
        if (get().loadingConversations) return;
        const generation = ++listGeneration;
        set({
          loadingConversations: true,
          loadingMoreConversations: false,
          conversationsError: null,
        });
        try {
          const page = await api.listConversations();
          if (account !== accountGeneration || generation !== listGeneration) return;
          for (const conversation of page.items) {
            const cached = localConversations.get(conversation.conversation_id);
            if (
              cached &&
              conversation.metadata_version >= cached.metadata_version &&
              conversation.updated_at >= cached.updated_at
            )
              localConversations.delete(conversation.conversation_id);
          }
          set({
            loadingConversations: false,
            conversationsLoaded: true,
            conversations: [
              ...new Map(
                [...page.items, ...localConversations.values()].map((c) => [c.conversation_id, c]),
              ).values(),
            ],
            conversationCursor: page.next_cursor,
            hasMoreConversations: page.has_more,
          });
        } catch (err) {
          if (account !== accountGeneration || generation !== listGeneration) return;
          set({ loadingConversations: false, conversationsError: messageErrorText(err) });
        }
      },
      loadMoreConversations: async () => {
        const { conversationCursor, loadingMoreConversations, loadingConversations } = get();
        if (!conversationCursor || loadingMoreConversations || loadingConversations) return;
        const generation = listGeneration,
          account = accountGeneration;
        set({ loadingMoreConversations: true, conversationsError: null });
        try {
          const page = await api.listConversations(conversationCursor);
          if (account !== accountGeneration || generation !== listGeneration) return;
          set((state) => ({
            loadingMoreConversations: false,
            conversations: [
              ...new Map(
                [...state.conversations, ...page.items].map((c) => [c.conversation_id, c]),
              ).values(),
            ],
            conversationCursor: page.next_cursor,
            hasMoreConversations: page.has_more,
          }));
        } catch (err) {
          if (account !== accountGeneration || generation !== listGeneration) return;
          set({ loadingMoreConversations: false, conversationsError: messageErrorText(err) });
        }
      },
      ensureConversation: async () => {
        if (get().activeConversationId) return get().activeConversationId;
        if (createPromise) return createPromise;
        const account = accountGeneration,
          selection = conversationSelectionGeneration;
        const draftAccountId = useAuth.getState().account?.account_id ?? "anonymous";
        const draftSnapshot = useConversationUi.getState().entries[`${draftAccountId}:new`];
        createKey ??= newIdempotencyKey();
        set({ creatingConversation: true, error: null });
        const task = (async () => {
          try {
            const result = await api.createConversation(createKey!);
            if (account !== accountGeneration) return null;
            createKey = null;
            upsert(result.conversation);
            if (
              selection === conversationSelectionGeneration ||
              useConversationUi.getState().entries[`${draftAccountId}:new`]?.revision ===
                draftSnapshot?.revision
            )
              useConversationUi
                .getState()
                .migrate(
                  `${draftAccountId}:new`,
                  `${draftAccountId}:${result.conversation.conversation_id}`,
                );
            else if (draftSnapshot)
              useConversationUi
                .getState()
                .update(`${draftAccountId}:${result.conversation.conversation_id}`, draftSnapshot);
            void get().loadConversations();
            if (selection !== conversationSelectionGeneration) return null;
            set({
              activeConversationId: result.conversation.conversation_id,
              activeConversation: result.conversation,
            });
            return result.conversation.conversation_id;
          } catch (err) {
            if (account === accountGeneration && selection === conversationSelectionGeneration)
              set({ error: messageErrorText(err) });
            return null;
          } finally {
            if (account === accountGeneration) {
              createPromise = null;
              set({ creatingConversation: false });
            }
          }
        })();
        createPromise = task;
        return task;
      },
      leaveConversation: () => {
        conversationSelectionGeneration += 1;
        candidateGeneration += 1;
        closeFeed();
        useTraceStore.getState().reset();
        set((state) => ({
          activeConversationId: null,
          activeConversation: null,
          messages: [],
          attachments: [],
          fileCandidates: [],
          fileCandidatesNext: null,
          loadingFileCandidates: false,
          fileCandidatesError: null,
          activeRun: null,
          activeRunError: null,
          activeRunProgress: null,
          messageCursor: null,
          hasMoreMessages: false,
          loadingMessages: false,
          loadingMoreMessages: false,
          sending: false,
          stopping: false,
          degraded: false,
          polling: false,
          error: null,
          pollGeneration: state.pollGeneration + 1,
        }));
      },
      renameActiveConversation: async (title) => {
        const conversation = get().activeConversation;
        const account = accountGeneration;
        if (!conversation) return false;
        const target = title.trim();
        if (!target || [...target].length > 200) {
          set({ error: "标题须为 1–200 个字符。" });
          return false;
        }
        try {
          const result = await api.renameConversation(
            conversation.conversation_id,
            target,
            `"conversation-${conversation.conversation_id}-m${conversation.metadata_version}"`,
            newIdempotencyKey(),
          );
          if (account !== accountGeneration) return false;
          upsert(result.conversation);
          void get().loadConversations();
          return true;
        } catch (err) {
          if (account !== accountGeneration) return false;
          try {
            const detail = await api.getConversationDetail(conversation.conversation_id);
            if (account !== accountGeneration) return false;
            upsert(detail.conversation);
            if (detail.conversation.title === target) return true;
          } catch {
            /* Preserve the edit and surface the original error. */
          }
          if (get().activeConversationId === conversation.conversation_id)
            set({
              error:
                err instanceof HpCommandError && err.status === 412
                  ? "标题已被更新，请检查后再次保存。"
                  : messageErrorText(err),
            });
          return false;
        }
      },

      createConversation: async () => {
        const valid = fence();
        if (get().creatingConversation) return;
        set({ creatingConversation: true, error: null });
        try {
          const result = await api.createConversation(newIdempotencyKey());
          if (!valid()) return;
          const conversation = result.conversation;
          conversationSelectionGeneration += 1;
          closeFeed();
          set((s) => ({
            creatingConversation: false,
            conversations: [
              conversation,
              ...s.conversations.filter((c) => c.conversation_id !== conversation.conversation_id),
            ],
            activeConversationId: conversation.conversation_id,
            activeConversation: conversation,
            loadingMessages: false,
            messages: [],
            attachments: [],
            fileCandidates: [],
            fileCandidatesNext: null,
            messageCursor: null,
            hasMoreMessages: false,
            activeRun: null,
            activeRunError: null,
            activeRunProgress: null,
            degraded: false,
            error: null,
            pollGeneration: s.pollGeneration + 1,
          }));
        } catch (err) {
          if (valid()) set({ creatingConversation: false, error: messageErrorText(err) });
        }
      },

      selectConversation: async (id, refresh = false) => {
        const refreshing = id === get().activeConversationId;
        if (refreshing && !refresh) return;
        const generation = ++conversationSelectionGeneration;
        if (!refreshing) {
          candidateGeneration += 1;
          closeFeed();
          useTraceStore.getState().reset();
          set((s) => ({
            activeConversationId: id,
            activeConversation: null,
            loadingFileCandidates: false,
            fileCandidatesError: null,
            creatingConversation: false,
            sending: false,
            stopping: false,
            loadingMoreMessages: false,
            messages: [],
            attachments: [],
            fileCandidates: [],
            fileCandidatesNext: null,
            messageCursor: null,
            hasMoreMessages: true,
            activeRun: null,
            activeRunError: null,
            activeRunProgress: null,
            degraded: false,
            error: null,
            pollGeneration: s.pollGeneration + 1,
          }));
        }
        set({ loadingMessages: true });
        const runGeneration = get().pollGeneration;
        try {
          const [detail, page] = await Promise.all([
            api.getConversationDetail(id),
            api.listMessages(id),
          ]);
          if (generation !== conversationSelectionGeneration) return;
          if (refreshing && runGeneration !== get().pollGeneration) {
            set({ loadingMessages: false });
            return;
          }
          const active = detail.active_run;
          set((s) => ({
            loadingMessages: false,
            activeConversation: detail.conversation,
            // The API normalizes each page to chat order (oldest first), so
            // send-time appends land at the end naturally.
            messages: page.items,
            messageCursor: page.next_cursor,
            hasMoreMessages: page.has_more,
            activeRun: active?.run ?? null,
            activeRunError: null,
            activeRunProgress: null,
            degraded: false,
            pollGeneration: s.pollGeneration + 1,
          }));
          if (active) {
            startRunMonitor(active.run.run_id);
          }
        } catch (err) {
          if (generation !== conversationSelectionGeneration) return;
          set({
            loadingMessages: false,
            activeConversationId: null,
            error:
              err instanceof HpCommandError && [403, 404].includes(err.status)
                ? "对象不可用。"
                : messageErrorText(err),
          });
        }
      },

      loadMoreMessages: async () => {
        const valid = fence();
        const conversationId = get().activeConversationId;
        const cursor = get().messageCursor;
        if (!conversationId || !cursor || get().loadingMoreMessages) return;
        set({ loadingMoreMessages: true });
        try {
          const page = await api.listMessages(conversationId, cursor);
          if (!valid()) return;
          const seen = new Set(get().messages.map((m) => m.message_id));
          const fresh = page.items.filter((m) => !seen.has(m.message_id));
          set((s) => ({
            loadingMoreMessages: false,
            // Older pages are also returned oldest-first; prepend unchanged.
            messages: fresh.concat(s.messages),
            messageCursor: page.next_cursor,
            hasMoreMessages: page.has_more,
          }));
        } catch (err) {
          if (!valid()) return;
          set({ loadingMoreMessages: false, error: messageErrorText(err) });
        }
      },

      addAttachments: async (selectedFiles) => {
        const account = accountGeneration;
        const valid = fence();
        const conversationId = get().activeConversationId;
        if (!conversationId || selectedFiles.length === 0) return;
        const active = get().activeRun;
        if (get().sending || (active && !isTerminalRunStatus(active.status))) return;

        const available = Math.max(0, 10 - get().attachments.length);
        const accepted = selectedFiles.slice(0, available);
        if (accepted.length === 0) {
          set({ error: "每条消息最多添加 10 个附件。" });
          return;
        }
        if (accepted.length < selectedFiles.length) {
          set({ error: "每条消息最多添加 10 个附件，多余文件未加入。" });
        }

        const queued = accepted.map<UploadAttachment>((file) => ({
          localId: newIdempotencyKey(),
          name: file.name,
          size: file.size,
          status: "uploading",
          fileId: null,
          file: null,
          error: null,
        }));
        set((state) => ({ attachments: [...state.attachments, ...queued] }));

        await Promise.all(
          accepted.map(async (browserFile, index) => {
            const attachment = queued[index];
            if (!attachment) return;
            try {
              const created = await api.createUpload(
                conversationId,
                browserFile,
                newIdempotencyKey(),
              );
              if (!valid()) {
                if (account === accountGeneration) await api.deleteFile(created.file.file_id);
                return;
              }
              set((state) => ({
                attachments: state.attachments.map((item) =>
                  item.localId === attachment.localId
                    ? { ...item, fileId: created.file.file_id }
                    : item,
                ),
              }));
              if (!get().attachments.some((item) => item.localId === attachment.localId)) {
                await api.deleteFile(created.file.file_id);
                return;
              }
              const uploaded = await api.uploadContent(created.content_url, browserFile);
              if (!valid()) {
                if (account === accountGeneration) await api.deleteFile(uploaded.file_id);
                return;
              }
              set((state) => ({
                attachments: state.attachments.map((item) =>
                  item.localId === attachment.localId
                    ? { ...item, status: "ready", fileId: uploaded.file_id, file: uploaded }
                    : item,
                ),
              }));
              if (!get().attachments.some((item) => item.localId === attachment.localId)) {
                await api.deleteFile(uploaded.file_id);
              }
            } catch (err) {
              if (!valid()) return;
              set((state) => ({
                attachments: state.attachments.map((item) =>
                  item.localId === attachment.localId
                    ? { ...item, status: "failed", error: messageErrorText(err) }
                    : item,
                ),
              }));
            }
          }),
        );
      },

      selectExistingFile: (file) => {
        if (get().sending || (get().activeRun && !isTerminalRunStatus(get().activeRun!.status)))
          return;
        if (
          file.status !== "ready" ||
          get().attachments.length >= 10 ||
          get().attachments.some((item) => item.fileId === file.file_id)
        )
          return;
        set((state) => ({
          attachments: [
            ...state.attachments,
            {
              localId: newIdempotencyKey(),
              name: file.file_name,
              size: file.size_bytes ?? 0,
              status: "ready",
              fileId: file.file_id,
              file,
              error: null,
              existing: true,
            },
          ],
        }));
      },

      loadFileCandidates: async (more = false) => {
        const valid = fence();
        const conversationId = get().activeConversationId;
        if (!conversationId || get().loadingFileCandidates || (more && !get().fileCandidatesNext))
          return;
        const generation = ++candidateGeneration;
        set({ loadingFileCandidates: true, fileCandidatesError: null });
        try {
          const page = await api.listFileCandidates(
            conversationId,
            more ? get().fileCandidatesNext : null,
          );
          if (!valid() || generation !== candidateGeneration) return;
          set((state) => ({
            loadingFileCandidates: false,
            fileCandidates: [
              ...new Map(
                (more ? [...state.fileCandidates, ...page.items] : page.items).map((f) => [
                  f.file_id,
                  f,
                ]),
              ).values(),
            ],
            fileCandidatesNext: page.next_before,
          }));
        } catch (err) {
          if (!valid() || generation !== candidateGeneration) return;
          set({ loadingFileCandidates: false, fileCandidatesError: messageErrorText(err) });
        }
      },

      removeAttachment: async (localId) => {
        const valid = fence();
        const attachment = get().attachments.find((item) => item.localId === localId);
        if (!attachment) return;
        set((state) => ({
          attachments: state.attachments.filter((item) => item.localId !== localId),
        }));
        if (attachment.fileId && !attachment.existing) {
          try {
            await api.deleteFile(attachment.fileId);
          } catch (err) {
            if (!valid()) return;
            set({ error: messageErrorText(err) });
          }
        }
      },

      confirmPendingSend: async () => {
        const id = get().activeConversationId;
        const intent = id ? intents.get(id) : null;
        return intent ? get().sendMessage(intent.content, true) : false;
      },
      sendMessage: async (content, confirm = false) => {
        const account = accountGeneration;
        const valid = fence();
        const conversationId = get().activeConversationId;
        const trimmed = content.trim();
        const draftKey = `${useAuth.getState().account?.account_id ?? "anonymous"}:${conversationId}`;
        const submittedDraft = useConversationUi.getState().entries[draftKey];
        if (!conversationId || !trimmed) return false;
        // Only a live Run blocks sending: after a terminal Run the composer is
        // re-enabled so a long conversation continues in place (E-07).
        const active = get().activeRun;
        if (
          get().sending ||
          get().stopping ||
          (!confirm && active && !isTerminalRunStatus(active.status))
        ) {
          return false;
        }
        const attachments = get().attachments;
        if (!confirm && attachments.some((item) => item.status !== "ready" || !item.fileId)) {
          set({ error: "请等待附件上传完成，或移除上传失败的附件。" });
          return false;
        }

        const previousIntent = intents.get(conversationId);
        if (
          !confirm &&
          previousIntent &&
          (previousIntent.content !== trimmed ||
            previousIntent.strategy !== get().agentStrategy ||
            JSON.stringify(previousIntent.fileIds) !==
              JSON.stringify(attachments.map((item) => item.fileId)))
        ) {
          set({ error: "上次发送结果尚未确认，请先用原内容和附件重新确认。" });
          return false;
        }
        const intent = previousIntent ?? {
          key: newIdempotencyKey(),
          content: trimmed,
          fileIds: attachments.map((item) => item.fileId as string),
          strategy: get().agentStrategy,
        };
        intents.set(conversationId, intent);
        set({
          pendingSendIds: [...intents.keys()],
          pendingSendContent: Object.fromEntries(
            [...intents].map(([id, intent]) => [id, intent.content]),
          ),
        });
        const idempotencyKey = intent.key;
        const tempId = `temp:${idempotencyKey}`;
        const tempMessage: HpMessage = {
          message_id: tempId,
          conversation_id: conversationId,
          role: "user",
          status: "accepted",
          content: trimmed,
          sequence: 0,
          client_request_id: null,
          produced_by_run_id: null,
          created_at: new Date().toISOString(),
          completed_at: null,
        };
        set((s) => ({ sending: true, error: null, messages: [...s.messages, tempMessage] }));

        try {
          const result = await api.sendMessage(conversationId, trimmed, {
            idempotencyKey,
            agentStrategy: intent.strategy,
            fileIds: intent.fileIds,
          });
          if (account === accountGeneration) {
            const currentDraft = useConversationUi.getState().entries[draftKey];
            if (
              currentDraft &&
              submittedDraft &&
              currentDraft.revision === submittedDraft.revision &&
              currentDraft.text.trim() === trimmed
            )
              useConversationUi
                .getState()
                .update(draftKey, { text: "", revision: currentDraft.revision + 1 });
            intents.delete(conversationId);
            set({
              pendingSendIds: [...intents.keys()],
              pendingSendContent: Object.fromEntries(
                [...intents].map(([id, intent]) => [id, intent.content]),
              ),
            });
          }
          if (!valid()) {
            // A new selection may have read the conversation before this POST
            // committed. Re-read the selected object rather than applying the
            // old response across its selection generation.
            if (account === accountGeneration && get().activeConversationId === conversationId)
              await get().selectConversation(conversationId, true);
            return false;
          }
          const currentRun = get().activeRun;
          const sameRun = currentRun?.run_id === result.run.run_id;
          const resolvedRun =
            sameRun && currentRun.version >= result.run.version ? currentRun : result.run;
          const existingAssistant = sameRun
            ? get().messages.find((m) => m.message_id === result.assistant_message.message_id)
            : null;
          set((s) => ({
            sending: false,
            messages: replaceTempMessage(
              s.messages,
              tempId,
              result.user_message,
              existingAssistant ?? result.assistant_message,
            ),
            activeRun: resolvedRun,
            activeRunError: null,
            activeRunProgress: sameRun ? s.activeRunProgress : null,
            degraded: sameRun ? s.degraded : false,
            attachments: s.attachments.filter(
              (a) => !a.fileId || !intent.fileIds.includes(a.fileId),
            ),
            pollGeneration: sameRun ? s.pollGeneration : s.pollGeneration + 1,
          }));
          if (!sameRun) startRunMonitor(result.run.run_id);
          return true;
        } catch (err) {
          if (account === accountGeneration && err instanceof HpCommandError && err.status < 500) {
            intents.delete(conversationId);
            set({
              pendingSendIds: [...intents.keys()],
              pendingSendContent: Object.fromEntries(
                [...intents].map(([id, intent]) => [id, intent.content]),
              ),
            });
          }
          if (!valid()) return false;
          const messages = get().messages.filter((m) => m.message_id !== tempId);
          if (err instanceof HpCommandError && err.code === "conversation_busy") {
            set({ sending: false, messages, activeRunError: err.error.message });
            void refreshActiveRun();
          } else {
            set({
              sending: false,
              messages,
              error: intents.has(conversationId)
                ? "发送结果尚未确认，请保留原内容重试确认。"
                : messageErrorText(err),
            });
            if (intents.has(conversationId)) void refreshActiveRun();
          }
          return false;
        }
      },

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

      clearError: () => set({ error: null, activeRunError: null }),
      reset: () => {
        accountGeneration += 1;
        listGeneration += 1;
        candidateGeneration += 1;
        createKey = null;
        createPromise = null;
        intents.clear();
        localConversations.clear();
        conversationSelectionGeneration += 1;
        closeFeed();
        pendingSleeps.forEach((resolve, timer) => {
          clearTimeout(timer);
          resolve();
        });
        pendingSleeps.clear();
        set({
          pendingSendIds: [],
          pendingSendContent: {},
          conversationCursor: null,
          hasMoreConversations: false,
          loadingMoreConversations: false,
          activeConversation: null,
          loadingFileCandidates: false,
          fileCandidatesError: null,
          conversations: [],
          conversationsLoaded: false,
          loadingConversations: false,
          creatingConversation: false,
          conversationsError: null,
          activeConversationId: null,
          messages: [],
          messageCursor: null,
          hasMoreMessages: false,
          loadingMessages: false,
          loadingMoreMessages: false,
          attachments: [],
          fileCandidates: [],
          fileCandidatesNext: null,
          activeRun: null,
          activeRunError: null,
          activeRunProgress: null,
          degraded: false,
          polling: false,
          sending: false,
          stopping: false,
          agentStrategy: "react",
          error: null,
          pollGeneration: get().pollGeneration + 1,
        });
      },
    };
  });
}

/** Singleton bound to the real API client. */
export const useWorkbench = createWorkbenchStore();
