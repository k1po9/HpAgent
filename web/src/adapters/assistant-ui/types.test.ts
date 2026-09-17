/**
 * assistant-ui adapter mapping tests.
 *
 * Regression: assistant-ui's thread render throws "status is only supported
 * for assistant messages" when a user message carries a `status`. The mapping
 * must therefore omit `status` on user messages and only attach it on
 * assistant ones (E-07 caught this through the browser E2E suite).
 */
import { describe, expect, it } from "vitest";
import type { HpMessage } from "../../api/types";
import { toAssistantStatus, toThreadMessageLike } from "./types";

function userMessage(overrides: Partial<HpMessage> = {}): HpMessage {
  return {
    message_id: "msg-user-1",
    conversation_id: "conv-1",
    role: "user",
    status: "completed",
    content: "你好",
    sequence: 1,
    client_request_id: "cr-1",
    produced_by_run_id: null,
    created_at: "2026-08-01T00:00:00Z",
    completed_at: "2026-08-01T00:00:00Z",
    ...overrides,
  };
}

function assistantMessage(overrides: Partial<HpMessage> = {}): HpMessage {
  return {
    message_id: "msg-assistant-1",
    conversation_id: "conv-1",
    role: "assistant",
    status: "completed",
    content: "这是回复",
    sequence: 2,
    client_request_id: null,
    produced_by_run_id: "run-1",
    created_at: "2026-08-01T00:00:00Z",
    completed_at: "2026-08-01T00:00:00Z",
    ...overrides,
  };
}

describe("toThreadMessageLike", () => {
  it("omits status on user messages (assistant-ui rejects it)", () => {
    const like = toThreadMessageLike(userMessage());
    expect(like.role).toBe("user");
    expect("status" in like).toBe(false);
  });

  it("maps an assistant message status and keeps the content", () => {
    const like = toThreadMessageLike(assistantMessage());
    expect(like.role).toBe("assistant");
    expect(like.status).toEqual({ type: "complete", reason: "stop" });
    expect(like.content).toBe("这是回复");
    expect(like.id).toBe("msg-assistant-1");
  });

  it("maps a pending assistant message to a running streaming status", () => {
    const like = toThreadMessageLike(assistantMessage({ status: "pending" }));
    expect(like.status).toEqual({ type: "running" });
  });

  it("maps a failed assistant message to an error status", () => {
    const like = toThreadMessageLike(assistantMessage({ status: "failed" }));
    expect(like.status).toEqual({
      type: "incomplete",
      reason: "error",
      error: { code: "run_failed" },
    });
  });
});

describe("toAssistantStatus", () => {
  it("maps accepted/completed to complete:stop", () => {
    expect(toAssistantStatus("accepted")).toEqual({ type: "complete", reason: "stop" });
    expect(toAssistantStatus("completed")).toEqual({ type: "complete", reason: "stop" });
  });

  it("maps aborted to incomplete:cancelled", () => {
    expect(toAssistantStatus("aborted")).toEqual({ type: "incomplete", reason: "cancelled" });
  });
});
