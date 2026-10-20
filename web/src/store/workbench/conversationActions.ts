import { useConversationUi } from "../conversationUi";
import { useAuth } from "../auth";
import { newIdempotencyKey } from "../../utils/idempotency";
import { useTraceStore } from "../../components/trace/traceStore";
import { HpCommandError, type HpConversation } from "../../api/types";
import type { WorkbenchContext } from "./context";
import type { WorkbenchState } from "./types";
import type { RunMonitor } from "./runMonitor";
import { messageErrorText } from "./messages";

export function conversationActions(
  context: WorkbenchContext,
  monitor: RunMonitor,
): Pick<
  WorkbenchState,
  | "loadConversations"
  | "loadMoreConversations"
  | "ensureConversation"
  | "leaveConversation"
  | "renameActiveConversation"
  | "createConversation"
  | "selectConversation"
  | "loadMoreMessages"
> {
  const { api, set, get, session, fence } = context;
  const { closeFeed, startRunMonitor } = monitor;
  const upsert = (conversation: HpConversation) => {
    const known = session.localConversations.get(conversation.conversation_id);
    if (known && known.metadata_version > conversation.metadata_version) return;
    session.localConversations.set(conversation.conversation_id, conversation);
    session.listGeneration += 1;
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
  return {
    loadConversations: async () => {
      const account = session.accountGeneration;
      if (get().loadingConversations) return;
      const generation = ++session.listGeneration;
      set({
        loadingConversations: true,
        loadingMoreConversations: false,
        conversationsError: null,
      });
      try {
        const page = await api.listConversations();
        if (account !== session.accountGeneration || generation !== session.listGeneration) return;
        for (const conversation of page.items) {
          const cached = session.localConversations.get(conversation.conversation_id);
          if (
            cached &&
            conversation.metadata_version >= cached.metadata_version &&
            conversation.updated_at >= cached.updated_at
          )
            session.localConversations.delete(conversation.conversation_id);
        }
        set({
          loadingConversations: false,
          conversationsLoaded: true,
          conversations: [
            ...new Map(
              [...page.items, ...session.localConversations.values()].map((c) => [
                c.conversation_id,
                c,
              ]),
            ).values(),
          ],
          conversationCursor: page.next_cursor,
          hasMoreConversations: page.has_more,
        });
      } catch (err) {
        if (account !== session.accountGeneration || generation !== session.listGeneration) return;
        set({ loadingConversations: false, conversationsError: messageErrorText(err) });
      }
    },
    loadMoreConversations: async () => {
      const { conversationCursor, loadingMoreConversations, loadingConversations } = get();
      if (!conversationCursor || loadingMoreConversations || loadingConversations) return;
      const generation = session.listGeneration,
        account = session.accountGeneration;
      set({ loadingMoreConversations: true, conversationsError: null });
      try {
        const page = await api.listConversations(conversationCursor);
        if (account !== session.accountGeneration || generation !== session.listGeneration) return;
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
        if (account !== session.accountGeneration || generation !== session.listGeneration) return;
        set({ loadingMoreConversations: false, conversationsError: messageErrorText(err) });
      }
    },
    ensureConversation: async () => {
      if (get().activeConversationId) return get().activeConversationId;
      if (session.createPromise) return session.createPromise;
      const account = session.accountGeneration,
        selection = session.conversationSelectionGeneration;
      const draftAccountId = useAuth.getState().account?.account_id ?? "anonymous";
      const draftSnapshot = useConversationUi.getState().entries[`${draftAccountId}:new`];
      session.createKey ??= newIdempotencyKey();
      set({ creatingConversation: true, error: null });
      const task = (async () => {
        try {
          const result = await api.createConversation(session.createKey!);
          if (account !== session.accountGeneration) return null;
          session.createKey = null;
          upsert(result.conversation);
          if (
            selection === session.conversationSelectionGeneration ||
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
          if (selection !== session.conversationSelectionGeneration) return null;
          set({
            activeConversationId: result.conversation.conversation_id,
            activeConversation: result.conversation,
          });
          return result.conversation.conversation_id;
        } catch (err) {
          if (
            account === session.accountGeneration &&
            selection === session.conversationSelectionGeneration
          )
            set({ error: messageErrorText(err) });
          return null;
        } finally {
          if (account === session.accountGeneration) {
            session.createPromise = null;
            set({ creatingConversation: false });
          }
        }
      })();
      session.createPromise = task;
      return task;
    },
    leaveConversation: () => {
      session.conversationSelectionGeneration += 1;
      session.candidateGeneration += 1;
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
      const account = session.accountGeneration;
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
        if (account !== session.accountGeneration) return false;
        upsert(result.conversation);
        void get().loadConversations();
        return true;
      } catch (err) {
        if (account !== session.accountGeneration) return false;
        try {
          const detail = await api.getConversationDetail(conversation.conversation_id);
          if (account !== session.accountGeneration) return false;
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
        session.conversationSelectionGeneration += 1;
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
      const generation = ++session.conversationSelectionGeneration;
      if (!refreshing) {
        session.candidateGeneration += 1;
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
        if (generation !== session.conversationSelectionGeneration) return;
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
        if (generation !== session.conversationSelectionGeneration) return;
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
  };
}
