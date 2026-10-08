import { acquireTaskLock, reserveTaskIntent } from "../tasks/taskOperations";
import { create } from "zustand";
import { api } from "../../api/client";
import { HpCommandError, type HpWork } from "../../api/types";
import { useWorkspace, workspaceApi } from "../../store/workspace";
import { useWorkbench } from "../../store/workbench";
import { useWorks } from "../../store/works";
import { useTraceStore } from "../trace/traceStore";
import { useRunInspector } from "../../store/runInspector";
import { newIdempotencyKey } from "../../utils/idempotency";
import { resetVersionOperations } from "./versionOperations";
import { commandError } from "../../utils/commands";
export type Subject = { kind: "conversation" | "work"; id: string; title: string };
export type Grant = Awaited<
  ReturnType<typeof workspaceApi.listConversationResources>
>["grants"][number];
export type Permission = Grant["operation"];
const workIntents = new Map<string, { version: number; key: string }>();
const subjectLocks = new Set<string>();
const workQueues = new Map<string, Promise<unknown>>();
export async function listGrants(subject: Subject): Promise<{
  grants: Grant[];
  attachments?: Array<{ file_id: string; name: string; available: boolean }>;
}> {
  return subject.kind === "conversation"
    ? workspaceApi.listConversationResources(subject.id)
    : api.request<{ grants: Grant[] }>({
        method: "GET",
        path: `/api/v1/works/${encodeURIComponent(subject.id)}/resources`,
      });
}
export async function workCommand(
  id: string,
  suffix: string,
  body: unknown,
  method: "POST" | "DELETE" = "POST",
) {
  const generation = useWorkspace.getState().generation;
  const previous = workQueues.get(id) ?? Promise.resolve();
  const command = previous
    .catch(() => {})
    .then(() => {
      if (generation !== useWorkspace.getState().generation)
        throw new DOMException("Session changed", "AbortError");
      const release = acquireTaskLock(id, JSON.stringify({ id, suffix, body, method }));
      return runWorkCommand(id, suffix, body, method).finally(release);
    });
  workQueues.set(id, command);
  try {
    return await command;
  } finally {
    if (workQueues.get(id) === command) workQueues.delete(id);
  }
}

