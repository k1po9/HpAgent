import { afterEach, expect, it, vi } from "vitest";
import { HpApi } from "../api/resources";
import { type HpArtifact, type HpArtifactVersion } from "../api/types";
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

it("R1: a late running poll cannot overwrite a completed version list", async () => {
  vi.useFakeTimers();
  const pending = deferred<{ version: HpArtifactVersion }>();
  const list = vi
    .fn()
    .mockResolvedValueOnce({ artifact, items: [version("running")] })
    .mockResolvedValue({ artifact, items: [version("completed")] });
  const store = createArtifactStore({
    api: fakeApi({ listArtifactVersions: list, getArtifactVersion: () => pending.promise }),
  });
  try {
    await store.getState().loadArtifact("a1");
    await vi.advanceTimersByTimeAsync(1000);
    await store.getState().loadArtifact("a1", true);
    expect(store.getState().versionsByArtifactId.a1?.[0]?.status).toBe("completed");
    pending.resolve({ version: version("running") });
    await vi.advanceTimersByTimeAsync(0);
    expect(store.getState().versionsByArtifactId.a1?.[0]?.status).toBe("completed");
  } finally {
    store.getState().reset();
    vi.useRealTimers();
  }
});

it("R2: refreshing message summaries accepts the same version's terminal state", async () => {
  const list = vi
    .fn()
    .mockResolvedValueOnce({ items: [{ artifact, latest_version: version("running") }] })
    .mockResolvedValue({ items: [{ artifact, latest_version: version("completed") }] });
  const store = createArtifactStore({ api: fakeApi({ listMessageArtifacts: list }) });
  try {
    await store.getState().loadForMessage("m1");
    await store.getState().loadForMessage("m1", true);
    expect(list).toHaveBeenCalledTimes(2);
    expect(store.getState().artifactsByMessageId.m1?.[0]?.latest_version?.status).toBe("completed");
  } finally {
    store.getState().reset();
  }
});

it("R1b: a poll of v2 does not replace the newly completed v1 from a list response", async () => {
  vi.useFakeTimers();
  const refresh = deferred<Awaited<ReturnType<HpApi["listArtifactVersions"]>>>();
  const v2 = { ...version("running"), artifact_version_id: "v2", version: 2 };
  const poll1 = deferred<{ version: HpArtifactVersion }>();
  const list = vi
    .fn()
    .mockResolvedValueOnce({ artifact, items: [version("running"), v2] })
    .mockReturnValueOnce(refresh.promise);
  const store = createArtifactStore({
    api: fakeApi({
      listArtifactVersions: list,
      getArtifactVersion: (id) => (id === "v1" ? poll1.promise : Promise.resolve({ version: v2 })),
    }),
  });
  try {
    await store.getState().loadArtifact("a1");
    const loading = store.getState().loadArtifact("a1", true);
    await vi.advanceTimersByTimeAsync(1000);
    refresh.resolve({ artifact, items: [version("completed"), v2] });
    await loading;
    expect(store.getState().versionsByArtifactId.a1?.[0]?.status).toBe("completed");
  } finally {
    store.getState().reset();
    vi.useRealTimers();
  }
});

it("R2: the same summary version accepts failed and a newer read accepts a legitimate retry", async () => {
  const list = vi
    .fn()
    .mockResolvedValueOnce({ items: [{ artifact, latest_version: version("running") }] })
    .mockResolvedValueOnce({ items: [{ artifact, latest_version: version("failed") }] })
    .mockResolvedValue({ items: [{ artifact, latest_version: version("running") }] });
  const store = createArtifactStore({
    api: fakeApi({ listMessageArtifacts: list }),
    sleep: () => new Promise(() => {}),
  });
  try {
    await store.getState().loadForMessage("m1");
    await store.getState().loadForMessage("m1", true);
    expect(store.getState().artifactsByMessageId.m1?.[0]?.latest_version?.status).toBe("failed");
    await store.getState().loadForMessage("m1", true);
    expect(store.getState().artifactsByMessageId.m1?.[0]?.latest_version?.status).toBe("running");
  } finally {
    store.getState().reset();
  }
});

it("R1: an older in-flight list may establish immutable completed HTML after a newer running poll", async () => {
  vi.useFakeTimers();
  const refresh = deferred<Awaited<ReturnType<HpApi["listArtifactVersions"]>>>();
  const list = vi
    .fn()
    .mockResolvedValueOnce({ artifact, items: [version("running")] })
    .mockReturnValueOnce(refresh.promise);
  const read = vi.fn(async () => ({ version: version("running") }));
  const store = createArtifactStore({
    api: fakeApi({ listArtifactVersions: list, getArtifactVersion: read }),
  });
  try {
    await store.getState().loadArtifact("a1");
    const pending = store.getState().loadArtifact("a1", true);
    await vi.advanceTimersByTimeAsync(1000);
    refresh.resolve({ artifact, items: [version("completed")] });
    await pending;
    expect(store.getState().versionsByArtifactId.a1?.[0]?.status).toBe("completed");
    await vi.advanceTimersByTimeAsync(10000);
    expect(read).toHaveBeenCalledTimes(1);
  } finally {
    store.getState().reset();
    vi.useRealTimers();
  }
});

it("R2: a late message GET cannot overwrite a newer terminal version GET", async () => {
  const late = deferred<Awaited<ReturnType<HpApi["listMessageArtifacts"]>>>();
  const store = createArtifactStore({ api: fakeApi({ listMessageArtifacts: () => late.promise }) });
  try {
    const pending = store.getState().loadForMessage("m1");
    await store.getState().loadArtifact("a1");
    late.resolve({ items: [{ artifact, latest_version: version("running") }] });
    await pending;
    expect(store.getState().artifactsByMessageId.m1?.[0]?.latest_version?.status).toBe("completed");
    expect(store.getState().buildingVersionIds).toEqual([]);
  } finally {
    store.getState().reset();
  }
});

