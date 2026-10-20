import type { StoreApi } from "zustand";
import type { HpConversation, AgentStrategy } from "../../api/types";
import type { WorkbenchState, WorkbenchDeps } from "./types";

/** Shared account/object lifetime; slices never invent independent generations. */
export function createWorkbenchSession() {
  return {
    conversationSelectionGeneration: 0,
    accountGeneration: 0,
    listGeneration: 0,
    candidateGeneration: 0,
    createKey: null as string | null,
    createPromise: null as Promise<string | null> | null,
    localConversations: new Map<string, HpConversation>(),
    intents: new Map<
      string,
      { key: string; content: string; fileIds: string[]; strategy: AgentStrategy }
    >(),
  };
}
export type WorkbenchSession = ReturnType<typeof createWorkbenchSession>;
export interface WorkbenchContext {
  set: StoreApi<WorkbenchState>["setState"];
  get: () => WorkbenchState;
  api: WorkbenchDeps["api"];
  deps: WorkbenchDeps;
  session: WorkbenchSession;
  fence: () => () => boolean;
}
