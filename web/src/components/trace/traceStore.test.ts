import { describe, expect, it, vi } from "vitest";
import { HpCommandError, type HpModelInputDetail } from "../../api/types";
import type { HpTraceTree } from "../../api/types";
import { createTraceStore } from "./traceStore";

const TREE: HpTraceTree = {
  run: {
    trace_run_id: "trace-1",
    run_id: "run-1",
    conversation_id: "conversation-1",
    strategy: "react",
    status: "completed",
    started_at: "2026-08-22T00:00:00Z",
    ended_at: "2026-08-22T00:00:01Z",
    metadata: {},
  },
  roots: [
    {
      event: {
        trace_event_id: "root",
        trace_run_id: "trace-1",
        parent_event_id: null,
        event_type: "agent",
        name: "AgentExecution",
        status: "completed",
        started_at: "2026-08-22T00:00:00Z",
        ended_at: "2026-08-22T00:00:01Z",
        duration_ms: 1000,
        metadata: {},
      },
      children: [],
    },
  ],
};

describe("traceStore", () => {
  it("merges ordered live start/end events without losing start metadata", () => {
    const store = createTraceStore({ getRunTrace: vi.fn() } as never);
    store.getState().followRun("run-1");
    store.getState().applyEvent("run-1", {
      action: "start",
      nodeId: "node-1",
      parentId: null,
      name: "LLMCall",
      nodeType: "llm",
      status: null,
      metadata: { model: "fast" },
      durationMs: null,
      occurredAt: "2026-08-22T00:00:00Z",
    });
    store.getState().applyEvent("run-1", {
      action: "end",
      nodeId: "node-1",
      parentId: null,
      name: null,
      nodeType: null,
      status: "completed",
      metadata: { token_usage: { total_tokens: 12 } },
      durationMs: 42,
      occurredAt: "2026-08-22T00:00:00.042Z",
    });

    expect(store.getState().nodes["node-1"]).toMatchObject({
      name: "LLMCall",
      status: "completed",
      durationMs: 42,
      metadata: { model: "fast", token_usage: { total_tokens: 12 } },
    });
    expect(store.getState().rootIds).toEqual(["node-1"]);
  });

  it("hydrates a persisted tree for refresh and historical runs", async () => {
    const getRunTrace = vi.fn().mockResolvedValue(TREE);
    const store = createTraceStore({ getRunTrace } as never);
    store.getState().followRun("run-1");
    await store.getState().loadTrace();

    expect(getRunTrace).toHaveBeenCalledWith("run-1");
    expect(store.getState().run?.status).toBe("completed");
    expect(store.getState().rootIds).toEqual(["root"]);
    expect(store.getState().selectedNodeId).toBe("root");
  });

  it("loads model input only on demand and caches fallback attempts independently", async () => {
    const getModelInput = vi.fn(async (snapshotId: string) => ({
      visibility: "summary",
      model_input: {
        snapshot_id: snapshotId,
        content_hash: `hash-${snapshotId}`,
        model_call_id: "call-1",
        phase: "decision",
        fallback_attempt: snapshotId === "snapshot-1" ? 1 : 2,
        endpoint_id: "endpoint",
        provider: "example",
        model: "model-a",
        api_format: "openai",
        created_at: "2026-08-22T00:00:00Z",
        message_count: 1,
        tool_count: 0,
      },
    }));
    const store = createTraceStore({ getRunTrace: vi.fn(), getModelInput } as never);

    expect(getModelInput).not.toHaveBeenCalled();
    await store.getState().loadModelInput("snapshot-1");
    await store.getState().loadModelInput("snapshot-2");
    await store.getState().loadModelInput("snapshot-1");

    expect(getModelInput).toHaveBeenCalledTimes(2);
    expect(store.getState().modelInputs["snapshot-1"]?.detail?.model_input.fallback_attempt).toBe(
      1,
    );
    expect(store.getState().modelInputs["snapshot-2"]?.detail?.model_input.fallback_attempt).toBe(
      2,
    );
  });
});

