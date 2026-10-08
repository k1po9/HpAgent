import { create } from "zustand";
import { useShell } from "./shell";
import { api as defaultApi } from "../api/client";
import { HpApi } from "../api/resources";
import {
  HpCommandError,
  type HpArtifact,
  type HpArtifactSummary,
  type HpArtifactVersion,
} from "../api/types";
import { newIdempotencyKey } from "../utils/idempotency";

const delays = [1000, 2000, 3000, 5000];
export const building = (v: HpArtifactVersion) => v.status === "queued" || v.status === "running";
export const latestSuccess = (items: HpArtifactVersion[]) =>
  [...items].sort((a, b) => b.version - a.version).find((v) => v.status === "completed");
export const defaultVersion = (items: HpArtifactVersion[]) =>
  latestSuccess(items) ?? [...items].sort((a, b) => b.version - a.version)[0];
export type QueryState = { loading: boolean; error?: string; status?: number };
export type CommandResult = {
  status: "success" | "failed" | "uncertain" | "stale" | "busy";
  version?: HpArtifactVersion;
  artifact?: HpArtifact;
  error?: string;
  code?: string;
  requestId?: string | null;
  draftRevision?: number;
};
export type ArtifactIntent = {
  key: string;
  instruction: string | null;
  busy: boolean;
  uncertain: boolean;
  result?: CommandResult;
  draftRevision?: number;
};
interface ArtifactState {
  artifactsByMessageId: Record<string, HpArtifactSummary[]>;
  artifactsById: Record<string, HpArtifact>;
  versionsByArtifactId: Record<string, HpArtifactVersion[]>;
  queries: Record<string, QueryState>;
  messageQueries: Record<string, QueryState>;
  intents: Record<string, ArtifactIntent>;
  loadingMessageIds: string[];
  buildingVersionIds: string[];
  loadForMessage: (messageId: string, force?: boolean) => Promise<HpArtifactSummary[] | null>;
  loadArtifact: (artifactId: string, force?: boolean) => Promise<HpArtifactVersion[] | null>;
  createArtifact: (messageId: string, instruction?: string | null) => Promise<CommandResult>;
  createVersion: (
    artifactId: string,
    instruction: string,
    expectedParent?: string | null,
    draftRevision?: number,
  ) => Promise<CommandResult>;
  // UI-7 compatibility: navigation intent only, no domain selection state.
  openArtifact: (artifactId: string, versionId?: string) => void;
  reset: () => void;
}
export interface ArtifactStoreDeps {
  api: HpApi;
  sleep?: (milliseconds: number) => Promise<void>;
  newIdempotencyKey?: () => string;
  onOpen?: (artifactId: string, versionId?: string) => void;
}
const initialState = {
  artifactsByMessageId: {},
  artifactsById: {},
  versionsByArtifactId: {},
  queries: {},
  messageQueries: {},
  intents: {},
  loadingMessageIds: [],
  buildingVersionIds: [],
};
function upsert(items: HpArtifactVersion[], v: HpArtifactVersion) {
  return [...items.filter((i) => i.artifact_version_id !== v.artifact_version_id), v].sort(
    (a, b) => a.version - b.version,
  );
}
const denied = (e: unknown) => e instanceof HpCommandError && [403, 404].includes(e.status);
const message = (e: unknown) => (e instanceof Error ? e.message : "暂时无法同步 HTML 成果。");
export function createArtifactStore(deps: ArtifactStoreDeps = { api: new HpApi(defaultApi) }) {
  const timers = new Map<ReturnType<typeof setTimeout>, () => void>();
  const sleep =
    deps.sleep ??
    ((ms: number) =>
      new Promise<void>((resolve) => {
        const timer = setTimeout(() => {
          timers.delete(timer);
          resolve();
        }, ms);
        timers.set(timer, resolve);
      }));
  const newKey = deps.newIdempotencyKey ?? newIdempotencyKey;
  return create<ArtifactState>()((set, get) => {
    let generation = 0;
    const tokens = new Map<string, number>();
    const epochs = new Map<string, number>();
    // All GET channels share a dispatch sequence, with freshness tracked per version.
    let sequence = 0;
    const versionReads = new Map<string, number>();
    const requests = new Map<string, Promise<HpArtifactVersion[] | null>>();
    const messageRequests = new Map<string, Promise<HpArtifactSummary[] | null>>();
    const pollers = new Map<string, Promise<void>>();
    let messageLimiter = { active: 0, queue: [] as (() => void)[] };
    let pollLimiter = { active: 0, queue: [] as (() => void)[] };
    const reset = () => {
      generation++;
      tokens.clear();
      epochs.clear();
      versionReads.clear();
      requests.clear();
      messageRequests.clear();
      messageLimiter.queue.splice(0).forEach((resolve) => resolve());
      messageLimiter = { active: 0, queue: [] };
      pollLimiter.queue.splice(0).forEach((resolve) => resolve());
      pollLimiter = { active: 0, queue: [] };
      timers.forEach((resolve, t) => {
        clearTimeout(t);
        resolve();
      });
      timers.clear();
      set({ ...initialState });
    };
    const purge = (id: string, error: unknown) => {
      epochs.set(id, (epochs.get(id) ?? 0) + 1);
      set((s) => ({
        artifactsById: Object.fromEntries(
          Object.entries(s.artifactsById).filter(([key]) => key !== id),
        ),
        versionsByArtifactId: Object.fromEntries(
          Object.entries(s.versionsByArtifactId).filter(([key]) => key !== id),
        ),
        artifactsByMessageId: Object.fromEntries(
          Object.entries(s.artifactsByMessageId).map(([key, list]) => [
            key,
            list.filter((x) => x.artifact.artifact_id !== id),
          ]),
        ),
        intents: Object.fromEntries(
          Object.entries(s.intents).filter(([key]) => key !== `version:${id}`),
        ),
        buildingVersionIds: s.buildingVersionIds.filter(
          (v) => !s.versionsByArtifactId[id]?.some((x) => x.artifact_version_id === v),
        ),
        queries: {
          ...s.queries,
          [id]: {
            loading: false,
            error: message(error),
            status: error instanceof HpCommandError ? error.status : 0,
          },
        },
      }));
    };
    const cachedVersion = (v: HpArtifactVersion) =>
      get().versionsByArtifactId[v.artifact_id]?.find(
        (item) => item.artifact_version_id === v.artifact_version_id,
      );
    const cacheVersion = (
      incoming: HpArtifactVersion,
      artifact?: HpArtifact,
      read = ++sequence,
    ) => {
      const cached = cachedVersion(incoming);
      const previousRead = versionReads.get(incoming.artifact_version_id) ?? 0;
      // Completed HTML is immutable. Failed builds may legitimately run again in
      // _prepare on the same Run, so a newer GET may accept failed -> running.
      const accept =
        !cached ||
        (incoming.status === "completed" && cached.status !== "completed") ||
        (read >= previousRead &&
          (cached.status !== "completed" || incoming.status === "completed"));
      const v = accept ? incoming : cached!;
      if (accept) versionReads.set(v.artifact_version_id, Math.max(previousRead, read));
      set((s) => ({
        artifactsById: artifact
          ? { ...s.artifactsById, [v.artifact_id]: artifact }
          : s.artifactsById,
        versionsByArtifactId: {
          ...s.versionsByArtifactId,
          [v.artifact_id]: upsert(s.versionsByArtifactId[v.artifact_id] ?? [], v),
        },
        artifactsByMessageId: Object.fromEntries(
          Object.entries(s.artifactsByMessageId).map(([id, list]) => [
            id,
            list.map((x) =>
              x.artifact.artifact_id === v.artifact_id &&
              (!x.latest_version || x.latest_version.version <= v.version)
                ? { ...x, latest_version: v }
                : x,
            ),
          ]),
        ),
        buildingVersionIds: building(v)
          ? [...new Set([...s.buildingVersionIds, v.artifact_version_id])]
          : s.buildingVersionIds.filter((id) => id !== v.artifact_version_id),
      }));
      return v;
    };
    const poll = (initial: HpArtifactVersion, g: number) => {
      if (!building(initial)) return;
      const epoch = epochs.get(initial.artifact_id) ?? 0;
      const valid = () => generation === g && (epochs.get(initial.artifact_id) ?? 0) === epoch;
      const key = `${g}:${epoch}:${initial.artifact_version_id}`;
      if (pollers.has(key)) return;
      const pending = (async () => {
        let v = initial,
          attempt = 0;
        while (building(v)) {
          await sleep(delays[Math.min(attempt++, 3)]!);
          if (!valid()) return;
          v = cachedVersion(v) ?? v;
          if (!building(v)) return;
          const limiter = pollLimiter;
          if (limiter.active >= 4)
            await new Promise<void>((resolve) => limiter.queue.push(resolve));
          limiter.active++;
          try {
            if (!valid() || !building(cachedVersion(v) ?? v)) return;
            const read = ++sequence;
            const result = await deps.api.getArtifactVersion(v.artifact_version_id);
            if (!valid()) return;
            v = cacheVersion(result.version, undefined, read);
            set((s) => ({
              queries: {
                ...s.queries,
                [v.artifact_id]: { loading: s.queries[v.artifact_id]?.loading ?? false },
              },
            }));
          } catch (e) {
            if (!valid()) return;
            if (e instanceof HpCommandError && e.status === 401) {
              reset();
              return;
            }
            if (denied(e)) {
              purge(v.artifact_id, e);
              return;
            }
            v = cachedVersion(v) ?? v;
            if (!building(v)) return;
            set((s) => ({
              queries: {
                ...s.queries,
                [v.artifact_id]: {
                  loading: s.queries[v.artifact_id]?.loading ?? false,
                  error: `同步中断：${message(e)}`,
                  status: e instanceof HpCommandError ? e.status : 0,
                },
              },
            }));
          } finally {
            limiter.active--;
            limiter.queue.shift()?.();
          }
        }
      })().finally(() => pollers.delete(key));
      pollers.set(key, pending);
    };
    const loadArtifact: ArtifactState["loadArtifact"] = (id, force = false) => {
      if (!force && requests.has(id)) return requests.get(id)!;
      const g = generation,
        token = (tokens.get(id) ?? 0) + 1,
        read = ++sequence;
      tokens.set(id, token);
      const epoch = epochs.get(id) ?? 0;
      const valid = () =>
        g === generation && tokens.get(id) === token && (epochs.get(id) ?? 0) === epoch;
      set((s) => ({ queries: { ...s.queries, [id]: { loading: true } } }));
      const pending = (async () => {
        try {
          const result = await deps.api.listArtifactVersions(id);
          if (!valid()) return null;
          let items = result.items.map((v) => cacheVersion(v, undefined, read));
          // Only versions written since dispatch can be absent from this snapshot
          // (for example, a newly accepted POST). An unrelated poll is no override.
          for (const v of get().versionsByArtifactId[id] ?? [])
            if ((versionReads.get(v.artifact_version_id) ?? 0) > read) items = upsert(items, v);
          items.sort((a, b) => a.version - b.version);
          set((s) => ({
            artifactsById: { ...s.artifactsById, [id]: result.artifact },
            versionsByArtifactId: { ...s.versionsByArtifactId, [id]: items },
            queries: { ...s.queries, [id]: { loading: false } },
            buildingVersionIds: [
              ...new Set([
                ...s.buildingVersionIds.filter(
                  (v) => !s.versionsByArtifactId[id]?.some((x) => x.artifact_version_id === v),
                ),
                ...items.filter(building).map((v) => v.artifact_version_id),
              ]),
            ],
          }));
          items.forEach((v) => poll(v, g));
          return items;
        } catch (e) {
          if (!valid()) return null;
          if (e instanceof HpCommandError && e.status === 401) {
            reset();
            return null;
          }
          if (denied(e)) purge(id, e);
          else
            set((s) => ({
              queries: {
                ...s.queries,
                [id]: {
                  loading: false,
                  error: message(e),
                  status: e instanceof HpCommandError ? e.status : 0,
                },
              },
            }));
          return null;
        }
      })().finally(() => {
        if (requests.get(id) === pending) requests.delete(id);
      });
      requests.set(id, pending);
      return pending;
    };
    const loadForMessage: ArtifactState["loadForMessage"] = (id, force = false) => {
      if (messageRequests.has(id)) return messageRequests.get(id)!;
      if (!force && get().artifactsByMessageId[id])
        return Promise.resolve(get().artifactsByMessageId[id]!);
      const g = generation;
      set((s) => ({
        messageQueries: { ...s.messageQueries, [id]: { loading: true } },
        loadingMessageIds: [...new Set([...s.loadingMessageIds, id])],
      }));
      const limiter = messageLimiter;
      const pending = (async () => {
        if (limiter.active >= 4) await new Promise<void>((resolve) => limiter.queue.push(resolve));
        limiter.active++;
        try {
          if (g !== generation) return null;
          const read = ++sequence;
          const requestEpochs = new Map(epochs);
          const result = await deps.api.listMessageArtifacts(id);
          if (g !== generation) return null;
          const known = get().artifactsByMessageId[id] ?? [];
          const items = [
            ...result.items
              .filter(
                (item) =>
                  (epochs.get(item.artifact.artifact_id) ?? 0) ===
                  (requestEpochs.get(item.artifact.artifact_id) ?? 0),
              )
              .map((item) => {
                if (item.latest_version) cacheVersion(item.latest_version, item.artifact, read);
                const cached = get().versionsByArtifactId[item.artifact.artifact_id]?.at(-1);
                const summary = known.find(
                  (x) => x.artifact.artifact_id === item.artifact.artifact_id,
                )?.latest_version;
                const latest =
                  summary && (!cached || summary.version > cached.version) ? summary : cached;
                return latest &&
                  (!item.latest_version || latest.version >= item.latest_version.version)
                  ? { ...item, latest_version: latest }
                  : item;
              }),
            ...known.filter(
              (x) => !result.items.some((y) => y.artifact.artifact_id === x.artifact.artifact_id),
            ),
          ];
          set((s) => ({
            artifactsByMessageId: { ...s.artifactsByMessageId, [id]: items },
            messageQueries: { ...s.messageQueries, [id]: { loading: false } },
          }));
          items.forEach((item) => {
            if (item.latest_version) poll(item.latest_version, g);
          });
          return items;
        } catch (e) {
          if (g !== generation) return null;
          if (e instanceof HpCommandError && e.status === 401) reset();
          else
            set((s) => ({
              messageQueries: {
                ...s.messageQueries,
                [id]: {
                  loading: false,
                  error: message(e),
                  status: e instanceof HpCommandError ? e.status : 0,
                },
              },
              ...(denied(e)
                ? { artifactsByMessageId: { ...s.artifactsByMessageId, [id]: [] } }
                : {}),
            }));
          return null;
        } finally {
          limiter.active--;
          limiter.queue.shift()?.();
          if (g === generation)
            set((s) => ({ loadingMessageIds: s.loadingMessageIds.filter((x) => x !== id) }));
        }
      })().finally(() => {
        if (messageRequests.get(id) === pending) messageRequests.delete(id);
      });
      messageRequests.set(id, pending);
      return pending;
    };
    const command = async (
      kind: "message" | "version",
      id: string,
      instruction: string | null,
      expectedParent?: string | null,
      draftRevision?: number,
    ): Promise<CommandResult> => {
      const slot = `${kind}:${id}`,
        old = get().intents[slot];
      if (old?.busy) return { status: "busy" };
      const intent: ArtifactIntent = old?.uncertain
        ? { ...old, busy: true }
        : { key: newKey(), instruction, busy: true, uncertain: false, draftRevision };
      if (!old?.uncertain && kind === "version" && (!instruction || [...instruction].length > 4000))
        return { status: "failed", error: "修改指令须为 1–4000 个字符。" };
      const g = generation,
        epoch = epochs.get(id) ?? 0;
      const valid = () =>
        generation === g && (kind !== "version" || (epochs.get(id) ?? 0) === epoch);
      const update = (result: CommandResult) => {
        if (valid())
          set((s) => ({
            intents: {
              ...s.intents,
              [slot]: { ...intent, busy: false, uncertain: result.status === "uncertain", result },
            },
          }));
        return result;
      };
      set((s) => ({ intents: { ...s.intents, [slot]: intent } }));
      if (kind === "version" && !old?.uncertain) {
        const items = await loadArtifact(id, true);
        if (!valid()) return { status: "stale" };
        if (!items) return update({ status: "failed", error: "无法核实版本，请同步后重试。" });
        const running = items.find(building);
        if (running)
          return update({
            status: "failed",
            error: `v${running.version} 正在构建，请等待完成。`,
            version: running,
          });
        if (
          expectedParent !== undefined &&
          (latestSuccess(items)?.artifact_version_id ?? null) !== expectedParent
        )
          return update({
            status: "failed",
            error: "最近成功版本已改变，请审阅新的修改基准后再次提交。",
          });
      }
      try {
        const result =
          kind === "message"
            ? await deps.api.createArtifact(id, intent.instruction, intent.key)
            : await deps.api.createArtifactVersion(id, intent.instruction!, intent.key);
        if (!valid()) return { status: "stale" };
        const version = cacheVersion(result.version, result.artifact);
        if (kind === "message")
          set((s) => ({
            artifactsByMessageId: {
              ...s.artifactsByMessageId,
              [id]: [
                ...(s.artifactsByMessageId[id] ?? []).filter(
                  (x) => x.artifact.artifact_id !== result.artifact.artifact_id,
                ),
                { artifact: result.artifact, latest_version: version },
              ],
            },
          }));
        poll(version, g);
        return update({
          status: "success",
          ...result,
          version,
          draftRevision: intent.draftRevision,
        });
      } catch (e) {
        if (!valid()) return { status: "stale" };
        if (e instanceof HpCommandError && e.status === 401) {
          reset();
          return { status: "stale" };
        }
        if (kind === "version" && denied(e)) {
          purge(id, e);
          return { status: "failed", error: message(e) };
        }
        return update({
          status: !(e instanceof HpCommandError) || e.status >= 500 ? "uncertain" : "failed",
          error: message(e),
          code: e instanceof HpCommandError ? e.code : undefined,
          requestId: e instanceof HpCommandError ? e.error.request_id : undefined,
        });
      }
    };
    return {
      ...initialState,
      loadArtifact,
      loadForMessage,
      createArtifact: (id, instruction = null) =>
        command("message", id, instruction?.trim() || null),
      createVersion: (id, instruction, parent, draftRevision) =>
        command("version", id, instruction.trim(), parent, draftRevision),
      openArtifact: (id, version) => deps.onOpen?.(id, version),
      reset,
    };
  });
}
export const useArtifacts = createArtifactStore({
  api: new HpApi(defaultApi),
  onOpen: (objectId, versionId) =>
    useShell.getState().openInspector({ kind: "artifact", objectId, versionId }),
});
