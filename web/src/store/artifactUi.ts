import { create } from "zustand";
export const useArtifactUi = create<{
  drafts: Record<string, { text: string; revision: number }>;
  edit: (id: string, text: string) => void;
  clearSubmitted: (id: string, revision: number) => void;
  reset: () => void;
}>((set) => ({
  drafts: {},
  edit: (id, text) =>
    set((s) => ({
      drafts: { ...s.drafts, [id]: { text, revision: (s.drafts[id]?.revision ?? 0) + 1 } },
    })),
  clearSubmitted: (id, revision) =>
    set((s) =>
      s.drafts[id]?.revision === revision
        ? { drafts: { ...s.drafts, [id]: { text: "", revision: revision + 1 } } }
        : {},
    ),
  reset: () => set({ drafts: {} }),
}));
