import { create } from "zustand";
import { HpCommandError } from "../../api/types";
import { useWorkspace, workspaceApi } from "../../store/workspace";
import { commandError } from "../../utils/commands";
import { newIdempotencyKey } from "../../utils/idempotency";

export type VersionPayload = {
  nodeId: string;
  runId: string;
  fileId: string;
  fileName: string;
  revision: number;
  sha256: string;
};
export type VersionOperation = VersionPayload & {
  generation: number;
  key: string;
  state: "pending" | "unknown" | "rejected" | "succeeded";
  busy: boolean;
  error?: string;
  conflict?: boolean;
};
export const useVersionOperations = create<{ operations: Record<string, VersionOperation> }>(
  () => ({
    operations: {},
  }),
);
export function startVersionUpdate(payload: VersionPayload) {
  const generation = useWorkspace.getState().generation;
  const existing = useVersionOperations.getState().operations[payload.nodeId];
  if (
    existing?.generation === generation &&
    (existing.busy || existing.state === "unknown" || existing.state === "pending")
  )
    return; // An unresolved command must be resumed with its frozen payload.
  useVersionOperations.setState((s) => ({
    operations: {
      ...s.operations,
      [payload.nodeId]: {
        ...payload,
        generation,
        key: newIdempotencyKey(),
        state: "pending",
        busy: false,
      },
    },
  }));
  void resumeVersionUpdate(payload.nodeId);
}
export async function resumeVersionUpdate(nodeId: string) {
  const original = useVersionOperations.getState().operations[nodeId];
  if (
    !original ||
    original.generation !== useWorkspace.getState().generation ||
    original.busy ||
    !["pending", "unknown"].includes(original.state)
  )
    return;
  const generation = original.generation;
  const current = () =>
    generation === useWorkspace.getState().generation &&
    useVersionOperations.getState().operations[nodeId]?.key === original.key;
  const update = (patch: Partial<VersionOperation>) => {
    if (current())
      useVersionOperations.setState((s) => ({
        operations: { ...s.operations, [nodeId]: { ...s.operations[nodeId]!, ...patch } },
      }));
  };
  update({ busy: true, error: undefined });
  try {
    await workspaceApi.updateWorkspaceFile(
      original.nodeId,
      original.runId,
      original.fileId,
      original.revision,
      original.sha256,
      original.key,
    );
    if (!current()) return;
    update({ state: "succeeded", conflict: false });
    useWorkspace.getState().invalidate();
  } catch (error) {
    const rejected =
      error instanceof HpCommandError &&
      error.status >= 400 &&
      error.status < 500 &&
      error.status !== 408;
    update({
      state: rejected ? "rejected" : "unknown",
      error: commandError(error),
      conflict: error instanceof HpCommandError && error.code === "workspace_version_conflict",
    });
  } finally {
    update({ busy: false });
  }
}
export function clearRejectedVersionUpdate(nodeId: string) {
  const operation = useVersionOperations.getState().operations[nodeId];
  if (!operation || operation.busy || operation.state !== "rejected") return;
  useVersionOperations.setState((s) => {
    const operations = { ...s.operations };
    delete operations[nodeId];
    return { operations };
  });
}
export function resetVersionOperations() {
  useVersionOperations.setState({ operations: {} });
}
