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
  it("ignores stale object loads across A-B-A, including a late failure", async () => {
    const old = deferred<Awaited<ReturnType<HpApi["listArtifactVersions"]>>>();
    const load = vi
      .fn()
      .mockReturnValueOnce(old.promise)
      .mockResolvedValue({
        artifact,
        items: [{ ...version("completed"), artifact_version_id: "current" }],
      });
    const store = createArtifactStore({ api: fakeApi({ listArtifactVersions: load }) });
    const pending = store.getState().openArtifact("a1");
    await store.getState().openArtifact("b");
    await store.getState().openArtifact("a1");
    old.resolve({ artifact, items: [version("completed")] });
    await pending;
    expect(store.getState().openVersionId).toBe("current");

    const failure = deferred<Awaited<ReturnType<HpApi["listArtifactVersions"]>>>();
    load.mockReturnValueOnce(
      failure.promise.then(() => {
        throw new Error("old failure");
      }),
    );
    const failed = store.getState().openArtifact("b");
    await store.getState().openArtifact("a1");
    failure.resolve({ artifact, items: [] });
    await failed;
    expect(store.getState().error).toBeNull();
  });

  it("keeps a late created version on its object without changing the new selection", async () => {
    const result = deferred<Awaited<ReturnType<HpApi["createArtifactVersion"]>>>();
    let navigation = 0;
    const onVersion = vi.fn();
    const store = createArtifactStore({
      api: fakeApi({ createArtifactVersion: () => result.promise }),
      navigationToken: () => navigation,
      onVersion,
    });
    await store.getState().openArtifact("a1");
    const creating = store.getState().createVersion("a1", "change");
    navigation += 1;
    result.resolve({ artifact, version: { ...version("completed"), artifact_version_id: "new" } });
    await creating;
    expect(
      store.getState().versionsByArtifactId.a1?.some((v) => v.artifact_version_id === "new"),
    ).toBe(true);
    expect(store.getState().openVersionId).toBe("v1");
    expect(onVersion).not.toHaveBeenCalled();
  });

  it("reopening a building version shares one poller and reset clears its timer", async () => {
    vi.useFakeTimers();
    try {
      const getVersion = vi.fn(async () => ({ version: version("completed") }));
      const store = createArtifactStore({
        api: fakeApi({
          listArtifactVersions: vi.fn(async () => ({ artifact, items: [version("running")] })),
          getArtifactVersion: getVersion,
        }),
      });
      await store.getState().openArtifact("a1");
      await store.getState().openArtifact("a1");
      expect(vi.getTimerCount()).toBe(1);
      await vi.advanceTimersByTimeAsync(1000);
      expect(getVersion).toHaveBeenCalledOnce();
      await store.getState().openArtifact("a1");
      store.getState().reset();
      expect(vi.getTimerCount()).toBe(0);
      await vi.advanceTimersByTimeAsync(5000);
      expect(getVersion).toHaveBeenCalledOnce();
    } finally {
      vi.useRealTimers();
    }
  });

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
