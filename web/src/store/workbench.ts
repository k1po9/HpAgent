/**
 * Workbench state (phase-e E-03/E-04).
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
 * - `stop`/`retry` call the cancel/retry APIs; a failed or cancelled Run can be
 *   retried, reusing the original user message.
 * - Run state is refreshed by keyset polling with backoff (1s/2s/3s/5s) until
 *   the Run reaches a terminal state. E-06 replaces the polling cadence with the
 *   SSE subscription; `applyRunSnapshot` is shared by both.
 */
import { create } from "zustand";
import { api as defaultApi } from "../api/client";
import { HpApi } from "../api/resources";
import {
  HpCommandError,
  type HpConversation,
  type HpMessage,
  type HpRun,
  type HpRunStatus,
} from "../api/types";

/** One idempotency key per user intent; never rotate implicitly. */
export function newIdempotencyKey(): string {
  return crypto.randomUUID();
}

const TERMINAL_RUN_STATUS = new Set<HpRunStatus>(["completed", "failed", "cancelled"]);

export function isTerminalRunStatus(status: HpRunStatus): boolean {
  return TERMINAL_RUN_STATUS.has(status);
}

export function isCancellableRunStatus(status: HpRunStatus): boolean {
  return status === "queued" || status === "running";
}

export function isRetryableRunStatus(status: HpRunStatus): boolean {
  return status === "failed" || status === "cancelled";
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
    case "completed":
      return "已完成";
    case "failed":
      return "运行失败";
    case "cancelled":
      return "已停止";
  }
}

const POLL_DELAYS = [1000, 2000, 3000, 5000] as const;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export interface WorkbenchDeps {
  api: HpApi;
}

export interface WorkbenchState {
  // Conversation list
  conversations: HpConversation[];
  conversationsLoaded: boolean;
  loadingConversations: boolean;
  creatingConversation: boolean;
  conversationsError: string | null;

  // Active conversation
  activeConversationId: string | null;
  messages: HpMessage[];
  messageCursor: string | null;
  hasMoreMessages: boolean;
  loadingMessages: boolean;
  loadingMoreMessages: boolean;

  // Active Run
  activeRun: HpRun | null;
  activeRunError: string | null;
  polling: boolean;
  sending: boolean;
  stopping: boolean;

  // Transient UI error (dismissible)
  error: string | null;

  // Bumped whenever a new Run becomes authoritative so stale pollers stop.
  pollGeneration: number;

  loadConversations: () => Promise<void>;
  createConversation: () => Promise<void>;
  selectConversation: (id: string) => Promise<void>;
  loadMoreMessages: () => Promise<void>;
  sendMessage: (content: string) => Promise<boolean>;
  stopRun: () => Promise<void>;
  retryRun: () => Promise<void>;
  refreshActiveRun: () => Promise<void>;
  clearError: () => void;
}