it("R1/R2: an old running message response cannot override a newer failed GET", async () => {
  const late = deferred<Awaited<ReturnType<HpApi["listMessageArtifacts"]>>>();
  const store = createArtifactStore({
    api: fakeApi({
      listMessageArtifacts: () => late.promise,
      listArtifactVersions: async () => ({ artifact, items: [version("failed")] }),
    }),
  });
  try {
    const pending = store.getState().loadForMessage("m1");
    await store.getState().loadArtifact("a1");
    late.resolve({ items: [{ artifact, latest_version: version("running") }] });
    await pending;
    expect(store.getState().artifactsByMessageId.m1?.[0]?.latest_version?.status).toBe("failed");
  } finally {
    store.getState().reset();
  }
});

it("R2: lower-version summaries preserve a genuinely higher cached version", async () => {
  const v2 = { ...version("completed"), artifact_version_id: "v2", version: 2 };
  const store = createArtifactStore({
    api: fakeApi({
      listArtifactVersions: async () => ({ artifact, items: [v2] }),
      listMessageArtifacts: async () => ({
        items: [{ artifact, latest_version: version("completed") }],
      }),
    }),
  });
  try {
    await store.getState().loadArtifact("a1");
    await store.getState().loadForMessage("m1");
    expect(store.getState().artifactsByMessageId.m1?.[0]?.latest_version?.artifact_version_id).toBe(
      "v2",
    );
  } finally {
    store.getState().reset();
  }
});

it("R2: restored message rows converge after a network failure without opening an Inspector", async () => {
  vi.useFakeTimers();
  const read = vi
    .fn()
    .mockRejectedValueOnce(new TypeError("offline"))
    .mockResolvedValue({ version: version("completed") });
  const listVersions = vi.fn();
  const store = createArtifactStore({
    api: fakeApi({
      listMessageArtifacts: async () => ({
        items: [{ artifact, latest_version: version("running") }],
      }),
      listArtifactVersions: listVersions,
      getArtifactVersion: read,
    }),
  });
  try {
    await store.getState().loadForMessage("m1");
    await store.getState().loadForMessage("m1");
    await vi.advanceTimersByTimeAsync(1000);
    expect(store.getState().artifactsByMessageId.m1?.[0]?.latest_version?.status).toBe("running");
    expect(store.getState().queries.a1?.error).toContain("offline");
    await vi.advanceTimersByTimeAsync(2000);
    expect(store.getState().artifactsByMessageId.m1?.[0]?.latest_version?.status).toBe("completed");
    await vi.advanceTimersByTimeAsync(10000);
    expect(read).toHaveBeenCalledTimes(2);
    expect(listVersions).not.toHaveBeenCalled();
  } finally {
    store.getState().reset();
    vi.useRealTimers();
  }
});

it("R1: an accepted POST version survives an older list snapshot", async () => {
  const late = deferred<Awaited<ReturnType<HpApi["listArtifactVersions"]>>>();
  const created = { ...version("completed"), artifact_version_id: "v2", version: 2 };
  const store = createArtifactStore({
    api: fakeApi({
      listArtifactVersions: () => late.promise,
      createArtifact: async () => ({ artifact, version: created }),
    }),
  });
  try {
    const pending = store.getState().loadArtifact("a1");
    await store.getState().createArtifact("m1");
    late.resolve({ artifact, items: [version("completed")] });
    await pending;
    expect(store.getState().versionsByArtifactId.a1?.map((v) => v.artifact_version_id)).toEqual([
      "v1",
      "v2",
    ]);
  } finally {
    store.getState().reset();
  }
});

it("R1: a terminal cache stops a sleeping poller before another GET", async () => {
  vi.useFakeTimers();
  const read = vi.fn();
  const list = vi
    .fn()
    .mockResolvedValueOnce({ artifact, items: [version("running")] })
    .mockResolvedValue({ artifact, items: [version("completed")] });
  const store = createArtifactStore({
    api: fakeApi({ listArtifactVersions: list, getArtifactVersion: read }),
  });
  try {
    await store.getState().loadArtifact("a1");
    await store.getState().loadArtifact("a1", true);
    await vi.advanceTimersByTimeAsync(10000);
    expect(read).not.toHaveBeenCalled();
  } finally {
    store.getState().reset();
    vi.useRealTimers();
  }
});

it("R2: polling message-only builds has at most four requests in flight", async () => {
  vi.useFakeTimers();
  const releases: (() => void)[] = [];
  const read = vi.fn(
    (id: string) =>
      new Promise<{ version: HpArtifactVersion }>((resolve) => {
        releases.push(() =>
          resolve({ version: { ...version("completed"), artifact_version_id: id } }),
        );
      }),
  );
  const store = createArtifactStore({
    api: fakeApi({
      listMessageArtifacts: async () => ({
        items: Array.from({ length: 8 }, (_, i) => ({
          artifact: { ...artifact, artifact_id: `a${i}` },
          latest_version: {
            ...version("running"),
            artifact_id: `a${i}`,
            artifact_version_id: `v${i}`,
          },
        })),
      }),
      getArtifactVersion: read,
    }),
  });
  try {
    await store.getState().loadForMessage("m1");
    await vi.advanceTimersByTimeAsync(1000);
    expect(read).toHaveBeenCalledTimes(4);
    store.getState().reset();
    releases.forEach((release) => release());
    await vi.advanceTimersByTimeAsync(10000);
    expect(read).toHaveBeenCalledTimes(4);
    expect(store.getState().artifactsByMessageId).toEqual({});
  } finally {
    store.getState().reset();
    vi.useRealTimers();
  }
});
