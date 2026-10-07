import { create } from "zustand";
import { useShell } from "../../store/shell";
import { HpCommandError } from "../../api/types";
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
  selectRun: (runId: string) => void;
  loadTrace: () => Promise<void>;
  applyEvent: (runId: string, event: TraceEventUpdate) => void;
  selectNode: (nodeId: string) => void;
  loadModelInput: (snapshotId: string) => Promise<void>;
  clearModelInputs: () => void;
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
  return "unknown";
}

export function createTraceStore(
  api: HpApi = new HpApi(defaultApi),
  onOpen?: (runId: string | null) => void,
) {
  let generation = 0;
  let request = 0;
  let modelGeneration = 0;
  let buffered: TraceEventUpdate[] = [];
  let overflow = false;
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

    setOpen: (open) => {
      if (!open) {
        generation += 1;
        request += 1;
        buffered = [];
        set({ loading: false, modelInputs: {} });
      }
      set({ open });
      onOpen?.(open ? get().runId : null);
    },

    selectRun: (runId) => {
      generation += 1;
      request += 1;
      buffered = [];
      set({ open: false, runId: null });
      get().followRun(runId);
      set({ open: true });
    },
    followRun: (runId) => {
      if (get().open || get().runId === runId) return;
      generation += 1;
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
      const token = generation;
      const requestToken = ++request;
      const runId = get().runId;
      if (!runId) return;
      if (!get().loading) {
        buffered = [];
        overflow = false;
      }
      set({ loading: true, error: null });
      try {
        const tree = await api.getRunTrace(runId);
        if (token !== generation || requestToken !== request || get().runId !== runId) return;
        const normalized = flattenTree(tree);
        for (const [id, previous] of Object.entries(get().nodes)) {
          const incoming = normalized.nodes[id];
          if (
            incoming?.status === "running" &&
            ["completed", "failed", "cancelled"].includes(previous.status)
          )
            normalized.nodes[id] = {
              ...incoming,
              ...previous,
              metadata: { ...incoming.metadata, ...previous.metadata },
            };
        }
        const events = buffered;
        buffered = [];
        set((state) => ({
          run: tree.run,
          ...normalized,
          selectedNodeId:
            state.selectedNodeId &&
            (normalized.nodes[state.selectedNodeId] ||
              events.some((event) => event.nodeId === state.selectedNodeId))
              ? state.selectedNodeId
              : (normalized.rootIds[0] ?? null),
          loading: false,
          error: overflow ? "诊断事件过多，请重新同步。" : null,
        }));
        events.forEach((event) => get().applyEvent(runId, event));
        if (overflow) {
          set({ error: "诊断事件过多，正在重新同步。" });
          queueMicrotask(() => {
            if (token === generation && requestToken === request) void get().loadTrace();
          });
        }
      } catch (error) {
        if (token === generation && requestToken === request && get().runId === runId) {
          buffered = [];
          const denied = error instanceof HpCommandError && [403, 404].includes(error.status);
          if (denied) modelGeneration++;
          set({
            loading: false,
            error: denied ? "暂无可用诊断记录。" : "诊断待同步，请重试。",
            ...(denied ? { nodes: {}, rootIds: [], selectedNodeId: null, modelInputs: {} } : {}),
          });
        }
      }
    },

    applyEvent: (runId, event) => {
      if (get().runId !== runId) return;
      if (get().loading) {
        if (buffered.length < 512) buffered.push(event);
        else overflow = true;
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
                status:
                  existing && ["completed", "failed", "cancelled"].includes(existing.status)
                    ? existing.status
                    : "running",
                startedAt: event.occurredAt ?? existing?.startedAt ?? null,
                endedAt: existing?.endedAt ?? null,
                durationMs: existing?.durationMs ?? null,
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

    clearModelInputs: () => {
      modelGeneration++;
      set({ modelInputs: {} });
    },
    loadModelInput: async (snapshotId) => {
      const token = generation;
      const modelToken = modelGeneration;
      const current = get().modelInputs[snapshotId];
      if (current?.status === "loading" || current?.status === "loaded") return;
      set((state) => ({
        modelInputs: { ...state.modelInputs, [snapshotId]: { status: "loading" } },
      }));
      try {
        const detail = await api.getModelInput(snapshotId);
        if (token !== generation || modelToken !== modelGeneration) return;
        set((state) => ({
          modelInputs: {
            ...state.modelInputs,
            [snapshotId]: { status: "loaded", detail },
          },
        }));
      } catch (error) {
        if (token !== generation || modelToken !== modelGeneration) return;
        const unavailable =
          typeof error === "object" &&
          error !== null &&
          "code" in error &&
          (error.code === "model_input_unavailable" ||
            (error instanceof HpCommandError && [403, 404].includes(error.status)));
        set((state) => ({
          modelInputs: {
            ...state.modelInputs,
            [snapshotId]: { status: unavailable ? "unavailable" : "error" },
          },
        }));
      }
    },

    reset: () => {
      generation += 1;
      request += 1;
      buffered = [];
      set({
        open: false,
        runId: null,
        run: null,
        nodes: {},
        rootIds: [],
        selectedNodeId: null,
        loading: false,
        error: null,
        modelInputs: {},
      });
    },
  }));
}

export const useTraceStore = createTraceStore(new HpApi(defaultApi), (runId) => {
  if (runId) useShell.getState().openInspector({ kind: "run", objectId: runId });
});
