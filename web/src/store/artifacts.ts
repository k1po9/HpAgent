import { create } from "zustand";
import { api as defaultApi } from "../api/client";
import { HpApi } from "../api/resources";
import {
  HpCommandError,
  type HpArtifact,
  type HpArtifactSummary,
  type HpArtifactVersion,
} from "../api/types";
import { newIdempotencyKey } from "../utils/idempotency";

const delays = [1000, 2000, 3000, 5000] as const;

interface ArtifactState {
  artifactsByMessageId: Record<string, HpArtifactSummary[]>;
  artifactsById: Record<string, HpArtifact>;
  versionsByArtifactId: Record<string, HpArtifactVersion[]>;
  openArtifactId: string | null;
  openVersionId: string | null;
  loadingMessageIds: string[];
  buildingVersionIds: string[];
  error: string | null;
  loadForMessage: (messageId: string) => Promise<HpArtifactSummary[] | null>;
  createArtifact: (messageId: string, instruction?: string | null) => Promise<void>;
  openArtifact: (artifactId: string) => Promise<void>;
  createVersion: (artifactId: string, instruction: string) => Promise<void>;
  selectVersion: (versionId: string) => void;
  clearError: () => void;
  closeArtifact: () => void;
  reset: () => void;
}

export interface ArtifactStoreDeps {
  api: HpApi;
  sleep?: (milliseconds: number) => Promise<void>;
  newIdempotencyKey?: () => string;
}

const initialState = {
  artifactsByMessageId: {},
  artifactsById: {},
  versionsByArtifactId: {},
  openArtifactId: null,
  openVersionId: null,
  loadingMessageIds: [],
  buildingVersionIds: [],
  error: null,
};

function upsert(items: HpArtifactVersion[], value: HpArtifactVersion): HpArtifactVersion[] {
  return [
    ...items.filter((item) => item.artifact_version_id !== value.artifact_version_id),
    value,
  ].sort((a, b) => a.version - b.version);
}

function unique(values: string[]): string[] {
  return [...new Set(values)];
}

