/**
 * Workbench store tests (phase-e test strategy):
 * double-click guard, send → authoritative replace, pagination dedup,
 * conversation_busy recovery, retry, and run polling until terminal.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiClient } from "../api/client";
import { HpApi } from "../api/resources";
import type { HpConversation, HpMessage, HpRun, HpRunSnapshot } from "../api/types";
import { createWorkbenchStore, isRetryableRun } from "./workbench";

const ENCODER = new TextEncoder();

/** A manually-driven SSE body so tests can emit frames deterministically. */
function sseChannel(): {
  response: Response;
  send: (eventType: string, eventId: string, data: unknown) => void;
  sendRaw: (text: string) => void;
  close: () => void;
} {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c;
    },
  });
  const sendRaw = (text: string) => controller.enqueue(ENCODER.encode(text));
  return {
    response: new Response(body, {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    }),
    send: (eventType, eventId, data) =>
      sendRaw(`id: ${eventId}\nevent: ${eventType}\ndata: ${JSON.stringify(data)}\n\n`),
    sendRaw,
    close: () => controller.close(),
  };
}

const OK = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

function message(overrides: Partial<HpMessage>): HpMessage {
  return {
    message_id: overrides.message_id ?? "m",
    conversation_id: "c1",
    role: "assistant",
    status: "completed",
    content: "hello",
    sequence: 1,
    client_request_id: null,
    produced_by_run_id: null,
    created_at: "2026-08-08T00:00:00Z",
    completed_at: null,
    ...overrides,
  };
}

function run(overrides: Partial<HpRun>): HpRun {
  return {
    run_id: overrides.run_id ?? "r1",
    conversation_id: "c1",
    session_id: "s1",
    trigger_message_id: "um1",
    retry_of_run_id: null,
    status: overrides.status ?? "queued",
    failure: null,
    version: 1,
    created_at: "2026-08-08T00:00:00Z",
    started_at: null,
    finished_at: null,
    updated_at: "2026-08-08T00:00:00Z",
    budget: overrides.budget ?? null,
    ...overrides,
    agent_strategy: overrides.agent_strategy ?? "react",
  };
}

function conversation(overrides: Partial<HpConversation>): HpConversation {
  return {
    conversation_id: overrides.conversation_id ?? "c1",
    title: "测试对话",
    status: "active",
    last_message_seq: 0,
    metadata_version: 1,
    created_at: "2026-08-08T00:00:00Z",
    updated_at: "2026-08-08T00:00:00Z",
    ...overrides,
  };
}

interface FakeBackend {
  calls: Array<{ method: string; url: string }>;
  state: {
    postCount: Record<string, number>;
    runSnapshots: Record<string, HpRunSnapshot>;
  };
  fetchMock: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
}