describe("UI-3 trace races", () => {
  const start = {
    action: "start" as const,
    nodeId: "root",
    parentId: null,
    name: "Agent",
    nodeType: "agent",
    status: null,
    metadata: { snapshot_id: "safe-snapshot" },
    durationMs: null,
    occurredAt: "2026-08-22T00:00:00Z",
  };
  it("invalidates a selected Trace request after closing or changing account", async () => {
    let resolve!: (tree: HpTraceTree) => void;
    const store = createTraceStore({
      getRunTrace: () =>
        new Promise<HpTraceTree>((r) => {
          resolve = r;
        }),
    } as never);
    store.getState().selectRun("run-1");
    const pending = store.getState().refreshSelectedRun("run-1");
    store.getState().reset();
    store.getState().selectRun("history");
    resolve(TREE);
    await pending;
    expect(store.getState().runId).toBe("history");
    expect(store.getState().nodes).toEqual({});
  });
  it("replays in-flight metadata without regressing terminal nodes", async () => {
    let resolve!: (tree: HpTraceTree) => void;
    const store = createTraceStore({
      getRunTrace: () =>
        new Promise<HpTraceTree>((r) => {
          resolve = r;
        }),
    } as never);
    store.getState().selectRun("run-1");
    const pending = store.getState().loadTrace();
    store.getState().applyEvent("run-1", start);
    store.getState().applyEvent("run-1", {
      ...start,
      action: "end",
      status: "completed",
      metadata: { token_usage: { total_tokens: 42 } },
      durationMs: 42,
    });
    store.getState().applyEvent("run-1", start);
    resolve(TREE);
    await pending;
    expect(store.getState().nodes.root).toMatchObject({
      status: "completed",
      durationMs: 42,
      metadata: { snapshot_id: "safe-snapshot", token_usage: { total_tokens: 42 } },
    });
  });
  it("keeps historical selection and invalidates model bodies during refresh", async () => {
    let resolve!: (v: unknown) => void;
    const store = createTraceStore({
      getModelInput: () =>
        new Promise((r) => {
          resolve = r;
        }),
    } as never);
    store.getState().selectRun("B");
    store.getState().applyEvent("A", start);
    expect(store.getState().runId).toBe("B");
    expect(store.getState().nodes).toEqual({});
    const pending = store.getState().loadModelInput("snapshot");
    store.getState().clearModelInputs();
    resolve({
      visibility: "full_safe",
      model_input: { provider_request_body: { secret: "synthetic" } },
    });
    await pending;
    expect(store.getState().modelInputs).toEqual({});
  });
  it("only commits the latest same-run snapshot request", async () => {
    const resolvers: Array<(v: HpTraceTree) => void> = [];
    const store = createTraceStore({
      getRunTrace: () => new Promise<HpTraceTree>((r) => resolvers.push(r)),
    } as never);
    store.getState().selectRun("run-1");
    const old = store.getState().loadTrace();
    const latest = store.getState().loadTrace();
    resolvers[1]!(TREE);
    await latest;
    resolvers[0]!({ ...TREE, roots: [] });
    await old;
    expect(store.getState().rootIds).toEqual(["root"]);
  });
  it("keeps buffered events across a superseding manual refresh", async () => {
    const resolvers: Array<(v: HpTraceTree) => void> = [];
    const store = createTraceStore({
      getRunTrace: () => new Promise<HpTraceTree>((r) => resolvers.push(r)),
    } as never);
    store.getState().selectRun("run-1");
    const old = store.getState().loadTrace();
    store.getState().applyEvent("run-1", {
      ...start,
      nodeId: "live-before-refresh",
      action: "end",
      status: "completed",
      metadata: { token_usage: { total_tokens: 17 } },
    });
    const next = store.getState().loadTrace();
    resolvers[1]!(TREE);
    await next;
    resolvers[0]!(TREE);
    await old;
    expect(store.getState().nodes["live-before-refresh"]).toMatchObject({
      status: "completed",
      metadata: { token_usage: { total_tokens: 17 } },
    });
  });
});