export function createArtifactStore(deps: ArtifactStoreDeps = { api: new HpApi(defaultApi) }) {
  const api = deps.api;
  const sleep =
    deps.sleep ?? ((milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)));
  const newKey = deps.newIdempotencyKey ?? newIdempotencyKey;

  return create<ArtifactState>()((set) => {
    // Every conversation/account context change invalidates all outstanding
    // requests and pollers. Fetch cannot always be aborted after dispatch, so
    // generation is the final guard before any response mutates the store.
    let generation = 0;
    const current = (value: number): boolean => value === generation;

    const reset = (): void => {
      generation += 1;
      set({ ...initialState });
    };

    const poll = async (initial: HpArtifactVersion, pollGeneration: number): Promise<void> => {
      let version = initial;
      let attempt = 0;
      while (version.status === "queued" || version.status === "running") {
        await sleep(delays[Math.min(attempt++, delays.length - 1)] ?? 5000);
        if (!current(pollGeneration)) return;
        try {
          version = (await api.getArtifactVersion(version.artifact_version_id)).version;
        } catch (error) {
          if (!current(pollGeneration)) return;
          if (error instanceof HpCommandError && error.status === 401) {
            reset();
            return;
          }
          // A network/API restart is not a build failure. Keep the build marker
          // and retry at the capped delay until an authoritative state returns.
          continue;
        }
        if (!current(pollGeneration)) return;
        set((state) => ({
          versionsByArtifactId: {
            ...state.versionsByArtifactId,
            [version.artifact_id]: upsert(
              state.versionsByArtifactId[version.artifact_id] ?? [],
              version,
            ),
          },
          buildingVersionIds:
            version.status === "queued" || version.status === "running"
              ? state.buildingVersionIds
              : state.buildingVersionIds.filter((id) => id !== version.artifact_version_id),
        }));
      }
    };

    return {
      ...initialState,
      loadForMessage: async (messageId) => {
        const requestGeneration = generation;
        set((state) => ({
          loadingMessageIds: unique([...state.loadingMessageIds, messageId]),
          error: null,
        }));
        try {
          const result = await api.listMessageArtifacts(messageId);
          if (!current(requestGeneration)) return null;
          set((state) => ({
            artifactsByMessageId: {
              ...state.artifactsByMessageId,
              [messageId]: result.items,
            },
            loadingMessageIds: state.loadingMessageIds.filter((id) => id !== messageId),
          }));
          return result.items;
        } catch (error) {
          if (!current(requestGeneration)) return null;
          set((state) => ({
            loadingMessageIds: state.loadingMessageIds.filter((id) => id !== messageId),
            error: error instanceof Error ? error.message : "Artifact 加载失败。",
          }));
          return null;
        }
      },
      createArtifact: async (messageId, instruction = null) => {
        const requestGeneration = generation;
        set({ error: null });
        try {
          const result = await api.createArtifact(messageId, instruction, newKey());
          if (!current(requestGeneration)) return;
          set((state) => ({
            artifactsByMessageId: {
              ...state.artifactsByMessageId,
              [messageId]: [
                ...(state.artifactsByMessageId[messageId] ?? []),
                { artifact: result.artifact, latest_version: result.version },
              ],
            },
            artifactsById: {
              ...state.artifactsById,
              [result.artifact.artifact_id]: result.artifact,
            },
            versionsByArtifactId: {
              ...state.versionsByArtifactId,
              [result.artifact.artifact_id]: [result.version],
            },
            openArtifactId: result.artifact.artifact_id,
            openVersionId: result.version.artifact_version_id,
            buildingVersionIds: unique([
              ...state.buildingVersionIds,
              result.version.artifact_version_id,
            ]),
          }));
          void poll(result.version, requestGeneration);
        } catch (error) {
          if (!current(requestGeneration)) return;
          set({ error: error instanceof Error ? error.message : "Artifact 创建失败。" });
        }
      },
      openArtifact: async (artifactId) => {
        const requestGeneration = generation;
        set({ error: null });
        try {
          const result = await api.listArtifactVersions(artifactId);
          if (!current(requestGeneration)) return;
          const completed = [...result.items]
            .reverse()
            .find((version) => version.status === "completed");
          const latest = result.items[result.items.length - 1] ?? null;
          set((state) => ({
            artifactsById: { ...state.artifactsById, [artifactId]: result.artifact },
            versionsByArtifactId: {
              ...state.versionsByArtifactId,
              [artifactId]: result.items,
            },
            openArtifactId: artifactId,
            openVersionId: (completed ?? latest)?.artifact_version_id ?? null,
            buildingVersionIds:
              latest && (latest.status === "queued" || latest.status === "running")
                ? unique([...state.buildingVersionIds, latest.artifact_version_id])
                : state.buildingVersionIds,
          }));
          if (latest && (latest.status === "queued" || latest.status === "running")) {
            void poll(latest, requestGeneration);
          }
        } catch (error) {
          if (!current(requestGeneration)) return;
          set({ error: error instanceof Error ? error.message : "Artifact 加载失败。" });
        }
      },
      createVersion: async (artifactId, instruction) => {
        const value = instruction.trim();
        if (!value) return;
        const requestGeneration = generation;
        set({ error: null });
        try {
          const result = await api.createArtifactVersion(artifactId, value, newKey());
          if (!current(requestGeneration)) return;
          set((state) => ({
            versionsByArtifactId: {
              ...state.versionsByArtifactId,
              [artifactId]: upsert(state.versionsByArtifactId[artifactId] ?? [], result.version),
            },
            openVersionId: result.version.artifact_version_id,
            buildingVersionIds: unique([
              ...state.buildingVersionIds,
              result.version.artifact_version_id,
            ]),
          }));
          void poll(result.version, requestGeneration);
        } catch (error) {
          if (!current(requestGeneration)) return;
          set({ error: error instanceof Error ? error.message : "Artifact 修改失败。" });
        }
      },
      selectVersion: (openVersionId) => set({ openVersionId }),
      clearError: () => set({ error: null }),
      closeArtifact: () => set({ openArtifactId: null, openVersionId: null }),
      reset,
    };
  });
}

export const useArtifacts = createArtifactStore();