function makeBackend(overrides: Partial<FakeBackend["state"]> = {}): FakeBackend {
  const state: FakeBackend["state"] = {
    postCount: {},
    runSnapshots: {},
    ...overrides,
  };
  const calls: FakeBackend["calls"] = [];
  const fetchMock: FakeBackend["fetchMock"] = async (input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    calls.push({ method, url });

    if (url === "/api/v1/me") {
      return OK({ csrf_token: "t" });
    }
    if (method === "POST" && url === "/api/v1/conversations") {
      return OK({ conversation: conversation({ conversation_id: "c2", title: "新对话" }) });
    }
    if (url === "/api/v1/conversations") {
      return OK({ items: [conversation({})], next_cursor: null, has_more: false });
    }
    const detailMatch = url.match(/^\/api\/v1\/conversations\/([^/]+)$/);
    if (method === "GET" && detailMatch) {
      return OK({
        conversation: conversation({ conversation_id: detailMatch[1] }),
        active_run: null,
      });
    }
    const sendMatch = url.match(/^\/api\/v1\/conversations\/([^/]+)\/messages$/);
    if (method === "POST" && sendMatch) {
      const id = sendMatch[1] ?? "c1";
      const key = `send:${id}`;
      state.postCount[key] = (state.postCount[key] ?? 0) + 1;
      const body = JSON.parse(String(init?.body)) as { content: string };
      const userMessage = message({
        message_id: `um-${state.postCount[key]}`,
        conversation_id: id,
        role: "user",
        status: "accepted",
        content: body.content,
        sequence: 1,
      });
      const assistantMessage = message({
        message_id: "am-1",
        conversation_id: id,
        role: "assistant",
        status: "pending",
        content: "",
        sequence: 2,
        produced_by_run_id: "r1",
      });
      const runObj = run({
        run_id: "r1",
        status: "queued",
        trigger_message_id: userMessage.message_id,
      });
      return OK({
        user_message: userMessage,
        assistant_message: assistantMessage,
        run: runObj,
        events_url: `/api/v1/runs/r1/events`,
      });
    }
    const listMatch = url.match(/^\/api\/v1\/conversations\/([^/]+)\/messages\?/);
    if (method === "GET" && listMatch) {
      return OK({
        items: [],
        next_cursor: null,
        has_more: false,
        conversation_last_message_seq: 0,
      });
    }
    const runMatch = url.match(/^\/api\/v1\/runs\/([^/]+)$/);
    if (method === "GET" && runMatch) {
      const snapshot = state.runSnapshots[runMatch[1] ?? ""];
      if (snapshot) return OK(snapshot);
      return OK({
        run: run({ run_id: runMatch[1], status: "completed" }),
        assistant_message: message({
          message_id: "am-1",
          role: "assistant",
          status: "completed",
          content: "completed answer",
          sequence: 2,
          produced_by_run_id: runMatch[1],
        }),
      });
    }
    const cancelMatch = url.match(/^\/api\/v1\/runs\/([^/]+)\/cancel$/);
    if (method === "POST" && cancelMatch) {
      return OK({
        run: run({
          run_id: cancelMatch[1],
          status: "cancelled",
          finished_at: "2026-08-08T00:00:01Z",
        }),
        assistant_message: message({
          message_id: "am-1",
          role: "assistant",
          status: "aborted",
          content: "partial",
          produced_by_run_id: cancelMatch[1],
        }),
      });
    }
    const retryMatch = url.match(/^\/api\/v1\/runs\/([^/]+)\/retry$/);
    if (method === "POST" && retryMatch) {
      return OK({
        source_run_id: retryMatch[1],
        assistant_message: message({
          message_id: "am-2",
          role: "assistant",
          status: "pending",
          content: "",
          produced_by_run_id: "r2",
        }),
        run: run({ run_id: "r2", status: "queued", retry_of_run_id: retryMatch[1] }),
        events_url: `/api/v1/runs/r2/events`,
      });
    }
    return OK({ items: [], next_cursor: null, has_more: false });
  };
  return { calls, state, fetchMock };
}

type BoundStore = ReturnType<typeof createWorkbenchStore>;

function makeStore(backend: FakeBackend): BoundStore {
  const client = new ApiClient(backend.fetchMock);
  // The SSE feed uses fetch directly (not the ApiClient), so it must be swapped
  // too — otherwise tests would hit the real network.
  return createWorkbenchStore({ api: new HpApi(client), fetchImpl: backend.fetchMock });
}

