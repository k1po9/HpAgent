/**
 * Run feed tests (contract §12.2–§12.5, §13.2).
 *
 * Covers: snapshot + online deltas/progress ordering, event_id dedup,
 * out-of-order buffering within a stream, unfillable-gap / stream-change
 * degradation, control events (stream.degraded / auth.expired), connect
 * failures, and the clean-EOF fallback to polling.
 */
import { describe, expect, it } from "vitest";
import { openRunFeed, type RunFeedHandlers, type RunProgress } from "./runFeed";
import type { HpRunSnapshot } from "../api/types";

const ENCODER = new TextEncoder();

/** Build one contract-format SSE frame. */
function frame(eventType: string, eventId: string, data: unknown): string {
  return `id: ${eventId}\nevent: ${eventType}\ndata: ${JSON.stringify(data)}\n\n`;
}

function envelope(
  eventType: string,
  eventSeq: number,
  streamId: string | null,
  payload: Record<string, unknown>,
): string {
  return frame(eventType, `ev-${eventSeq}`, {
    schema_version: 1,
    event_id: `ev-${eventSeq}`,
    event_type: eventType,
    conversation_id: "c1",
    run_id: "r1",
    message_id: eventType === "message.delta" ? "am-1" : null,
    stream_id: streamId,
    event_seq: eventSeq,
    occurred_at: "2026-08-08T00:00:00Z",
    payload,
  });
}

function snapshotFrame(snapshot: HpRunSnapshot): string {
  return frame("run.snapshot", "snap-1", {
    schema_version: 1,
    event_id: "snap-1",
    event_type: "run.snapshot",
    conversation_id: "c1",
    run_id: "r1",
    message_id: null,
    stream_id: null,
    event_seq: null,
    occurred_at: "2026-08-08T00:00:00Z",
    payload: { snapshot },
  });
}

function terminalFrame(eventType: string, snapshot: HpRunSnapshot): string {
  return frame(eventType, `term-1`, {
    schema_version: 1,
    event_id: "term-1",
    event_type: eventType,
    conversation_id: "c1",
    run_id: "r1",
    message_id: snapshot.assistant_message.message_id,
    stream_id: null,
    event_seq: null,
    occurred_at: "2026-08-08T00:00:00Z",
    payload: { snapshot },
  });
}

const STREAM_ID = "str-1";

const RUNNING_SNAPSHOT: HpRunSnapshot = {
  run: {
    run_id: "r1",
    conversation_id: "c1",
    session_id: "s1",
    trigger_message_id: "um-1",
    retry_of_run_id: null,
    agent_strategy: "react",
    status: "running",
    failure: null,
    version: 1,
    created_at: "2026-08-08T00:00:00Z",
    started_at: null,
    finished_at: null,
    updated_at: "2026-08-08T00:00:00Z",
  },
  assistant_message: {
    message_id: "am-1",
    conversation_id: "c1",
    role: "assistant",
    status: "pending",
    content: "",
    sequence: 2,
    client_request_id: null,
    produced_by_run_id: "r1",
    created_at: "2026-08-08T00:00:00Z",
    completed_at: null,
  },
};

const COMPLETED_SNAPSHOT: HpRunSnapshot = {
  run: { ...RUNNING_SNAPSHOT.run, status: "completed", finished_at: "2026-08-08T00:01:00Z" },
  assistant_message: {
    ...RUNNING_SNAPSHOT.assistant_message,
    status: "completed",
    content: "完整的最终回复",
    completed_at: "2026-08-08T00:01:00Z",
  },
};

function manualStream(): {
  response: Response;
  send: (text: string) => void;
  close: () => void;
} {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c;
    },
  });
  return {
    response: new Response(body, {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    }),
    send: (text) => controller.enqueue(ENCODER.encode(text)),
    close: () => controller.close(),
  };
}

interface FeedProbe {
  calls: Array<{ type: string; value?: unknown }>;
  handlers: RunFeedHandlers;
}

