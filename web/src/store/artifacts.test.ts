import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiClient } from "../api/client";
import { HpApi } from "../api/resources";
import type { HpArtifact, HpArtifactVersion } from "../api/types";
import { createArtifactStore } from "./artifacts";

afterEach(() => vi.unstubAllGlobals());

const artifact: HpArtifact = {
  artifact_id: "a1",
  conversation_id: "c1",
  source_message_id: "m1",
  kind: "html",
  title: "Artifact",
  created_at: "2026-08-15T00:00:00Z",
  updated_at: "2026-08-15T00:00:00Z",
};

function version(status: HpArtifactVersion["status"]): HpArtifactVersion {
  return {
    artifact_version_id: "v1",
    artifact_id: "a1",
    version: 1,
    parent_version_id: null,
    status,
    instruction: null,
    html: status === "completed" ? "<html></html>" : null,
    failure: null,
    created_at: "2026-08-15T00:00:00Z",
    started_at: status === "queued" ? null : "2026-08-15T00:00:01Z",
    completed_at: status === "completed" ? "2026-08-15T00:00:02Z" : null,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function fakeApi(overrides: Partial<HpApi> = {}): HpApi {
  return {
    createArtifact: vi.fn(async () => ({ artifact, version: version("queued") })),
    getArtifactVersion: vi.fn(async () => ({ version: version("completed") })),
    listMessageArtifacts: vi.fn(async () => ({ items: [] })),
    listArtifactVersions: vi.fn(async () => ({ artifact, items: [version("completed")] })),
    createArtifactVersion: vi.fn(async () => ({ artifact, version: version("queued") })),
    ...overrides,
  } as unknown as HpApi;
}

describe("artifact store lifecycle", () => {
  it("fully clears account-scoped HTML and transient state on reset", () => {
    const store = createArtifactStore({ api: fakeApi() });
    store.setState({
      artifactsByMessageId: { m1: [{ artifact, latest_version: version("completed") }] },
      artifactsById: { a1: artifact },
      versionsByArtifactId: { a1: [version("completed")] },
      openArtifactId: "a1",
      openVersionId: "v1",
      loadingMessageIds: ["m1"],
      buildingVersionIds: ["v1"],
      error: "old account error",
    });

    store.getState().reset();

    expect(store.getState()).toMatchObject({
      artifactsByMessageId: {},
      artifactsById: {},
      versionsByArtifactId: {},
      openArtifactId: null,
      openVersionId: null,
      loadingMessageIds: [],
      buildingVersionIds: [],
      error: null,
    });
  });

  it("recovers polling after a transient network failure", async () => {
    const getVersion = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("network reset"))
      .mockResolvedValueOnce({ version: version("completed") });
    const store = createArtifactStore({
      api: fakeApi({ getArtifactVersion: getVersion }),
      sleep: async () => undefined,
      newIdempotencyKey: () => "key",
    });

    await store.getState().createArtifact("m1");
    await vi.waitFor(() => expect(getVersion).toHaveBeenCalledTimes(2));
    await vi.waitFor(() => expect(store.getState().buildingVersionIds).toEqual([]));
    expect(store.getState().versionsByArtifactId.a1?.[0]?.status).toBe("completed");
  });

  it("drops a poll response that returns after reset", async () => {
    const pending = deferred<{ version: HpArtifactVersion }>();
    const store = createArtifactStore({
      api: fakeApi({ getArtifactVersion: vi.fn(() => pending.promise) }),
      sleep: async () => undefined,
      newIdempotencyKey: () => "key",
    });

    await store.getState().createArtifact("m1");
    await vi.waitFor(() => expect(store.getState().buildingVersionIds).toEqual(["v1"]));
    store.getState().reset();
    pending.resolve({ version: version("completed") });
    await Promise.resolve();
    await Promise.resolve();

    expect(store.getState().artifactsById).toEqual({});
    expect(store.getState().versionsByArtifactId).toEqual({});
    expect(store.getState().buildingVersionIds).toEqual([]);
  });

  it("creates an Artifact with a fallback UUID when randomUUID is unavailable", async () => {
    const request = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        new Response(JSON.stringify({ artifact, version: version("completed") }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    );
    vi.stubGlobal("crypto", {
      getRandomValues: (bytes: Uint8Array) => {
        bytes.fill(0);
        return bytes;
      },
    });
    const store = createArtifactStore({ api: new HpApi(new ApiClient(request)) });

    await store.getState().createArtifact("m1");

    expect(request).toHaveBeenCalledOnce();
    expect(request.mock.calls[0]?.[0]).toBe("/api/v1/messages/m1/artifacts");
    expect(request.mock.calls[0]?.[1]).toMatchObject({
      method: "POST",
      headers: expect.objectContaining({
        "Idempotency-Key": "00000000-0000-4000-8000-000000000000",
      }),
    });
    expect(store.getState().openArtifactId).toBe("a1");
  });

  it("exposes an Artifact creation failure to the UI state", async () => {
    const store = createArtifactStore({
      api: fakeApi({ createArtifact: vi.fn().mockRejectedValue(new Error("Artifact 服务不可用")) }),
    });

    await store.getState().createArtifact("m1");

    expect(store.getState().error).toBe("Artifact 服务不可用");
  });
});