describe("workbench store", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("guards against double-send: only one POST, second call returns false", async () => {
    const backend = makeBackend();
    const store = makeStore(backend);
    await store.getState().loadConversations();
    await store.getState().selectConversation("c1");

    const first = store.getState().sendMessage("你好");
    const second = store.getState().sendMessage("又一条"); // ignored while sending
    expect(await second).toBe(false);
    expect(await first).toBe(true);

    const posts = backend.calls.filter((c) => c.method === "POST" && c.url.endsWith("/messages"));
    expect(posts).toHaveLength(1);
    expect(store.getState().activeRun?.run_id).toBe("r1");
  });

  it("replaces the temp user message with authoritative ids and starts a run", async () => {
    const backend = makeBackend();
    const store = makeStore(backend);
    await store.getState().loadConversations();
    await store.getState().selectConversation("c1");

    const ok = await store.getState().sendMessage("你好");
    expect(ok).toBe(true);
    expect(store.getState().messages.map((m) => m.message_id)).toEqual(["um-1", "am-1"]);
    expect(store.getState().messages[0]).toMatchObject({ role: "user", content: "你好" });
    expect(store.getState().activeRun?.run_id).toBe("r1");
    expect(store.getState().sending).toBe(false);
  });

  it("uploads attachments, binds ready file ids to the message, and clears them on success", async () => {
    const backend = makeBackend();
    const original = backend.fetchMock;
    let sentBody: { content?: string; file_ids?: string[] } | null = null;
    backend.fetchMock = async (input, init) => {
      const url = String(input);
      if (init?.method === "POST" && url === "/api/v1/conversations/c1/uploads") {
        return OK({
          file: {
            file_id: "f1",
            conversation_id: "c1",
            file_name: "notes.txt",
            content_type: "text/plain",
            size_bytes: 5,
            status: "pending",
            created_at: "2026-08-08T00:00:00Z",
          },
          content_url: "/api/v1/files/f1/content",
        });
      }
      if (init?.method === "PUT" && url === "/api/v1/files/f1/content") {
        expect(init.body).toBeInstanceOf(File);
        return OK({
          file: {
            file_id: "f1",
            conversation_id: "c1",
            file_name: "notes.txt",
            content_type: "text/plain",
            size_bytes: 5,
            status: "ready",
            created_at: "2026-08-08T00:00:00Z",
          },
        });
      }
      if (init?.method === "POST" && url.endsWith("/messages")) {
        sentBody = JSON.parse(String(init.body)) as typeof sentBody;
      }
      return original(input, init);
    };
    const store = makeStore(backend);
    await store.getState().loadConversations();
    await store.getState().selectConversation("c1");

    await store.getState().addAttachments([new File(["hello"], "notes.txt")]);
    expect(store.getState().attachments).toMatchObject([
      { name: "notes.txt", fileId: "f1", status: "ready" },
    ]);

    expect(await store.getState().sendMessage("分析附件")).toBe(true);
    expect(sentBody).toMatchObject({ content: "分析附件", file_ids: ["f1"] });
    expect(store.getState().attachments).toEqual([]);
  });

  it("blocks sending while an attachment failed and deletes it when removed", async () => {
    const backend = makeBackend();
    const original = backend.fetchMock;
    backend.fetchMock = async (input, init) => {
      const url = String(input);
      if (init?.method === "POST" && url.endsWith("/uploads")) {
        return OK({
          file: {
            file_id: "f-bad",
            conversation_id: "c1",
            file_name: "bad.txt",
            content_type: "text/plain",
            size_bytes: 3,
            status: "pending",
            created_at: "2026-08-08T00:00:00Z",
          },
          content_url: "/api/v1/files/f-bad/content",
        });
      }
      if (init?.method === "PUT" && url === "/api/v1/files/f-bad/content") {
        return OK(
          {
            error: {
              code: "file_content_rejected",
              message: "文件内容不受支持。",
              request_id: null,
              retryable: false,
              details: {},
            },
          },
          422,
        );
      }
      if (init?.method === "DELETE" && url === "/api/v1/files/f-bad") {
        return new Response(null, { status: 204 });
      }
      return original(input, init);
    };
    const store = makeStore(backend);
    await store.getState().loadConversations();
    await store.getState().selectConversation("c1");

    await store.getState().addAttachments([new File(["bad"], "bad.txt")]);
    expect(store.getState().attachments[0]).toMatchObject({
      fileId: "f-bad",
      status: "failed",
    });
    expect(await store.getState().sendMessage("不要发送")).toBe(false);
    expect(backend.calls.filter((call) => call.url.endsWith("/messages"))).toHaveLength(0);

    const localId = store.getState().attachments[0]?.localId;
    expect(localId).toBeTruthy();
    await store.getState().removeAttachment(localId as string);
    expect(store.getState().attachments).toEqual([]);
  });

  it("does not duplicate messages when pagination pages overlap", async () => {
    const backend = makeBackend();
    // Each API page is already oldest-first. The older page re-returns the
    // cursor boundary from the current page, which must also be deduplicated.
    const orig = backend.fetchMock;
    let page = 1;
    backend.fetchMock = async (input, init) => {
      const url = String(input);
      if (url.startsWith("/api/v1/conversations/c1/messages")) {
        if (page === 1) {
          page += 1;
          return OK({
            items: [
              message({ message_id: "m-3", sequence: 3 }),
              message({ message_id: "m-4", sequence: 4 }),
            ],
            next_cursor: "older",
            has_more: true,
            conversation_last_message_seq: 4,
          });
        }
        return OK({
          items: [
            message({ message_id: "m-1", sequence: 1 }),
            message({ message_id: "m-2", sequence: 2 }),
            message({ message_id: "m-3", sequence: 3 }),
          ],
          next_cursor: null,
          has_more: false,
          conversation_last_message_seq: 4,
        });
      }
      return orig(input, init);
    };
    const store = makeStore(backend);
    await store.getState().loadConversations();
    await store.getState().selectConversation("c1");
    expect(store.getState().messages.map((m) => m.message_id)).toEqual(["m-3", "m-4"]);

    await store.getState().loadMoreMessages();
    const ids = store.getState().messages.map((m) => m.message_id);
    expect(ids).toEqual(["m-1", "m-2", "m-3", "m-4"]);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("surfaces conversation_busy from the backend without losing the draft", async () => {
    const backend = makeBackend();
    const orig = backend.fetchMock;
    backend.fetchMock = async (input, init) => {
      const url = String(input);
      if (url.includes("/messages") && init?.method === "POST") {
        return OK(
          {
            error: {
              code: "conversation_busy",
              message: "当前对话仍有请求正在执行。",
              request_id: null,
              retryable: false,
              details: {},
            },
          },
          409,
        );
      }
      return orig(input, init);
    };
    const store = makeStore(backend);
    await store.getState().loadConversations();
    await store.getState().selectConversation("c1");

    const ok = await store.getState().sendMessage("你好");
    expect(ok).toBe(false);
    // The temp message is removed; the draft lives in the composer, not here.
    expect(store.getState().messages).toHaveLength(0);
    expect(store.getState().activeRunError).not.toBeNull();
    expect(store.getState().error).toBeNull();
  });

  it("retries a failed run with a fresh idempotency key and new assistant message", async () => {
    const backend = makeBackend();
    const store = makeStore(backend);
    await store.getState().loadConversations();
    await store.getState().selectConversation("c1");
    await store.getState().sendMessage("你好");
    // The backend marks the run failed.
    store.setState({
      activeRun: run({
        run_id: "r1",
        status: "failed",
        failure: { code: "run_failed", message: "boom", retryable: true },
      }),
    });

    await store.getState().retryRun();
    expect(store.getState().activeRun?.run_id).toBe("r2");
    expect(store.getState().activeRun?.status).toBe("queued");
    expect(store.getState().messages.some((m) => m.message_id === "am-2")).toBe(true);
  });

  it("does not call retry for an unsafe failed run", async () => {
    const backend = makeBackend();
    const store = makeStore(backend);
    await store.getState().loadConversations();
    await store.getState().selectConversation("c1");
    store.setState({
      activeRun: run({
        run_id: "r-unsafe",
        status: "failed",
        failure: {
          code: "tool_side_effect_uncertain",
          message: "provider outcome is unknown",
          retryable: false,
        },
      }),
    });

    await store.getState().retryRun();
    expect(backend.calls.filter((call) => call.url.endsWith("/retry"))).toHaveLength(0);
    expect(store.getState().activeRun?.run_id).toBe("r-unsafe");
  });

  it("does not consider a cancelled run retryable", async () => {
    const backend = makeBackend();
    const store = makeStore(backend);
    await store.getState().loadConversations();
    await store.getState().selectConversation("c1");
    const cancelled = run({ run_id: "r-cancelled", status: "cancelled" });
    store.setState({ activeRun: cancelled });

    expect(isRetryableRun(cancelled)).toBe(false);
    expect(
      isRetryableRun(
        run({
          status: "failed",
          failure: { code: "run_failed", message: "boom", retryable: true },
        }),
      ),
    ).toBe(true);
    expect(
      isRetryableRun(
        run({
          status: "failed",
          failure: { code: "tool_side_effect_uncertain", message: "unknown", retryable: false },
        }),
      ),
    ).toBe(false);
    await store.getState().retryRun();
    expect(backend.calls.filter((call) => call.url.endsWith("/retry"))).toHaveLength(0);
  });

  it("polls a running run until terminal and reconciles the assistant message", async () => {
    const backend = makeBackend();
    const store = makeStore(backend);
    await store.getState().loadConversations();
    await store.getState().selectConversation("c1");
    await store.getState().sendMessage("你好");
    expect(store.getState().activeRun?.status).toBe("queued");

    await vi.advanceTimersByTimeAsync(1000); // first poll
    // backend default: completed snapshot
    expect(store.getState().activeRun?.status).toBe("completed");
    const assistant = store.getState().messages.find((m) => m.message_id === "am-1");
    expect(assistant?.content).toBe("completed answer");
    expect(store.getState().polling).toBe(false);
  });

  it("allows a follow-up send after the Run completes (long conversation)", async () => {
    const backend = makeBackend();
    const store = makeStore(backend);
    await store.getState().loadConversations();
    await store.getState().selectConversation("c1");
    await store.getState().sendMessage("你好");
    await vi.advanceTimersByTimeAsync(1000); // first poll resolves the completed snapshot
    expect(store.getState().activeRun?.status).toBe("completed");

    const posts = () =>
      backend.calls.filter((c) => c.method === "POST" && c.url.endsWith("/messages"));
    const before = posts().length;
    // A terminal Run must NOT gate the composer: the next turn can start.
    const ok = await store.getState().sendMessage("又一条");
    expect(ok).toBe(true);
    expect(posts().length).toBe(before + 1);
  });

  // ---- E-06 SSE integration -------------------------------------------------

  /** Build one contract envelope (run_id pinned to r1). */
  function env(
    eventType: string,
    eventId: string,
    opts: {
      messageId?: string | null;
      streamId?: string | null;
      eventSeq?: number | null;
      payload?: Record<string, unknown>;
    } = {},
  ): unknown {
    return {
      schema_version: 1,
      event_id: eventId,
      event_type: eventType,
      conversation_id: "c1",
      run_id: "r1",
      message_id: opts.messageId ?? null,
      stream_id: opts.streamId ?? null,
      event_seq: opts.eventSeq ?? null,
      occurred_at: "2026-08-08T00:00:00Z",
      payload: opts.payload ?? {},
    };
  }

  /** A backend whose events URL answers from a hand-driven SSE channel. */
  function makeSseBackend(channel: ReturnType<typeof sseChannel>): FakeBackend {
    const base = makeBackend();
    const orig = base.fetchMock;
    base.fetchMock = async (input, init) => {
      if (/\/events$/.test(String(input))) return channel.response;
      return orig(input, init);
    };
    return base;
  }

  it("streams SSE deltas and progress, then resolves on the terminal snapshot", async () => {
    const channel = sseChannel();
    const backend = makeSseBackend(channel);
    const store = makeStore(backend);
    await store.getState().loadConversations();
    await store.getState().selectConversation("c1");
    await store.getState().sendMessage("你好");
    const assistantId = "am-1";

    channel.send(
      "run.snapshot",
      "s1",
      env("run.snapshot", "s1", {
        payload: {
          snapshot: {
            run: run({ run_id: "r1", status: "running" }),
            assistant_message: message({
              message_id: assistantId,
              status: "pending",
              content: "",
              produced_by_run_id: "r1",
            }),
          },
        },
      }),
    );
    await vi.advanceTimersByTimeAsync(0);

    channel.send(
      "message.delta",
      "e1",
      env("message.delta", "e1", {
        messageId: assistantId,
        streamId: "str-1",
        eventSeq: 1,
        payload: { delta: "你" },
      }),
    );
    channel.send(
      "message.delta",
      "e2",
      env("message.delta", "e2", {
        messageId: assistantId,
        streamId: "str-1",
        eventSeq: 2,
        payload: { delta: "好" },
      }),
    );
    await vi.advanceTimersByTimeAsync(0);
    expect(store.getState().messages.find((m) => m.message_id === assistantId)?.content).toBe(
      "你好",
    );
    expect(store.getState().activeRunProgress).toBeNull();

    // Progress lands only in the run-status area; Message content is untouched.
    channel.send(
      "run.progress",
      "p1",
      env("run.progress", "p1", {
        streamId: "str-1",
        eventSeq: 3,
        payload: { phase: "executing_tool", summary: "正在分析项目文件" },
      }),
    );
    await vi.advanceTimersByTimeAsync(0);
    expect(store.getState().activeRunProgress).toMatchObject({ phase: "executing_tool" });
    expect(store.getState().messages.find((m) => m.message_id === assistantId)?.content).toBe(
      "你好",
    );

    const completed: HpRunSnapshot = {
      run: run({ run_id: "r1", status: "completed" }),
      assistant_message: message({
        message_id: assistantId,
        status: "completed",
        content: "完整的最终回复",
        produced_by_run_id: "r1",
      }),
    };
    backend.state.runSnapshots["r1"] = completed; // the confirm GET returns the same truth
    channel.send(
      "run.completed",
      "t1",
      env("run.completed", "t1", { messageId: assistantId, payload: { snapshot: completed } }),
    );
    await vi.advanceTimersByTimeAsync(0);

    expect(store.getState().activeRun?.status).toBe("completed");
    expect(store.getState().messages.find((m) => m.message_id === assistantId)?.content).toBe(
      "完整的最终回复",
    );
    expect(store.getState().polling).toBe(false);
    expect(store.getState().activeRunProgress).toBeNull();
    expect(store.getState().degraded).toBe(false);
  });

  it("refreshes budget after an LLM trace event without overwriting streamed content", async () => {
    const channel = sseChannel();
    const backend = makeSseBackend(channel);
    const store = makeStore(backend);
    await store.getState().loadConversations();
    await store.getState().selectConversation("c1");
    await store.getState().sendMessage("你好");

    channel.send(
      "message.delta",
      "d1",
      env("message.delta", "d1", {
        messageId: "am-1",
        streamId: "str-1",
        eventSeq: 1,
        payload: { delta: "Hello" },
      }),
    );
    await vi.advanceTimersByTimeAsync(0);

    const refreshedRun = run({ run_id: "r1", status: "running" });
    refreshedRun.budget = {
      status: "ok",
      mode: "observe",
      policy_version: "web-token-v2",
      tokens: {
        input: { used: 10, reserved: 0, limit: 100 },
        output: { used: 5, reserved: 20, limit: 100 },
        total: { used: 15, reserved: 20, limit: 200 },
      },
      model_calls: { settled: 1, in_flight: 1, unmetered: 0, total_attempts: 2, limit: 10 },
      by_source: {
        provider: { input_tokens: 10, output_tokens: 5, total_tokens: 15 },
        measured: { input_tokens: 0, output_tokens: 0, total_tokens: 0 },
        estimated: { input_tokens: 0, output_tokens: 0, total_tokens: 0 },
      },
      usage_state: "in_flight",
      usage_quality: "provider",
      has_estimates: false,
      model_total_tokens_used: 15,
      model_total_tokens_limit: 200,
      tool_calls_used: 0,
      tool_calls_limit: 0,
      bytes_scanned_used: 0,
      bytes_scanned_limit: 0,
    };
    backend.state.runSnapshots.r1 = {
      run: refreshedRun,
      assistant_message: message({
        message_id: "am-1",
        status: "pending",
        content: null,
        produced_by_run_id: "r1",
      }),
    };

    channel.send(
      "trace.event",
      "tr1",
      env("trace.event", "tr1", {
        payload: {
          action: "start",
          node_id: "llm-1",
          parent_id: null,
          name: "LLMCall",
          type: "llm",
          metadata: {},
        },
      }),
    );
    await vi.advanceTimersByTimeAsync(150);

    expect(store.getState().activeRun?.budget?.tokens.total.used).toBe(15);
    expect(store.getState().messages.find((item) => item.message_id === "am-1")?.content).toBe(
      "Hello",
    );
  });

  it("degrades on a sequence gap and recovers by polling the Run", async () => {
    const channel = sseChannel();
    const backend = makeSseBackend(channel);
    const store = makeStore(backend);
    await store.getState().loadConversations();
    await store.getState().selectConversation("c1");
    await store.getState().sendMessage("你好");

    channel.send(
      "message.delta",
      "e1",
      env("message.delta", "e1", {
        messageId: "am-1",
        streamId: "str-1",
        eventSeq: 1,
        payload: { delta: "一" },
      }),
    );
    await vi.advanceTimersByTimeAsync(0);
    channel.send(
      "message.delta",
      "e100",
      env("message.delta", "e100", {
        messageId: "am-1",
        streamId: "str-1",
        eventSeq: 100,
        payload: { delta: "跳" },
      }),
    );
    await vi.advanceTimersByTimeAsync(0);

    // Delta assembly stops; the degraded notice shows while polling recovers.
    expect(store.getState().degraded).toBe(true);
    expect(store.getState().messages.find((m) => m.message_id === "am-1")?.content).toBe("一");
    await vi.advanceTimersByTimeAsync(1000);
    expect(store.getState().activeRun?.status).toBe("completed");
    expect(store.getState().messages.find((m) => m.message_id === "am-1")?.content).toBe(
      "completed answer",
    );
    expect(store.getState().polling).toBe(false);
  });

  it("stops polling when the conversation is switched away", async () => {
    const backend = makeBackend();
    backend.state.runSnapshots["r1"] = {
      run: run({ run_id: "r1", status: "running" }),
      assistant_message: message({
        message_id: "am-1",
        role: "assistant",
        status: "pending",
        content: "growing…",
        produced_by_run_id: "r1",
      }),
    };
    const store = makeStore(backend);
    await store.getState().loadConversations();
    await store.getState().selectConversation("c1");
    await store.getState().sendMessage("你好");

    // Switch away: the r1 poller must stop polling. Count only Run GETs (the
    // events URL contains "/runs/" too and would flake this assertion).
    const runGets = (calls: FakeBackend["calls"]) =>
      calls.filter((c) => /^\/api\/v1\/runs\/[^/]+$/.test(c.url)).length;
    const callsBefore = runGets(backend.calls);
    await store.getState().selectConversation("c2");
    await vi.advanceTimersByTimeAsync(2000);
    const callsAfter = runGets(backend.calls);
    expect(callsAfter).toBe(callsBefore);
  });
});
