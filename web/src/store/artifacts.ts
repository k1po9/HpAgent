import { create } from "zustand";
import { api as defaultApi } from "../api/client";
import { HpApi } from "../api/resources";
import type { HpArtifact, HpArtifactSummary, HpArtifactVersion } from "../api/types";
import { newIdempotencyKey } from "./workbench";

const api = new HpApi(defaultApi);
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
  loadForMessage: (messageId: string) => Promise<HpArtifactSummary[]>;
  createArtifact: (messageId: string, instruction?: string | null) => Promise<void>;
  openArtifact: (artifactId: string) => Promise<void>;
  createVersion: (artifactId: string, instruction: string) => Promise<void>;
  selectVersion: (versionId: string) => void;
  closeArtifact: () => void;
  clearConversation: () => void;
}

function upsert(items: HpArtifactVersion[], value: HpArtifactVersion): HpArtifactVersion[] {
  return [
    ...items.filter((item) => item.artifact_version_id !== value.artifact_version_id),
    value,
  ].sort((a, b) => a.version - b.version);
}

export const useArtifacts = create<ArtifactState>()((set) => {
  const poll = async (version: HpArtifactVersion): Promise<void> => {
    let attempt = 0;
    while (version.status === "queued" || version.status === "running") {
      await new Promise((resolve) => setTimeout(resolve, delays[Math.min(attempt++, 3)]));
      version = (await api.getArtifactVersion(version.artifact_version_id)).version;
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
    artifactsByMessageId: {},
    artifactsById: {},
    versionsByArtifactId: {},
    openArtifactId: null,
    openVersionId: null,
    loadingMessageIds: [],
    buildingVersionIds: [],
    error: null,
    loadForMessage: async (messageId) => {
      set((s) => ({ loadingMessageIds: [...s.loadingMessageIds, messageId] }));
      try {
        const result = await api.listMessageArtifacts(messageId);
        set((s) => ({
          artifactsByMessageId: { ...s.artifactsByMessageId, [messageId]: result.items },
          loadingMessageIds: s.loadingMessageIds.filter((id) => id !== messageId),
        }));
        return result.items;
      } catch (error) {
        set((s) => ({
          loadingMessageIds: s.loadingMessageIds.filter((id) => id !== messageId),
          error: error instanceof Error ? error.message : "Artifact 加载失败。",
        }));
        return [];
      }
    },
    createArtifact: async (messageId, instruction = null) => {
      try {
        const result = await api.createArtifact(messageId, instruction, newIdempotencyKey());
        set((s) => ({
          artifactsByMessageId: {
            ...s.artifactsByMessageId,
            [messageId]: [
              ...(s.artifactsByMessageId[messageId] ?? []),
              { artifact: result.artifact, latest_version: result.version },
            ],
          },
          artifactsById: { ...s.artifactsById, [result.artifact.artifact_id]: result.artifact },
          versionsByArtifactId: {
            ...s.versionsByArtifactId,
            [result.artifact.artifact_id]: [result.version],
          },
          openArtifactId: result.artifact.artifact_id,
          openVersionId: result.version.artifact_version_id,
          buildingVersionIds: [...s.buildingVersionIds, result.version.artifact_version_id],
        }));
        void poll(result.version);
      } catch (error) {
        set({ error: error instanceof Error ? error.message : "Artifact 创建失败。" });
      }
    },
    openArtifact: async (artifactId) => {
      try {
        const result = await api.listArtifactVersions(artifactId);
        const completed = [...result.items].reverse().find((v) => v.status === "completed");
        const latest = result.items[result.items.length - 1] ?? null;
        set((s) => ({
          artifactsById: { ...s.artifactsById, [artifactId]: result.artifact },
          versionsByArtifactId: { ...s.versionsByArtifactId, [artifactId]: result.items },
          openArtifactId: artifactId,
          openVersionId: (completed ?? latest)?.artifact_version_id ?? null,
        }));
        if (latest && (latest.status === "queued" || latest.status === "running"))
          void poll(latest);
      } catch (error) {
        set({ error: error instanceof Error ? error.message : "Artifact 加载失败。" });
      }
    },
    createVersion: async (artifactId, instruction) => {
      const value = instruction.trim();
      if (!value) return;
      try {
        const result = await api.createArtifactVersion(artifactId, value, newIdempotencyKey());
        set((s) => ({
          versionsByArtifactId: {
            ...s.versionsByArtifactId,
            [artifactId]: upsert(s.versionsByArtifactId[artifactId] ?? [], result.version),
          },
          openVersionId: result.version.artifact_version_id,
          buildingVersionIds: [...s.buildingVersionIds, result.version.artifact_version_id],
        }));
        void poll(result.version);
      } catch (error) {
        set({ error: error instanceof Error ? error.message : "Artifact 修改失败。" });
      }
    },
    selectVersion: (openVersionId) => set({ openVersionId }),
    closeArtifact: () => set({ openArtifactId: null, openVersionId: null }),
    clearConversation: () => set({ openArtifactId: null, openVersionId: null }),
  };
});