/** Replace the temp user message with authoritative ids; append the assistant msg. */
function replaceTempMessage(
  messages: HpMessage[],
  tempId: string,
  userMessage: HpMessage,
  assistantMessage: HpMessage,
): HpMessage[] {
  const next = messages.filter((m) => m.message_id !== tempId);
  return [...next, userMessage, assistantMessage];
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

function messageErrorText(err: unknown): string {
  if (err instanceof HpCommandError) {
    return err.error.message;
  }
  if (err instanceof Error) {
    return err.message;
  }
  return "发生未知错误，请重试。";
}

export function createWorkbenchStore(deps: WorkbenchDeps = { api: new HpApi(defaultApi) }) {
  const { api } = deps;

  return create<WorkbenchState>()((set, get) => {
    /** Poll a Run until terminal; stops when a newer Run supersedes it. */
    function startPolling(runId: string): void {
      const generation = get().pollGeneration;
      void (async () => {
        let attempt = 0;
        for (;;) {
          const delay = POLL_DELAYS[Math.min(attempt, POLL_DELAYS.length - 1)] ?? 5000;
          await sleep(delay);
          attempt += 1;
          const current = get();
          if (current.pollGeneration !== generation) return;
          if (current.activeRun?.run_id !== runId) return;
          if (current.activeRun && isTerminalRunStatus(current.activeRun.status)) {
            set({ polling: false });
            return;
          }
          try {
            const snapshot = await api.getRun(runId);
            const latest = get();
            if (latest.pollGeneration !== generation) return;
            const run = snapshot.run;
            set({
              activeRun: run,
              messages: reconcileAssistantMessage(latest.messages, snapshot.assistant_message),
              activeRunError: null,
              polling: !isTerminalRunStatus(run.status),
            });
            if (isTerminalRunStatus(run.status)) return;
          } catch {
            // Transient fetch error: keep polling with the current backoff.
          }
        }
      })();
    }

    /** Re-fetch the active Run from the conversation detail (busy recovery). */
    async function refreshActiveRun(): Promise<void> {
      const conversationId = get().activeConversationId;
      if (!conversationId) {
        set({ activeRun: null, activeRunError: null });
        return;
      }
      try {
        const detail = await api.getConversationDetail(conversationId);
        const active = detail.active_run;
        set((s) => ({
          activeRun: active?.run ?? null,
          activeRunError: null,
          pollGeneration: active ? s.pollGeneration + 1 : s.pollGeneration,
        }));
        if (active) {
          startPolling(active.run.run_id);
        }
      } catch {
        // Keep whatever Run we had; a later poll or user action will re-sync.
      }
    }

    return {
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
      activeRun: null,
      activeRunError: null,
      polling: false,
      sending: false,
      stopping: false,
      error: null,
      pollGeneration: 0,

      loadConversations: async () => {
        if (get().loadingConversations) return;
        set({ loadingConversations: true, conversationsError: null });
        try {
          const page = await api.listConversations();
          set({
            loadingConversations: false,
            conversationsLoaded: true,
            conversations: page.items,
          });
        } catch (err) {
          set({
            loadingConversations: false,
            conversationsError: messageErrorText(err),
          });
        }
      },

      createConversation: async () => {
        if (get().creatingConversation) return;
        set({ creatingConversation: true, error: null });
        try {
          const result = await api.createConversation(newIdempotencyKey());
          const conversation = result.conversation;
          set((s) => ({
            creatingConversation: false,
            conversations: [
              conversation,
              ...s.conversations.filter((c) => c.conversation_id !== conversation.conversation_id),
            ],
            activeConversationId: conversation.conversation_id,
            messages: [],
            messageCursor: null,
            hasMoreMessages: false,
            activeRun: null,
            activeRunError: null,
            error: null,
            pollGeneration: s.pollGeneration + 1,
          }));
        } catch (err) {
          set({ creatingConversation: false, error: messageErrorText(err) });
        }
      },

      selectConversation: async (id) => {
        if (id === get().activeConversationId) return;
        set((s) => ({
          activeConversationId: id,
          messages: [],
          messageCursor: null,
          hasMoreMessages: true,
          activeRun: null,
          activeRunError: null,
          error: null,
          pollGeneration: s.pollGeneration + 1,
        }));
        set({ loadingMessages: true });
        try {
          const [detail, page] = await Promise.all([
            api.getConversationDetail(id),
            api.listMessages(id),
          ]);
          const active = detail.active_run;
          set((s) => ({
            loadingMessages: false,
            // The API returns newest-first; the store keeps chat order (oldest
            // first) so send-time appends land at the end naturally.
            messages: [...page.items].reverse(),
            messageCursor: page.next_cursor,
            hasMoreMessages: page.has_more,
            activeRun: active?.run ?? null,
            activeRunError: null,
            pollGeneration: s.pollGeneration + 1,
          }));
          if (active) {
            startPolling(active.run.run_id);
          }
        } catch (err) {
          set({ loadingMessages: false, error: messageErrorText(err) });
        }
      },

      loadMoreMessages: async () => {
        const conversationId = get().activeConversationId;
        const cursor = get().messageCursor;
        if (!conversationId || !cursor || get().loadingMoreMessages) return;
        set({ loadingMoreMessages: true });
        try {
          const page = await api.listMessages(conversationId, cursor);
          const seen = new Set(get().messages.map((m) => m.message_id));
          const fresh = page.items.filter((m) => !seen.has(m.message_id));
          set((s) => ({
            loadingMoreMessages: false,
            // Older batch is newest-first; prepend in chat order (oldest first).
            messages: [...fresh].reverse().concat(s.messages),
            messageCursor: page.next_cursor,
            hasMoreMessages: page.has_more,
          }));
        } catch (err) {
          set({ loadingMoreMessages: false, error: messageErrorText(err) });
        }
      },

      sendMessage: async (content) => {
        const conversationId = get().activeConversationId;
        const trimmed = content.trim();
        if (!conversationId || !trimmed) return false;
        if (get().sending || get().stopping || get().activeRun) return false;

        const idempotencyKey = newIdempotencyKey();
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
          const result = await api.sendMessage(conversationId, trimmed, { idempotencyKey });
          set((s) => ({
            sending: false,
            messages: replaceTempMessage(
              s.messages,
              tempId,
              result.user_message,
              result.assistant_message,
            ),
            activeRun: result.run,
            activeRunError: null,
            pollGeneration: s.pollGeneration + 1,
          }));
          startPolling(result.run.run_id);
          return true;
        } catch (err) {
          const messages = get().messages.filter((m) => m.message_id !== tempId);
          if (err instanceof HpCommandError && err.code === "conversation_busy") {
            set({ sending: false, messages, activeRunError: err.error.message });
            void refreshActiveRun();
          } else {
            set({ sending: false, messages, error: messageErrorText(err) });
          }
          return false;
        }
      },

      stopRun: async () => {
        const run = get().activeRun;
        if (!run || !isCancellableRunStatus(run.status) || get().stopping) return;
        set({ stopping: true, error: null });
        try {
          await api.cancelRun(run.run_id, newIdempotencyKey());
          // The active poll observes the terminal cancelled state.
        } catch (err) {
          if (err instanceof HpCommandError && err.code === "run_not_cancellable") {
            void refreshActiveRun();
          } else {
            set({ error: messageErrorText(err) });
          }
        } finally {
          set({ stopping: false });
        }
      },

      retryRun: async () => {
        const run = get().activeRun;
        if (!run || !isRetryableRunStatus(run.status)) return;
        if (get().sending || get().stopping) return;
        set({ sending: true, error: null });
        try {
          const result = await api.retryRun(run.run_id, newIdempotencyKey());
          set((s) => ({
            sending: false,
            messages: reconcileAssistantMessage(s.messages, result.assistant_message),
            activeRun: result.run,
            activeRunError: null,
            pollGeneration: s.pollGeneration + 1,
          }));
          startPolling(result.run.run_id);
        } catch (err) {
          if (err instanceof HpCommandError && err.code === "conversation_busy") {
            set({ sending: false, activeRunError: err.error.message });
            void refreshActiveRun();
          } else if (err instanceof HpCommandError && err.code === "run_not_retryable") {
            set({ sending: false, error: "当前状态不可重试。" });
            void refreshActiveRun();
          } else {
            set({ sending: false, error: messageErrorText(err) });
          }
        }
      },

      refreshActiveRun,

      clearError: () => set({ error: null, activeRunError: null }),
    };
  });
}

/** Singleton bound to the real API client. */
export const useWorkbench = createWorkbenchStore();
