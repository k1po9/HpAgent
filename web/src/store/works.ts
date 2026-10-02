import { create } from "zustand";
import { api } from "../api/client";
import type { HpWork, HpWorkEvent } from "../api/types";
import { newIdempotencyKey } from "../utils/idempotency";

interface WorkState {
  items: HpWork[];
  events: Record<string, HpWorkEvent[]>;
  cursors: Record<string, number>;
  busy: string | null;
  error: string | null;
  load: () => Promise<void>;
  refresh: (id: string) => Promise<void>;
  receive: (event: HpWorkEvent) => void;
  control: (
    work: HpWork,
    action: "pause" | "resume" | "stop" | "advance" | "accept-result",
    artifactVersionId?: string,
  ) => Promise<void>;
  link: (work: HpWork, conversationId: string) => Promise<void>;
  increaseBudget: (work: HpWork) => Promise<void>;
  resolveDelivery: (
    work: HpWork,
    deliveryId: string,
    outcome: "accepted" | "not_sent" | "retry_accepting_duplicate_risk",
  ) => Promise<void>;
  reset: () => void;
}

let generation = 0;
function latest(previous: HpWork | undefined, incoming: HpWork): HpWork {
  return previous && previous.row_version > incoming.row_version ? previous : incoming;
}
export const useWorks = create<WorkState>((set, get) => ({
  items: [],
  events: {},
  cursors: {},
  busy: null,
  error: null,
  async load() {
    const current = generation;
    try {
      const result = await api.request<{ items: HpWork[] }>({
        method: "GET",
        path: "/api/v1/works",
      });
      if (current === generation)
        set({
          items: result.items.map((w) =>
            latest(
              get().items.find((old) => old.work_id === w.work_id),
              w,
            ),
          ),
          error: null,
        });
    } catch {
      if (current === generation) set({ error: "无法恢复工作状态，请稍后重试。" });
    }
  },
  async refresh(id) {
    const current = generation;
    const result = await api.request<{ work: HpWork }>({
      method: "GET",
      path: `/api/v1/works/${id}`,
    });
    if (current === generation)
      set({ items: get().items.map((w) => (w.work_id === id ? latest(w, result.work) : w)) });
  },
  receive(event) {
    if (!get().items.some((w) => w.work_id === event.work_id)) return;
    const current = generation;
    if (event.event_seq <= (get().cursors[event.work_id] ?? 0)) return;
    set({
      cursors: { ...get().cursors, [event.work_id]: event.event_seq },
      events: {
        ...get().events,
        [event.work_id]: [...(get().events[event.work_id] ?? []), event].slice(-20),
      },
    });
    void get()
      .refresh(event.work_id)
      .catch(() => {
        if (current === generation) set({ error: "工作事件已收到，状态刷新失败。" });
      });
  },
  async control(work, action, artifactVersionId) {
    const current = generation;
    set({ busy: work.work_id, error: null });
    try {
      const result = await api.request<{ work: HpWork }>({
        method: "POST",
        path: `/api/v1/works/${work.work_id}/${action}`,
        body:
          action === "accept-result"
            ? {
                requirement_revision: work.current_requirement_revision,
                artifact_version_id: artifactVersionId,
              }
            : {},
        idempotencyKey: newIdempotencyKey(),
        headers: { "If-Match": `"work-${work.work_id}-v${work.row_version}"` },
      });
      if (current === generation)
        set({
          items: get().items.map((w) => (w.work_id === work.work_id ? latest(w, result.work) : w)),
        });
    } catch {
      if (current !== generation) return;
      set({ error: "操作未完成，可能是版本已更新；请查看最新状态后重试。" });
      await get().load();
    } finally {
      if (current === generation) set({ busy: null });
    }
  },
  async link(work, conversationId) {
    const current = generation;
    try {
      await api.request({
        method: "PUT",
        path: `/api/v1/works/${work.work_id}/conversations/${conversationId}`,
        body: {},
        idempotencyKey: newIdempotencyKey(),
        headers: { "If-Match": `"work-${work.work_id}-v${work.row_version}"` },
      });
      if (current === generation) await get().refresh(work.work_id);
    } catch {
      if (current === generation) set({ error: "续接失败，请刷新后重试。" });
    }
  },
  async increaseBudget(work) {
    if (!work.budget) return;
    const current = generation;
    try {
      await api.request({
        method: "POST",
        path: `/api/v1/works/${work.work_id}/budget`,
        body: {
          budget_version: work.budget.version,
          limits: Object.fromEntries(
            Object.entries(work.budget.limits).map(([k, v]) => [k, v * 2]),
          ),
        },
        idempotencyKey: newIdempotencyKey(),
        headers: { "If-Match": `"work-${work.work_id}-v${work.row_version}"` },
      });
      if (current === generation) await get().refresh(work.work_id);
    } catch {
      if (current === generation) set({ error: "增额失败，请刷新最新额度后重试。" });
    }
  },
  async resolveDelivery(work, deliveryId, outcome) {
    const current = generation;
    try {
      await api.request({
        method: "POST",
        path: `/api/v1/works/${work.work_id}/deliveries/${deliveryId}/resolve`,
        body: { outcome },
        idempotencyKey: newIdempotencyKey(),
        headers: { "If-Match": `"work-${work.work_id}-v${work.row_version}"` },
      });
      if (current === generation) await get().refresh(work.work_id);
    } catch {
      if (current === generation) set({ error: "投递决定未提交，请刷新后重试。" });
    }
  },
  reset() {
    generation += 1;
    set({ items: [], events: {}, cursors: {}, busy: null, error: null });
  },
}));
