import { describe, expect, it, vi } from "vitest";
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
