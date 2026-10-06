import { create } from "zustand";
import type { AgentStrategy } from "../api/types";

export interface ConversationUi {
  text: string;
  revision: number;
  strategy: AgentStrategy;
  anchorMessageId?: string;
  offset: number;
  atBottom: boolean;
}
export const emptyConversationUi: ConversationUi = {
  text: "",
  revision: 0,
  strategy: "react",
  offset: 0,
  atBottom: true,
};
// Session-scoped only. Non-empty drafts are never evicted.
export const useConversationUi = create<{
  entries: Record<string, ConversationUi>;
  update: (key: string, patch: Partial<ConversationUi>) => void;
  migrate: (from: string, to: string) => void;
  reset: () => void;
}>((set) => ({
  entries: {},
  update: (key, patch) =>
    set((state) => {
      const entries = { ...state.entries };
      const previous = entries[key] ?? emptyConversationUi;
      delete entries[key];
      entries[key] = { ...previous, ...patch };
      const disposable = Object.keys(entries).filter((id) => id !== key && !entries[id]!.text);
      for (const id of disposable.slice(0, Math.max(0, disposable.length - 30))) delete entries[id];
      return { entries };
    }),
  migrate: (from, to) =>
    set((state) => {
      const entries = { ...state.entries, [to]: state.entries[from] ?? emptyConversationUi };
      delete entries[from];
      return { entries };
    }),
  reset: () => set({ entries: {} }),
}));
