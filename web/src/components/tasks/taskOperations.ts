import { create } from "zustand";
import { api, type ApiRequestInit } from "../../api/client";
import { HpCommandError, type HpWork } from "../../api/types";
import { commandError } from "../../utils/commands";
import { newIdempotencyKey } from "../../utils/idempotency";
type Intent = {
  key: string;
  signature: string;
  request: ApiRequestInit;
  busy: boolean;
  uncertain: boolean;
  error?: string;
  notice?: string;
};
export const useTaskOperations = create<{ intents: Record<string, Intent> }>(() => ({
  intents: {},
}));
let epoch = 0;
const workLocks = new Map<string, object>();
const reservations = new Map<string, string>();
export function reserveTaskIntent(owner: string, signature: string, uncertain: boolean) {
  if (uncertain) reservations.set(owner, signature);
  else if (reservations.get(owner) === signature) reservations.delete(owner);
}
export function acquireTaskLock(owner: string, signature?: string) {
  if (
    workLocks.has(owner) ||
    (reservations.has(owner) && reservations.get(owner) !== signature) ||
    useTaskOperations.getState().intents[owner]?.busy ||
    useTaskOperations.getState().intents[owner]?.uncertain
  )
    throw new Error("该任务仍有操作正在处理或结果待确认，请先恢复原操作。");
  const token = {};
  workLocks.set(owner, token);
  return () => {
    if (workLocks.get(owner) === token) workLocks.delete(owner);
  };
}
export function resetTaskOperations() {
  epoch++;
  workLocks.clear();
  reservations.clear();
  useTaskOperations.setState({ intents: {} });
}
export async function taskCommand(
  owner: string,
  request: ApiRequestInit,
  recover: (work?: HpWork) => Promise<void>,
) {
  const state = useTaskOperations.getState();
  const previous = state.intents[owner];
  if (previous?.busy) return false;
  if (workLocks.has(owner) || reservations.has(owner)) {
    const blocked = previous ?? { key: "", signature: "", request, busy: false, uncertain: false };
    useTaskOperations.setState({
      intents: {
        ...state.intents,
        [owner]: { ...blocked, error: "此任务的资料操作正在处理或结果待确认，请先恢复资料操作。" },
      },
    });
    return false;
  }
  const signature = JSON.stringify({
    method: request.method,
    path: request.path,
    body: request.body,
  });
  // Uncertain retry uses the original payload and If-Match, even after an SSE snapshot changed.
  if (previous?.uncertain && signature !== previous.signature) {
    useTaskOperations.setState({
      intents: {
        ...state.intents,
        [owner]: { ...previous, error: "上次响应未知，请先重试原操作；不能用新参数替换原意图。" },
      },
    });
    return false;
  }
  let intent: Intent = previous?.uncertain
    ? { ...previous, busy: true, error: undefined }
    : {
        signature,
        request: { ...request, body: structuredClone(request.body) },
        key: newIdempotencyKey(),
        busy: true,
        uncertain: false,
      };
  const current = epoch;
  const valid = () => current === epoch && useTaskOperations.getState().intents[owner] === intent;
  const update = (patch: Partial<Intent>) => {
    if (valid()) {
      intent = { ...intent, ...patch };
      useTaskOperations.setState((s) => ({ intents: { ...s.intents, [owner]: intent } }));
    }
  };
  useTaskOperations.setState((s) => ({ intents: { ...s.intents, [owner]: intent } }));
  try {
    const result = await api.request<{ work?: HpWork }>({
      ...intent.request,
      idempotencyKey: intent.key,
    });
    if (!valid()) return false;
    update({ uncertain: false, notice: "操作已提交。" });
    try {
      await recover(result.work);
    } catch {
      update({ notice: "操作已提交，状态同步失败。请刷新状态。" });
    }
    return valid();
  } catch (error) {
    if (!valid()) return false;
    const uncertain = !(error instanceof HpCommandError) || error.status >= 500;
    update({
      uncertain,
      error:
        error instanceof HpCommandError && [403, 404].includes(error.status)
          ? "对象不可用。"
          : `${commandError(error)}${uncertain ? " 响应未知，再次提交会重试原操作。" : ""}`,
      notice: undefined,
    });
    if (error instanceof HpCommandError && [403, 404, 409].includes(error.status)) {
      try {
        await recover();
      } catch {
        /* Original error and draft remain visible. */
      }
    }
    return false;
  } finally {
    update({ busy: false });
  }
}
export function retryTaskCommand(owner: string, recover: (work?: HpWork) => Promise<void>) {
  const intent = useTaskOperations.getState().intents[owner];
  return intent?.uncertain ? taskCommand(owner, intent.request, recover) : Promise.resolve(false);
}