async function runWorkCommand(
  id: string,
  suffix: string,
  body: unknown,
  method: "POST" | "DELETE" = "POST",
) {
  const generation = useWorkspace.getState().generation;
  const fingerprint = JSON.stringify({ id, suffix, body, method });
  let intent = workIntents.get(fingerprint);
  if (!intent) {
    const { work } = await api.request<{ work: HpWork }>({
      method: "GET",
      path: `/api/v1/works/${encodeURIComponent(id)}`,
    });
    if (generation !== useWorkspace.getState().generation)
      throw new DOMException("Session changed", "AbortError");
    intent = { version: work.row_version, key: newIdempotencyKey() };
    workIntents.set(fingerprint, intent);
  }
  try {
    const result = await api.request<{ work: HpWork; affected_run_ids?: string[] }>({
      method,
      path: `/api/v1/works/${encodeURIComponent(id)}/${suffix}`,
      body,
      idempotencyKey: intent.key,
      headers: { "If-Match": `"work-${id}-v${intent.version}"` },
    });
    if (generation !== useWorkspace.getState().generation)
      throw new DOMException("Session changed", "AbortError");
    workIntents.delete(fingerprint);
    reserveTaskIntent(id, fingerprint, false);
    if (result.work) useWorks.getState().upsert(result.work);
    else
      void useWorks
        .getState()
        .refresh(id)
        .catch(() => {});
    observeRevocation(result.affected_run_ids ?? []);
    return result;
  } catch (error) {
    // A definitive conflict is a rejected command. Retry explicitly re-reads its version.
    const uncertain = !(error instanceof HpCommandError) || error.status >= 500;
    if (generation === useWorkspace.getState().generation)
      reserveTaskIntent(id, fingerprint, uncertain);
    if (!uncertain) workIntents.delete(fingerprint);
    throw error;
  }
}
export async function grantMissing(
  subject: Subject,
  nodeId: string,
  operations: Permission[],
  recursive: boolean,
) {
  const lock = `${subject.kind}:${subject.id}`;
  if (subjectLocks.has(lock)) throw new Error("该主体的权限操作正在处理中。");
  subjectLocks.add(lock);
  const generation = useWorkspace.getState().generation;
  try {
    const page = await listGrants(subject);
    if (generation !== useWorkspace.getState().generation)
      throw new DOMException("Session changed", "AbortError");
    const missing = operations.filter(
      (op) =>
        !page.grants.some(
          (g) => g.node_id === nodeId && g.operation === op && g.recursive === recursive,
        ),
    );
    if (missing.length) {
      if (subject.kind === "conversation")
        await workspaceApi.grantConversationResource(subject.id, nodeId, missing, recursive);
      else
        await workCommand(subject.id, "resources", {
          node_id: nodeId,
          operations: missing,
          recursive,
        });
    }
    if (generation !== useWorkspace.getState().generation)
      throw new DOMException("Session changed", "AbortError");
    permissionsChanged();
  } finally {
    if (generation === useWorkspace.getState().generation) subjectLocks.delete(lock);
  }
}
export async function revokeRules(subject: Subject, rules: Grant[]) {
  const lock = `${subject.kind}:${subject.id}`;
  if (subjectLocks.has(lock)) throw new Error("该主体的权限操作正在处理中。");
  subjectLocks.add(lock);
  const generation = useWorkspace.getState().generation;
  try {
    const current = await listGrants(subject);
    const wanted = new Set(rules.map((g) => g.grant_id));
    for (const grant of current.grants.filter((g) => wanted.has(g.grant_id))) {
      if (generation !== useWorkspace.getState().generation)
        throw new DOMException("Session changed", "AbortError");
      try {
        if (subject.kind === "conversation") {
          const result = await workspaceApi.revokeConversationResource(subject.id, grant.grant_id);
          observeRevocation(result.affected_runs.map((r) => r.run_id));
        } else
          await workCommand(
            subject.id,
            `resources/${encodeURIComponent(grant.grant_id)}`,
            undefined,
            "DELETE",
          );
      } catch {
        /* Read-back below determines partial/unknown success. */
      }
    }
    const page = await listGrants(subject);
    if (generation !== useWorkspace.getState().generation)
      throw new DOMException("Session changed", "AbortError");
    permissionsChanged();
    return page.grants.filter((g) => wanted.has(g.grant_id));
  } finally {
    if (generation === useWorkspace.getState().generation) subjectLocks.delete(lock);
  }
}
export function permissionsChanged(notifyChips = true) {
  if (notifyChips) window.dispatchEvent(new Event("workspace-permissions-changed"));
  invalidateResourceViews();
  void useWorkbench.getState().refreshActiveRun();
}
export function invalidateResourceViews() {
  useWorkspace.getState().invalidateQueries();
  useTraceStore.getState().clearModelInputs();
  void useRunInspector.getState().load();
}
export const useAffectedRuns = create<{ states: Record<string, string> }>(() => ({ states: {} }));
const timers = new Map<string, ReturnType<typeof setTimeout>>();
const waiting = new Set<string>();
const checking = new Set<string>();
let polling = false;
export function observeRevocation(ids: string[]) {
  useTraceStore.getState().clearModelInputs();
  for (const id of ids) {
    if (timers.has(id) || waiting.has(id) || checking.has(id)) continue;
    waiting.add(id);
    useAffectedRuns.setState((s) => ({
      states: { ...s.states, [id]: "正在停止，等待执行端确认" },
    }));
  }
  void drainRuns();
}
async function drainRuns() {
  if (polling || document.hidden) return;
  polling = true;
  const generation = useWorkspace.getState().generation;
  try {
    while (waiting.size && !document.hidden && generation === useWorkspace.getState().generation) {
      const batch = [...waiting].slice(0, 3);
      batch.forEach((id) => {
        waiting.delete(id);
        checking.add(id);
      });
      await Promise.all(
        batch.map(async (id) => {
          let terminal = false;
          let delay = 2000;
          try {
            const snapshot = await workspaceApi.getRun(id);
            if (generation !== useWorkspace.getState().generation) return;
            terminal = ["succeeded", "failed", "cancelled"].includes(snapshot.run.status);
            useAffectedRuns.setState((s) => ({
              states: {
                ...s.states,
                [id]: terminal
                  ? `执行已结束（${snapshot.run.status}）`
                  : "正在停止，等待执行端确认",
              },
            }));
          } catch {
            if (generation !== useWorkspace.getState().generation) return;
            delay = 10000;
            useAffectedRuns.setState((s) => ({ states: { ...s.states, [id]: "状态待确认" } }));
          }
          if (generation !== useWorkspace.getState().generation) return;
          checking.delete(id);
          if (!terminal)
            timers.set(
              id,
              setTimeout(() => {
                timers.delete(id);
                waiting.add(id);
                void drainRuns();
              }, delay),
            );
        }),
      );
    }
  } finally {
    if (generation === useWorkspace.getState().generation) polling = false;
  }
}
const visible = () => {
  if (!document.hidden) void drainRuns();
};
if (typeof document !== "undefined") document.addEventListener("visibilitychange", visible);
if (import.meta.hot)
  import.meta.hot.dispose(() => {
    document.removeEventListener("visibilitychange", visible);
    resetWorkspaceOperations();
  });

export const useConversationUseIntents = create<{
  intents: Record<
    string,
    { key: string; subject?: Subject; recursive: boolean; createNew: boolean }
  >;
}>(() => ({ intents: {} }));

