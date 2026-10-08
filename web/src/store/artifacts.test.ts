import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiClient } from "../api/client";
import { HpApi } from "../api/resources";
import { HpCommandError, type HpArtifact, type HpArtifactVersion } from "../api/types";
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
    const pending = store.getState().loadArtifact("a1");
    await store.getState().loadArtifact("b");
    await store.getState().loadArtifact("a1", true);
    old.resolve({ artifact, items: [version("completed")] });
    await pending;
    expect(store.getState().versionsByArtifactId.a1?.[0]?.artifact_version_id).toBe("current");

    const failure = deferred<Awaited<ReturnType<HpApi["listArtifactVersions"]>>>();
    load.mockReturnValueOnce(
      failure.promise.then(() => {
        throw new Error("old failure");
      }),
    );
    const failed = store.getState().loadArtifact("b");
    await store.getState().loadArtifact("a1");
    failure.resolve({ artifact, items: [] });
    await failed;
    expect(store.getState().queries.a1?.error).toBeUndefined();
    expect(store.getState().queries.b?.error).toBe("old failure");
  });

  it("keeps a late created version on its object without changing the new selection", async () => {
    const result = deferred<Awaited<ReturnType<HpApi["createArtifactVersion"]>>>();
    const store = createArtifactStore({
      api: fakeApi({ createArtifactVersion: () => result.promise }),
    });
    await store.getState().loadArtifact("a1");
    const creating = store.getState().createVersion("a1", "change");
    await store.getState().loadArtifact("b");
    result.resolve({ artifact, version: { ...version("completed"), artifact_version_id: "new" } });
    await creating;
    expect(
      store.getState().versionsByArtifactId.a1?.some((v) => v.artifact_version_id === "new"),
    ).toBe(true);
    expect(store.getState()).not.toHaveProperty("openArtifact");
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
      await store.getState().loadArtifact("a1");
      await store.getState().loadArtifact("a1");
      expect(vi.getTimerCount()).toBe(1);
      await vi.advanceTimersByTimeAsync(1000);
      expect(getVersion).toHaveBeenCalledOnce();
      await store.getState().loadArtifact("a1");
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
      loadingMessageIds: ["m1"],
      buildingVersionIds: ["v1"],
      queries: { a1: { loading: false, error: "old account error" } },
    });

    store.getState().reset();

    expect(store.getState()).toMatchObject({
      artifactsByMessageId: {},
      artifactsById: {},
      versionsByArtifactId: {},
      loadingMessageIds: [],
      buildingVersionIds: [],
      queries: {},
      intents: {},
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
    expect(store.getState().artifactsById.a1).toEqual(artifact);
  });

  it("exposes an Artifact creation failure to the UI state", async () => {
    const store = createArtifactStore({
      api: fakeApi({ createArtifact: vi.fn().mockRejectedValue(new Error("Artifact 服务不可用")) }),
    });

    await store.getState().createArtifact("m1");

    expect(store.getState().intents["message:m1"]?.result).toMatchObject({
      status: "uncertain",
      error: "Artifact 服务不可用",
    });
  });
});

