import { create } from "zustand";
import { api } from "../api/client";
import { HpApi } from "../api/resources";
import { HpCommandError, type HpFileApproval, type HpRunSnapshot } from "../api/types";
import { useWorkbench } from "./workbench";

export const runApi = new HpApi(api);
type Intent = {
  runId: string;
  decision: "approve" | "reject";
  key: string;
  state: "sending" | "unknown";
  message?: string;
  canReplay?: boolean;
  conflict?: boolean;
};
interface State {
  runId: string | null;
  snapshot: HpRunSnapshot | null;
  loading: boolean;
  error: string | null;
  unavailable: boolean;
  approvals: HpFileApproval[];
  approvalError: string | null;
  approvalLoading: boolean;
  approvalLoaded: boolean;
  intents: Record<string, Intent>;
  select: (id: string) => void;
  load: () => Promise<void>;
  loadApprovals: () => Promise<void>;
  decide: (
    approval: HpFileApproval,
    decision: "approve" | "reject",
    replay?: boolean,
  ) => Promise<void>;
  reset: () => void;
}
export function createRunInspector(resources: HpApi = runApi) {
  let generation = 0,
    request = 0,
    approvalRequest = 0;
  let snapshotController: AbortController | null = null,
    approvalController: AbortController | null = null;
  return create<State>((set, get) => ({
    runId: null,
    snapshot: null,
    loading: false,
    error: null,
    unavailable: false,
    approvals: [],
    approvalError: null,
    approvalLoading: false,
    approvalLoaded: false,
    intents: {},
    select(id) {
      snapshotController?.abort();
      approvalController?.abort();
      generation++;
      request++;
      approvalRequest++;
      set({
        runId: id,
        snapshot: null,
        loading: false,
        error: null,
        unavailable: false,
        approvals: [],
        approvalError: null,
        approvalLoading: false,
        approvalLoaded: false,
      });
    },
    async load() {
      const id = get().runId;
      if (!id) return;
      const token = ++request,
        boundary = generation;
      const active = useWorkbench.getState();
      if (active.activeRun?.run_id === id) {
        const message = active.messages.find(
          (m) => m.role === "assistant" && m.produced_by_run_id === id,
        );
        if (message) {
          set({
            snapshot: { source_kind: "chat", run: active.activeRun, assistant_message: message },
            error: null,
            unavailable: false,
            loading: false,
          });
          return;
        }
      }
      snapshotController?.abort();
      snapshotController = new AbortController();
      set({ loading: true });
      try {
        const snapshot = await resources.getRun(id, snapshotController.signal);
        if (token !== request || boundary !== generation) return;
        set({ snapshot, loading: false, error: null, unavailable: false });
      } catch (e) {
        if (token !== request || boundary !== generation) return;
        const unavailable = e instanceof HpCommandError && [403, 404].includes(e.status);
        set({
          loading: false,
          unavailable,
          error: unavailable ? "对象不可用。" : "待同步，请重试。",
          ...(unavailable ? { snapshot: null, approvals: [] } : {}),
        });
      }
    },
    async loadApprovals() {
      const id = get().runId;
      if (!id || get().approvalLoading) return;
      approvalController = new AbortController();
      set({ approvalLoading: true });
      const token = ++approvalRequest,
        boundary = generation;
      try {
        const { approvals } = await resources.listRunFileApprovals(id, approvalController.signal);
        if (token !== approvalRequest || boundary !== generation) return;
        const intents = { ...get().intents };
        for (const approval of approvals) {
          const intent = intents[approval.approval_id];
          if (!intent || intent.state === "sending") continue;
          if (approval.status !== "pending") delete intents[approval.approval_id];
          else
            intents[approval.approval_id] = {
              ...intent,
              canReplay:
                !intent.conflict &&
                (!approval.expires_at || Date.parse(approval.expires_at) > Date.now()),
            };
        }
        set({
          approvals,
          intents,
          approvalError: null,
          approvalLoading: false,
          approvalLoaded: true,
        });
      } catch (e) {
        if (token !== approvalRequest || boundary !== generation) return;
        set({
          approvalError: "审批暂时无法同步。",
          approvalLoading: false,
          ...(e instanceof HpCommandError && [403, 404].includes(e.status)
            ? { approvals: [] }
            : {}),
        });
      }
    },
    async decide(approval, decision, replay = false) {
      const id = approval.approval_id,
        prior = get().intents[id],
        known = get().approvals.find((a) => a.approval_id === id);
      if (
        get().runId !== approval.run_id ||
        (known && known.status !== "pending") ||
        approval.status !== "pending" ||
        (approval.expires_at && Date.parse(approval.expires_at) <= Date.now())
      )
        return;
      if (
        prior &&
        (!replay || prior.state !== "unknown" || !prior.canReplay || prior.decision !== decision)
      )
        return;
      const intent: Intent = {
        runId: approval.run_id,
        decision,
        key: prior?.key ?? crypto.randomUUID(),
        state: "sending",
      };
      const boundary = generation;
      // Selection changes invalidate view requests; command identity survives them.
      const sessionIntents = get().intents;
      set({ intents: { ...sessionIntents, [id]: intent } });
      try {
        const { approval: updated } = await resources.decideFileApproval(id, decision, intent.key);
        if (get().intents[id] !== intent) return;
        const intents = { ...get().intents };
        delete intents[id];
        set({ intents });
        if (get().runId === intent.runId) {
          // A read started before this command cannot restore pending controls.
          approvalController?.abort();
          approvalRequest++;
          set({
            approvalLoading: false,
            approvals: get().approvals.map((a) => (a.approval_id === id ? updated : a)),
            approvalError: decision === "approve" ? "已允许，等待执行状态更新。" : "已拒绝。",
          });
          await get().loadApprovals();
          await get().load();
        }
      } catch (e) {
        if (get().intents[id] !== intent) return;
        const conflict = e instanceof HpCommandError && e.status === 409;
        set({
          intents: {
            ...get().intents,
            [id]: {
              ...intent,
              state: "unknown",
              canReplay: false,
              conflict,
              message: conflict
                ? "审批已处理或过期，请同步核对。"
                : "结果尚未确认，请先同步核对；仍待审批时可确认原决策。",
            },
          },
        });
        if (boundary === generation || get().runId === intent.runId) await get().loadApprovals();
      }
    },
    reset() {
      snapshotController?.abort();
      approvalController?.abort();
      generation++;
      request++;
      approvalRequest++;
      set({
        runId: null,
        snapshot: null,
        loading: false,
        unavailable: false,
        error: null,
        approvals: [],
        approvalError: null,
        approvalLoading: false,
        approvalLoaded: false,
        intents: {},
      });
    },
  }));
}
export const useRunInspector = createRunInspector();