export type ArtifactSaveSource = {
  html: string;
  file_name: string;
  artifactId?: string;
  versionId?: string;
  version?: number;
  title?: string;
};
export type SaveSource = { file_id: string; file_name: string } | ArtifactSaveSource;
export type SaveOperation = {
  id: string;
  source: SaveSource | File;
  parentId: string;
  name: string;
  subject?: Subject;
  uploadKey: string;
  saveKey: string;
  fileId?: string;
  contentUrl?: string;
  ready?: boolean;
  nodeId?: string;
  busy: boolean;
  phase: string;
  error?: string;
  completed?: boolean;
  canChangeSave?: boolean;
};
export const useWorkspaceOperations = create<{ operations: Record<string, SaveOperation> }>(() => ({
  operations: {},
}));
export function startSave(
  source: SaveSource | File,
  parentId: string,
  name: string,
  subject?: Subject,
) {
  const id = newIdempotencyKey();
  const operation: SaveOperation = {
    id,
    source: source instanceof File ? source : { ...source },
    parentId,
    name,
    subject,
    uploadKey: newIdempotencyKey(),
    saveKey: newIdempotencyKey(),
    busy: false,
    phase: "准备保存",
  };
  if (!(source instanceof File) && "file_id" in source) {
    operation.fileId = source.file_id;
    operation.ready = true;
  }
  useWorkspaceOperations.setState((s) => ({ operations: { ...s.operations, [id]: operation } }));
  return id;
}
export function changeSaveTarget(id: string, parentId: string, name: string) {
  const original = useWorkspaceOperations.getState().operations[id];
  if (!original || !original.canChangeSave || original.busy || !original.ready || original.nodeId)
    throw new Error("请先核实原保存结果。");
  const next = startSave(
    { file_id: original.fileId!, file_name: original.name },
    parentId,
    name,
    original.subject,
  );
  useWorkspaceOperations.setState((s) => ({
    operations: {
      ...s.operations,
      [next]: {
        ...s.operations[next]!,
        source: original.source,
        fileId: original.fileId,
        ready: true,
      },
      [id]: { ...original, completed: true, phase: "已改用新保存意图" },
    },
  }));
  return next;
}

export async function resumeSave(id: string) {
  const original = useWorkspaceOperations.getState().operations[id];
  if (!original || original.busy || original.completed) return;
  const generation = useWorkspace.getState().generation;
  const current = () =>
    generation === useWorkspace.getState().generation &&
    Boolean(useWorkspaceOperations.getState().operations[id]);
  const update = (patch: Partial<SaveOperation>) => {
    if (!current()) throw new DOMException("Session changed", "AbortError");
    useWorkspaceOperations.setState((s) => ({
      operations: { ...s.operations, [id]: { ...s.operations[id]!, ...patch } },
    }));
  };
  const read = () => useWorkspaceOperations.getState().operations[id]!;
  update({ busy: true, error: undefined });
  try {
    const file =
      original.source instanceof File
        ? original.source
        : "html" in original.source
          ? new File([original.source.html], original.source.file_name, { type: "text/plain" })
          : null;
    if (!read().fileId) {
      update({ phase: "初始化上传" });
      const upload = await workspaceApi.createWorkspaceUpload(file!, original.uploadKey);
      update({
        fileId: upload.file.file_id,
        contentUrl: upload.content_url,
        ready: upload.file.status === "ready",
      });
    }
    if (!read().ready) {
      update({ phase: "上传内容" });
      const { file: metadata } = await workspaceApi.getFile(read().fileId!);
      if (!current()) return;
      if (metadata.status === "ready") update({ ready: true });
      else if (metadata.status === "uploading") {
        await workspaceApi.uploadContent(read().contentUrl!, file!);
        update({ ready: true });
      } else throw new Error("上传已过期或被拒绝，请重新选择文件。");
    }
    if (!read().nodeId) {
      update({ phase: "保存空间入口" });
      const saved = await workspaceApi.saveWorkspaceFile(
        original.parentId,
        read().fileId!,
        original.name,
        original.saveKey,
      );
      update({ nodeId: saved.node_id });
      useWorkspace.getState().invalidate();
    }
    if (original.subject) {
      update({ phase: "资料已保存，正在授权" });
      await grantMissing(
        original.subject,
        read().nodeId!,
        ["list_metadata", "read_content"],
        false,
      );
    }
    update({
      completed: true,
      phase: original.subject
        ? "已保存并授权，下一轮可用"
        : !(original.source instanceof File) && "html" in original.source
          ? "源码副本已保存到空间，未自动授权"
          : "已保存到空间，未自动授权",
    });
  } catch (error) {
    if (current())
      update({
        error: commandError(error),
        canChangeSave:
          error instanceof HpCommandError &&
          error.status === 409 &&
          Boolean(read().ready) &&
          !read().nodeId,
      });
  } finally {
    if (current()) update({ busy: false });
  }
}
export function resetWorkspaceOperations() {
  resetVersionOperations();
  workQueues.clear();
  workIntents.clear();
  subjectLocks.clear();
  timers.forEach(clearTimeout);
  timers.clear();
  waiting.clear();
  checking.clear();
  polling = false;
  useConversationUseIntents.setState({ intents: {} });
  useAffectedRuns.setState({ states: {} });
  useWorkspaceOperations.setState({ operations: {} });
}