function probe(): FeedProbe {
  const calls: Array<{ type: string; value?: unknown }> = [];
  const handlers: RunFeedHandlers = {
    onSnapshot: (s) => calls.push({ type: "snapshot", value: s }),
    onDelta: (id, delta) => calls.push({ type: "delta", value: { id, delta } }),
    onProgress: (p) => calls.push({ type: "progress", value: p }),
    onStatus: (s) => calls.push({ type: "status", value: s }),
    onTrace: (event) => calls.push({ type: "trace", value: event }),
    onTerminal: (s) => calls.push({ type: "terminal", value: s }),
    onDegraded: (r) => calls.push({ type: "degraded", value: r }),
    onAuthExpired: () => calls.push({ type: "auth" }),
  };
  return { calls, handlers };
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const deltas = (probe: FeedProbe) =>
  probe.calls.filter((c) => c.type === "delta").map((c) => (c.value as { delta: string }).delta);

describe("openRunFeed", () => {
  it("routes snapshot, ordered deltas, progress, status and the terminal snapshot", async () => {
    const p = probe();
    const stream = manualStream();
    const feed = openRunFeed("r1", p.handlers, { fetchImpl: async () => stream.response });

    stream.send(snapshotFrame(RUNNING_SNAPSHOT));
    stream.send(envelope("run.started", 1, STREAM_ID, { status: "running" }));
    stream.send(envelope("message.delta", 2, STREAM_ID, { delta: "你" }));
    stream.send(
      envelope("run.progress", 3, STREAM_ID, {
        phase: "executing_tool",
        summary: "正在分析项目文件",
      }),
    );
    stream.send(envelope("message.delta", 4, STREAM_ID, { delta: "好" }));
    stream.send(
      envelope("trace.event", 5, STREAM_ID, {
        action: "start",
        node_id: "node-1",
        parent_id: null,
        name: "AgentExecution",
        type: "agent",
        metadata: { strategy: "react" },
      }),
    );
    stream.send(terminalFrame("run.completed", COMPLETED_SNAPSHOT));
    await feed.done;

    const types = p.calls.map((c) => c.type);
    expect(types).toContain("snapshot");
    expect(deltas(p)).toEqual(["你", "好"]);
    expect(p.calls.find((c) => c.type === "progress")?.value).toMatchObject({
      phase: "executing_tool",
      summary: "正在分析项目文件",
    } as RunProgress);
    expect(p.calls.find((c) => c.type === "trace")?.value).toMatchObject({
      action: "start",
      nodeId: "node-1",
      name: "AgentExecution",
      nodeType: "agent",
    });
    const terminal = p.calls.find((c) => c.type === "terminal")?.value as HpRunSnapshot;
    expect(terminal.run.status).toBe("completed");
    expect(terminal.assistant_message.content).toBe("完整的最终回复");
  });

  it("dedups by event_id even when the same frame is delivered twice", async () => {
    const p = probe();
    const stream = manualStream();
    const feed = openRunFeed("r1", p.handlers, { fetchImpl: async () => stream.response });

    const frame = envelope("message.delta", 1, STREAM_ID, { delta: "你" });
    stream.send(frame);
    stream.send(frame); // duplicate terminal-publisher style redelivery
    stream.send(envelope("message.delta", 2, STREAM_ID, { delta: "好" }));
    stream.close();
    await feed.done;

    expect(deltas(p)).toEqual(["你", "好"]);
  });

  it("buffers reordered deltas within a stream and flushes them once the gap fills", async () => {
    const p = probe();
    const stream = manualStream();
    const feed = openRunFeed("r1", p.handlers, { fetchImpl: async () => stream.response });

    stream.send(envelope("message.delta", 1, STREAM_ID, { delta: "一" }));
    await tick();
    stream.send(envelope("message.delta", 3, STREAM_ID, { delta: "三" })); // reordered
    await tick();
    expect(deltas(p)).toEqual(["一"]); // seq3 held back
    stream.send(envelope("message.delta", 2, STREAM_ID, { delta: "二" })); // fills the gap
    await tick();
    expect(deltas(p)).toEqual(["一", "二", "三"]);
    stream.close();
    await feed.done;
  });

  it("degrades permanently on an unfillable sequence gap and stops delta assembly", async () => {
    const p = probe();
    const stream = manualStream();
    const feed = openRunFeed("r1", p.handlers, { fetchImpl: async () => stream.response });

    stream.send(envelope("message.delta", 1, STREAM_ID, { delta: "一" }));
    await tick();
    stream.send(envelope("message.delta", 100, STREAM_ID, { delta: "跳" })); // jump > window
    await tick();
    expect(p.calls).toContainEqual({ type: "degraded", value: "sequence_gap" });
    // After degradation the feed aborts the connection, so further deltas can
    // never reach the handler — delta assembly stays stopped.
    await feed.done;
    expect(deltas(p)).toEqual(["一"]);
  });

  it("degrades immediately when the stream_id changes mid-flight", async () => {
    const p = probe();
    const stream = manualStream();
    const feed = openRunFeed("r1", p.handlers, { fetchImpl: async () => stream.response });

    stream.send(envelope("message.delta", 1, STREAM_ID, { delta: "一" }));
    await tick();
    stream.send(envelope("message.delta", 2, "str-2", { delta: "x" })); // different sink lifetime
    await tick();
    expect(p.calls).toContainEqual({ type: "degraded", value: "sequence_gap" });
    await feed.done;
  });

  it("surfaces the stable reason from a stream.degraded control event", async () => {
    const p = probe();
    const stream = manualStream();
    const feed = openRunFeed("r1", p.handlers, { fetchImpl: async () => stream.response });

    stream.send(
      frame("stream.degraded", "degr-1", {
        schema_version: 1,
        event_id: "degr-1",
        event_type: "stream.degraded",
        conversation_id: "c1",
        run_id: "r1",
        message_id: null,
        stream_id: null,
        event_seq: null,
        occurred_at: "2026-08-08T00:00:00Z",
        payload: {
          reason: "handshake_buffer_overflow",
          recovery: "query_run",
          retry_after_ms: 2000,
        },
      }),
    );
    await tick();
    expect(p.calls).toContainEqual({ type: "degraded", value: "handshake_buffer_overflow" });
    await feed.done;
  });

  it("reports auth.expired and stops", async () => {
    const p = probe();
    const stream = manualStream();
    const feed = openRunFeed("r1", p.handlers, { fetchImpl: async () => stream.response });

    stream.send(
      frame("auth.expired", "auth-1", {
        schema_version: 1,
        event_id: "auth-1",
        event_type: "auth.expired",
        conversation_id: "c1",
        run_id: "r1",
        message_id: null,
        stream_id: null,
        event_seq: null,
        occurred_at: "2026-08-08T00:00:00Z",
        payload: {},
      }),
    );
    await feed.done;
    expect(p.calls).toContainEqual({ type: "auth" });
  });

  it("treats a 401 connect failure as an expired session", async () => {
    const p = probe();
    const response = new Response(JSON.stringify({ error: { code: "unauthenticated" } }), {
      status: 401,
      headers: { "Content-Type": "application/json" },
    });
    const feed = openRunFeed("r1", p.handlers, { fetchImpl: async () => response });
    await feed.done;
    expect(p.calls).toContainEqual({ type: "auth" });
  });

  it("falls back to degraded when the stream ends cleanly without a terminal decision", async () => {
    const p = probe();
    const stream = manualStream();
    const feed = openRunFeed("r1", p.handlers, { fetchImpl: async () => stream.response });

    stream.send(snapshotFrame(RUNNING_SNAPSHOT));
    stream.send(envelope("message.delta", 1, STREAM_ID, { delta: "一" }));
    stream.close(); // server drops the connection without a terminal event
    await feed.done;

    expect(p.calls).toContainEqual({ type: "degraded", value: "upstream_disconnected" });
    expect(feed.degraded).toBe(true);
  });

  it("ignores unknown event types without degrading", async () => {
    const p = probe();
    const stream = manualStream();
    const feed = openRunFeed("r1", p.handlers, { fetchImpl: async () => stream.response });

    stream.send(envelope("some.future.event", 1, STREAM_ID, {}));
    await tick();
    expect(p.calls).toHaveLength(0);
    stream.close();
    await feed.done;
  });
});