describe("UI-6 commands and queries", () => {
  it("locks a message intent and replays an uncertain create using the same key", async () => {
    const pending = deferred<Awaited<ReturnType<HpApi["createArtifact"]>>>();
    const create = vi
      .fn()
      .mockReturnValueOnce(
        pending.promise.then(() => {
          throw new TypeError("lost response");
        }),
      )
      .mockResolvedValue({ artifact, version: version("completed") });
    const key = vi.fn().mockReturnValueOnce("original").mockReturnValue("new");
    const store = createArtifactStore({
      api: fakeApi({ createArtifact: create }),
      newIdempotencyKey: key,
    });
    const first = store.getState().createArtifact("m1");
    expect((await store.getState().createArtifact("m1")).status).toBe("busy");
    pending.resolve({ artifact, version: version("completed") });
    expect((await first).status).toBe("uncertain");
    expect((await store.getState().createArtifact("m1", "changed")).status).toBe("success");
    expect(create.mock.calls.map((c) => c.slice(1))).toEqual([
      [null, "original"],
      [null, "original"],
    ]);
    expect(key).toHaveBeenCalledOnce();
  });
  it("preflights busy versions and changed parents without POST, then allows a new intent", async () => {
    const create = vi.fn(async () => ({
      artifact,
      version: { ...version("completed"), version: 3, artifact_version_id: "v3" },
    }));
    const list = vi
      .fn()
      .mockResolvedValueOnce({ artifact, items: [version("running")] })
      .mockResolvedValue({
        artifact,
        items: [{ ...version("completed"), artifact_version_id: "v2", version: 2 }],
      });
    const store = createArtifactStore({
      api: fakeApi({ listArtifactVersions: list, createArtifactVersion: create }),
      sleep: () => new Promise(() => {}),
    });
    expect((await store.getState().createVersion("a1", "修改", "v1")).error).toContain("正在构建");
    expect((await store.getState().createVersion("a1", "修改", "v1")).error).toContain("基准");
    expect(create).not.toHaveBeenCalled();
    expect((await store.getState().createVersion("a1", "修改", "v2")).status).toBe("success");
    expect(create.mock.calls[0]).toHaveLength(3);
  });
  it("retains a version key across network failure and ignores changed draft during recovery", async () => {
    const create = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("lost"))
      .mockResolvedValue({ artifact, version: version("completed") });
    const store = createArtifactStore({ api: fakeApi({ createArtifactVersion: create }) });
    await store.getState().createVersion("a1", "原指令", "v1");
    await store.getState().createVersion("a1", "新指令", "v1");
    expect(create.mock.calls[1]).toEqual(create.mock.calls[0]);
  });
  it("counts Unicode code points, rejecting over 4000 while admitting 4000 emoji", async () => {
    const create = vi.fn(async () => ({ artifact, version: version("completed") }));
    const store = createArtifactStore({ api: fakeApi({ createArtifactVersion: create }) });
    expect((await store.getState().createVersion("a1", "😀".repeat(4001))).status).toBe("failed");
    expect(create).not.toHaveBeenCalled();
    expect((await store.getState().createVersion("a1", "😀".repeat(4000))).status).toBe("success");
  });
  it("restores every unfinished version once, updates summaries and stops denied pollers", async () => {
    vi.useFakeTimers();
    try {
      const v2 = { ...version("running"), version: 2, artifact_version_id: "v2" };
      const read = vi
        .fn()
        .mockResolvedValueOnce({ version: version("completed") })
        .mockRejectedValueOnce(
          new HpCommandError(403, {
            code: "forbidden",
            message: "denied",
            request_id: null,
            retryable: false,
            details: {},
          }),
        );
      const store = createArtifactStore({
        api: fakeApi({
          getArtifactVersion: read,
          listArtifactVersions: async () => ({ artifact, items: [v2, version("queued")] }),
        }),
      });
      store.setState({ artifactsByMessageId: { m1: [{ artifact, latest_version: v2 }] } });
      await store.getState().loadArtifact("a1");
      await store.getState().loadArtifact("a1");
      expect(vi.getTimerCount()).toBe(2);
      await vi.advanceTimersByTimeAsync(1000);
      expect(store.getState().artifactsById.a1).toBeUndefined();
      expect(store.getState().buildingVersionIds).toEqual([]);
      await vi.advanceTimersByTimeAsync(10000);
      expect(read).toHaveBeenCalledTimes(2);
      store.getState().reset();
    } finally {
      vi.useRealTimers();
    }
  });
  it("deduplicates and bounds visible message queries", async () => {
    const pending = deferred<{ items: [] }>();
    const list = vi.fn(() => pending.promise);
    const store = createArtifactStore({ api: fakeApi({ listMessageArtifacts: list }) });
    const queries = Array.from({ length: 8 }, (_, i) => store.getState().loadForMessage(`m${i}`));
    const duplicate = store.getState().loadForMessage("m0");
    expect(duplicate).toBe(queries[0]);
    expect(list).toHaveBeenCalledTimes(4);
    pending.resolve({ items: [] });
    await Promise.all(queries);
    expect(list).toHaveBeenCalledTimes(8);
  });
  it("ignores late create and load after account reset", async () => {
    const pending = deferred<Awaited<ReturnType<HpApi["createArtifact"]>>>();
    const store = createArtifactStore({ api: fakeApi({ createArtifact: () => pending.promise }) });
    const command = store.getState().createArtifact("m1");
    store.getState().reset();
    pending.resolve({ artifact, version: version("completed") });
    expect((await command).status).toBe("stale");
    expect(store.getState().intents).toEqual({});
    expect(store.getState().artifactsById).toEqual({});
  });
});

it("carries the original draft revision through uncertain recovery", async () => {
  const create = vi
    .fn()
    .mockRejectedValueOnce(new TypeError("lost"))
    .mockResolvedValue({ artifact, version: version("completed") });
  const store = createArtifactStore({ api: fakeApi({ createArtifactVersion: create }) });
  await store.getState().createVersion("a1", "原指令", "v1", 3);
  const result = await store.getState().createVersion("a1", "更新后的草稿", "v1", 4);
  expect(result.draftRevision).toBe(3);
  expect(create.mock.calls[1]?.[1]).toBe("原指令");
});
it("lets a new account query messages while old account requests are still pending", async () => {
  const old = deferred<{ items: [] }>();
  const list = vi.fn().mockReturnValue(old.promise);
  const store = createArtifactStore({ api: fakeApi({ listMessageArtifacts: list }) });
  const requests = Array.from({ length: 5 }, (_, i) => store.getState().loadForMessage(`old${i}`));
  store.getState().reset();
  list.mockResolvedValue({ items: [] });
  expect(await store.getState().loadForMessage("new")).toEqual([]);
  old.resolve({ items: [] });
  await Promise.all(requests);
  expect(Object.keys(store.getState().artifactsByMessageId)).toEqual(["new"]);
});
