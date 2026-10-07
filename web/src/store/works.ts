import { create } from "zustand";
import { api, type ApiRequestInit } from "../api/client";
import { HpCommandError, type HpWork, type HpWorkEvent } from "../api/types";
import { taskCommand, resetTaskOperations } from "../components/tasks/taskOperations";
interface WorkState {
  generation: number;
  now: number;
  items: HpWork[];
  events: Record<string, HpWorkEvent[]>;
  cursors: Record<string, number>;
  busy: string | null;
  error: string | null;
  detailErrors: Record<string, string | undefined>;
  loadState: "initial" | "scanning" | "complete" | "partial" | "error";
  nextBefore: string | null;
  scanPaused: boolean;
  lastCompletedAt: number | null;
  load: (resume?: boolean) => Promise<void>;
  cancelScan: (pause?: boolean) => void;
  upsert: (work: HpWork) => void;
  refresh: (id: string) => Promise<void>;
  receive: (event: HpWorkEvent) => void;
  command: (work: HpWork, request: ApiRequestInit) => Promise<boolean>;
  control: (
    work: HpWork,
    action: "pause" | "resume" | "stop" | "advance" | "accept-result",
    artifactVersionId?: string,
  ) => Promise<boolean>;
  link: (work: HpWork, conversationId: string) => Promise<boolean>;
  increaseBudget: (work: HpWork, limits?: Record<string, number>) => Promise<boolean>;
  resolveDelivery: (
    work: HpWork,
    deliveryId: string,
    outcome: "accepted" | "not_sent" | "retry_accepting_duplicate_risk",
  ) => Promise<boolean>;
  reset: () => void;
}
let scanId = 0;
let scanPromise: Promise<void> | null = null;
let scanAbort: AbortController | null = null;
const refreshes = new Map<string, Promise<void>>();
const requestControllers = new Set<AbortController>();
export const useWorks = create<WorkState>((set, get) => ({
  generation: 0,
  now: Date.now(),
  items: [],
  events: {},
  cursors: {},
  busy: null,
  error: null,
  detailErrors: {},
  loadState: "initial",
  nextBefore: null,
  scanPaused: false,
  lastCompletedAt: null,
  upsert(work) {
    const old = get().items.find((w) => w.work_id === work.work_id);
    if (old && old.row_version > work.row_version) return;
    set({
      items: old
        ? get().items.map((w) => (w.work_id === work.work_id ? work : w))
        : [...get().items, work],
      detailErrors: { ...get().detailErrors, [work.work_id]: undefined },
    });
  },
  load(resume = false) {
    if (scanPromise) return scanPromise;
    const generation = get().generation,
      token = ++scanId;
    const abort = new AbortController();
    scanAbort = abort;
    set({ loadState: "scanning", error: null, scanPaused: false });
    const promise = (async () => {
      let before = resume ? get().nextBefore : null;
      const cursors = new Set<string>();
      const seen = new Set<string>();
      try {
        do {
          const result = await api.request<{ items: HpWork[]; next_before?: string | null }>({
            method: "GET",
            path: `/api/v1/works${before ? `?before=${encodeURIComponent(before)}` : ""}`,
            signal: abort.signal,
          });
          if (generation !== get().generation || token !== scanId) return;
          const newItems = result.items.filter((w) => !seen.has(w.work_id));
          result.items.forEach((w) => {
            seen.add(w.work_id);
            get().upsert(w);
          });
          const next = result.next_before ?? null;
          if (next && (cursors.has(next) || next === before || !newItems.length)) {
            set({
              loadState: "partial",
              nextBefore: null,
              scanPaused: false,
              error: "分页游标未前进，已停止扫描。请重新同步。",
            });
            return;
          }
          if (next) cursors.add(next);
          set({ nextBefore: next });
          before = next;
        } while (before);
        set({ loadState: "complete", lastCompletedAt: Date.now(), error: null });
      } catch {
        if (generation === get().generation && token === scanId)
          set({
            loadState: get().items.length ? "partial" : "error",
            error: "任务同步未完成，已加载内容仍可查看。请重试。",
          });
      }
    })();
    scanPromise = promise;
    void promise.finally(() => {
      if (scanPromise === promise) {
        scanPromise = null;
        scanAbort = null;
      }
    });
    return promise;
  },
  cancelScan(pause = false) {
    scanId++;
    scanAbort?.abort();
    scanAbort = null;
    scanPromise = null;
    if (get().loadState === "scanning")
      set({ loadState: "partial", ...(pause ? { scanPaused: true } : {}) });
  },
  refresh(id) {
    const existing = refreshes.get(id);
    if (existing) return existing;
    const generation = get().generation;
    const abort = new AbortController();
    requestControllers.add(abort);
    const promise = (async () => {
      try {
        const result = await api.request<{ work: HpWork }>({
          method: "GET",
          path: `/api/v1/works/${encodeURIComponent(id)}`,
          signal: abort.signal,
        });
        if (generation === get().generation) get().upsert(result.work);
      } catch (error) {
        if (generation === get().generation) {
          const unavailable = error instanceof HpCommandError && [403, 404].includes(error.status);
          set({
            ...(unavailable
              ? {
                  items: get().items.filter((w) => w.work_id !== id),
                  events: { ...get().events, [id]: [] },
                }
              : {}),
            detailErrors: {
              ...get().detailErrors,
              [id]: unavailable ? "对象不可用。" : "状态待同步，请重试。",
            },
          });
        }
        throw error;
      } finally {
        requestControllers.delete(abort);
      }
    })();
    refreshes.set(id, promise);
    void promise.then(
      () => {
        if (refreshes.get(id) === promise) refreshes.delete(id);
      },
      () => {
        if (refreshes.get(id) === promise) refreshes.delete(id);
      },
    );
    return promise;
  },
  receive(event) {
    if (event.event_seq <= (get().cursors[event.work_id] ?? 0)) return;
    set({
      cursors: { ...get().cursors, [event.work_id]: event.event_seq },
      events: {
        ...get().events,
        [event.work_id]: [...(get().events[event.work_id] ?? []), event].slice(-100),
      },
    });
    void get()
      .refresh(event.work_id)
      .catch(() => {});
  },
  command(work, request) {
    return taskCommand(
      work.work_id,
      {
        ...request,
        headers: { "If-Match": `"work-${work.work_id}-v${work.row_version}"`, ...request.headers },
      },
      async (snapshot) => {
        if (snapshot) get().upsert(snapshot);
        else await get().refresh(work.work_id);
      },
    );
  },
  control(work, action, artifactVersionId) {
    return get().command(work, {
      method: "POST",
      path: `/api/v1/works/${work.work_id}/${action}`,
      body:
        action === "accept-result"
          ? {
              requirement_revision: work.current_requirement_revision,
              artifact_version_id: artifactVersionId,
            }
          : {},
    });
  },
  link(work, conversationId) {
    return get().command(work, {
      method: "PUT",
      path: `/api/v1/works/${work.work_id}/conversations/${encodeURIComponent(conversationId)}`,
      body: {},
    });
  },
  increaseBudget(work, limits) {
    if (
      !work.budget ||
      !limits ||
      !Object.keys(limits).length ||
      Object.entries(limits).some(
        ([key, value]) =>
          !(key in work.budget!.limits) ||
          !Number.isFinite(value) ||
          value <= work.budget!.limits[key]!,
      )
    )
      return Promise.resolve(false);
    return get().command(work, {
      method: "POST",
      path: `/api/v1/works/${work.work_id}/budget`,
      body: { budget_version: work.budget.version, limits },
    });
  },
  resolveDelivery(work, deliveryId, outcome) {
    return get().command(work, {
      method: "POST",
      path: `/api/v1/works/${work.work_id}/deliveries/${encodeURIComponent(deliveryId)}/resolve`,
      body: { outcome },
    });
  },
  reset() {
    get().cancelScan();
    requestControllers.forEach((c) => c.abort());
    requestControllers.clear();
    refreshes.clear();
    resetTaskOperations();
    set({
      generation: get().generation + 1,
      items: [],
      events: {},
      cursors: {},
      busy: null,
      error: null,
      detailErrors: {},
      loadState: "initial",
      nextBefore: null,
      scanPaused: false,
      lastCompletedAt: null,
    });
  },
}));