describe("model input permission boundaries", () => {
  function detail(id: string, visibility: "summary" | "full_safe"): HpModelInputDetail {
    return {
      visibility,
      model_input: {
        snapshot_id: id,
        content_hash: "synthetic-hash",
        model_call_id: "synthetic-call",
        phase: "decision",
        fallback_attempt: 0,
        endpoint_id: "synthetic-endpoint",
        provider: "synthetic-provider",
        model: "synthetic-model",
        api_format: "openai",
        created_at: "2026-10-07T00:00:00Z",
        message_count: 1,
        tool_count: 0,
        ...(visibility === "full_safe"
          ? { provider_request_body: { messages: ["synthetic body"] } }
          : {}),
      },
    };
  }
  it.each(["summary", "full_safe"] as const)(
    "clears loaded bodies and ignores in-flight responses after denial, then reloads %s through new requests",
    async (visibility) => {
      let resolve!: (value: HpModelInputDetail) => void;
      const getModelInput = vi
        .fn()
        .mockResolvedValueOnce(detail("first", "full_safe"))
        .mockImplementationOnce(
          () =>
            new Promise<HpModelInputDetail>((r) => {
              resolve = r;
            }),
        )
        .mockRejectedValueOnce(
          new HpCommandError(403, {
            code: "model_input_unavailable",
            message: "denied",
            request_id: null,
            retryable: false,
            details: {},
          }),
        )
        .mockResolvedValueOnce(detail("first", visibility))
        .mockResolvedValueOnce(detail("late", visibility));
      const store = createTraceStore({ getModelInput } as never);
      store.getState().selectRun("A");
      await store.getState().loadModelInput("first");
      const late = store.getState().loadModelInput("late");
      await store.getState().loadModelInput("denied");
      expect(store.getState().modelInputs).toEqual({ denied: { status: "unavailable" } });
      await store.getState().loadModelInput("first");
      await store.getState().loadModelInput("late");
      expect(getModelInput).toHaveBeenCalledTimes(5);
      resolve(detail("late", "full_safe"));
      await late;
      expect(store.getState().modelInputs.first?.detail?.visibility).toBe(visibility);
      expect(store.getState().modelInputs.late?.detail?.visibility).toBe(visibility);
      expect(store.getState().modelInputs.denied).toEqual({ status: "unavailable" });
      if (visibility === "summary") {
        expect(
          Object.values(store.getState().modelInputs).some(
            (value) => value.detail?.model_input.provider_request_body,
          ),
        ).toBe(false);
      }
    },
  );
  it("treats a missing snapshot 404 locally without discarding other bodies or requests", async () => {
    let resolve!: (value: HpModelInputDetail) => void;
    const getModelInput = vi
      .fn()
      .mockResolvedValueOnce(detail("first", "full_safe"))
      .mockImplementationOnce(
        () =>
          new Promise<HpModelInputDetail>((r) => {
            resolve = r;
          }),
      )
      .mockRejectedValueOnce(
        new HpCommandError(404, {
          code: "resource_not_found",
          message: "missing",
          request_id: null,
          retryable: false,
          details: {},
        }),
      );
    const store = createTraceStore({ getModelInput } as never);
    store.getState().selectRun("A");
    await store.getState().loadModelInput("first");
    const late = store.getState().loadModelInput("late");
    await store.getState().loadModelInput("missing");
    resolve(detail("late", "full_safe"));
    await late;
    expect(store.getState().modelInputs.missing).toEqual({ status: "unavailable" });
    expect(
      store.getState().modelInputs.first?.detail?.model_input.provider_request_body,
    ).toBeDefined();
    expect(
      store.getState().modelInputs.late?.detail?.model_input.provider_request_body,
    ).toBeDefined();
  });
});
