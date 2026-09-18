import { create } from "zustand";
import { api as defaultApi } from "../../api/client";
import { HpApi } from "../../api/resources";
import type { HpTraceEventNode, HpTraceRun, HpTraceStatus, HpTraceTree } from "../../api/types";
import type { HpModelInputDetail } from "../../api/types";
import type { TraceEventUpdate } from "../../sse/runFeed";

export interface TraceNode {
  id: string;
  parentId: string | null;
  name: string;
  type: string;
  status: HpTraceStatus;
  startedAt: string | null;
  endedAt: string | null;
  durationMs: number | null;
  metadata: Record<string, unknown>;
}

export interface TraceState {
  open: boolean;
  runId: string | null;
  run: HpTraceRun | null;
  nodes: Record<string, TraceNode>;
  rootIds: string[];
  selectedNodeId: string | null;
  loading: boolean;
  error: string | null;
  modelInputs: Record<string, ModelInputState>;

  setOpen: (open: boolean) => void;
  followRun: (runId: string | null) => void;
  loadTrace: () => Promise<void>;
  applyEvent: (runId: string, event: TraceEventUpdate) => void;
  selectNode: (nodeId: string) => void;
  loadModelInput: (snapshotId: string) => Promise<void>;
  reset: () => void;
}

export interface ModelInputState {
  status: "loading" | "loaded" | "unavailable" | "error";
  detail?: HpModelInputDetail;
}

function flattenTree(tree: HpTraceTree): {
  nodes: Record<string, TraceNode>;
  rootIds: string[];
} {
  const nodes: Record<string, TraceNode> = {};
  const visit = (node: HpTraceEventNode): void => {
    const event = node.event;
    nodes[event.trace_event_id] = {
      id: event.trace_event_id,
      parentId: event.parent_event_id,
      name: event.name,
      type: event.event_type,
      status: event.status,
      startedAt: event.started_at,
      endedAt: event.ended_at,
      durationMs: event.duration_ms,
      metadata: event.metadata,
    };
    node.children.forEach(visit);
  };
  tree.roots.forEach(visit);
  return { nodes, rootIds: tree.roots.map((node) => node.event.trace_event_id) };
}

function terminalStatus(value: string | null): HpTraceStatus {
  if (value === "failed" || value === "cancelled" || value === "completed") return value;
  return "completed";
}

export function createTraceStore(api: HpApi = new HpApi(defaultApi)) {
  return create<TraceState>()((set, get) => ({
    open: false,
    runId: null,
    run: null,
    nodes: {},
    rootIds: [],
    selectedNodeId: null,
    loading: false,
    error: null,
    modelInputs: {},

    setOpen: (open) => set({ open }),

    followRun: (runId) => {
      if (get().runId === runId) return;
      set({
        runId,
        run: null,
        nodes: {},
        rootIds: [],
        selectedNodeId: null,
        loading: false,
        error: null,
        modelInputs: {},
      });
    },

    loadTrace: async () => {
      const runId = get().runId;
      if (!runId || get().loading) return;
      set({ loading: true, error: null });
      try {
        const tree = await api.getRunTrace(runId);
        if (get().runId !== runId) return;
        const normalized = flattenTree(tree);
        set((state) => ({
          run: tree.run,
          ...normalized,
          selectedNodeId:
            state.selectedNodeId && normalized.nodes[state.selectedNodeId]
              ? state.selectedNodeId
              : (normalized.rootIds[0] ?? null),
          loading: false,
        }));
      } catch {
        if (get().runId === runId) {
          set({ loading: false, error: "Trace 暂不可用，实时事件仍会继续显示。" });
        }
      }
    },

    applyEvent: (runId, event) => {
      if (get().runId !== runId) {
        get().followRun(runId);
      }
      set((state) => {
        const existing = state.nodes[event.nodeId];
        const node: TraceNode =
          event.action === "start"
            ? {
                id: event.nodeId,
                parentId: event.parentId,
                name: event.name ?? existing?.name ?? "TraceEvent",
                type: event.nodeType ?? existing?.type ?? "event",
                status: "running",
                startedAt: event.occurredAt ?? existing?.startedAt ?? null,
                endedAt: null,
                durationMs: null,
                metadata: { ...(existing?.metadata ?? {}), ...event.metadata },
              }
            : {
                id: event.nodeId,
                parentId: existing?.parentId ?? event.parentId,
                name: existing?.name ?? event.name ?? "TraceEvent",
                type: existing?.type ?? event.nodeType ?? "event",
                status: terminalStatus(event.status),
                startedAt: existing?.startedAt ?? null,
                endedAt: event.occurredAt,
                durationMs: event.durationMs ?? existing?.durationMs ?? null,
                metadata: { ...(existing?.metadata ?? {}), ...event.metadata },
              };
        const rootIds =
          node.parentId === null && !state.rootIds.includes(node.id)
            ? [...state.rootIds, node.id]
            : state.rootIds;
        return {
          nodes: { ...state.nodes, [node.id]: node },
          rootIds,
          selectedNodeId: state.selectedNodeId ?? node.id,
          error: null,
        };
      });
    },

    selectNode: (selectedNodeId) => set({ selectedNodeId }),

    loadModelInput: async (snapshotId) => {
      const current = get().modelInputs[snapshotId];
      if (current?.status === "loading" || current?.status === "loaded") return;
      set((state) => ({
        modelInputs: { ...state.modelInputs, [snapshotId]: { status: "loading" } },
      }));
      try {
        const detail = await api.getModelInput(snapshotId);
        set((state) => ({
          modelInputs: {
            ...state.modelInputs,
            [snapshotId]: { status: "loaded", detail },
          },
        }));
      } catch (error) {
        const unavailable =
          typeof error === "object" &&
          error !== null &&
          "code" in error &&
          error.code === "model_input_unavailable";
        set((state) => ({
          modelInputs: {
            ...state.modelInputs,
            [snapshotId]: { status: unavailable ? "unavailable" : "error" },
          },
        }));
      }
    },

    reset: () =>
      set({
        runId: null,
        run: null,
        nodes: {},
        rootIds: [],
        selectedNodeId: null,
        loading: false,
        error: null,
        modelInputs: {},
      }),
  }));
}

export const useTraceStore = createTraceStore();
