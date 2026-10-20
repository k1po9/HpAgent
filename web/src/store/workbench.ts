/** Conversation store facade; shared lifetime and reset are owned here. */
import { create } from "zustand";
import { api as defaultApi } from "../api/client";
import { HpApi } from "../api/resources";
import { useAuth } from "./auth";
import type { WorkbenchDeps, WorkbenchState } from "./workbench/types";
import { createWorkbenchSession } from "./workbench/context";
import { createRunMonitor } from "./workbench/runMonitor";
import { conversationActions } from "./workbench/conversationActions";
import { attachmentActions } from "./workbench/attachmentActions";
import { sendActions } from "./workbench/sendActions";
import { runActions } from "./workbench/runActions";
export type {
  WorkbenchDeps,
  WorkbenchState,
  UploadAttachment,
  UploadAttachmentStatus,
} from "./workbench/types";
export {
  isTerminalRunStatus,
  isCancellableRunStatus,
  isRetryableRun,
  runStatusLabel,
} from "./workbench/runStatus";

export function createWorkbenchStore(
  deps: WorkbenchDeps = {
    api: new HpApi(defaultApi),
    onAuthExpired: () => useAuth.getState().expire(),
  },
) {
  return create<WorkbenchState>()((set, get) => {
    const session = createWorkbenchSession();
    const fence = () => {
      const account = session.accountGeneration;
      const selection = session.conversationSelectionGeneration;
      return () =>
        account === session.accountGeneration &&
        selection === session.conversationSelectionGeneration;
    };
    const context = { deps, api: deps.api, set, get, session, fence };
    const monitor = createRunMonitor(context);
    const { closeFeed } = monitor;
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
      ...conversationActions(context, monitor),
      ...attachmentActions(context),
      ...sendActions(context, monitor),
      ...runActions(context, monitor),
      clearError: () => set({ error: null, activeRunError: null }),
      reset: () => {
        session.accountGeneration += 1;
        session.listGeneration += 1;
        session.candidateGeneration += 1;
        session.createKey = null;
        session.createPromise = null;
        session.intents.clear();
        session.localConversations.clear();
        session.conversationSelectionGeneration += 1;
        closeFeed();
        monitor.clearSleeps();
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
export const useWorkbench = createWorkbenchStore();
