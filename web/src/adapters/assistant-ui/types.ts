/**
 * assistant-ui adapter types (contract §14.1).
 *
 * This is the only module in the app allowed to import assistant-ui's type
 * surface. Business DTOs (Hp*) live in `src/api/types.ts`; this file maps them
 * to assistant-ui `ThreadMessageLike`s so the rest of the app stays assistant-ui
 * agnostic.
 */
import type { MessageStatus, ThreadMessageLike } from "@assistant-ui/react";
import type { HpMessage, HpMessageStatus } from "../../api/types";

/** Map a persisted Hp message status onto an assistant-ui streaming status. */
export function toAssistantStatus(status: HpMessageStatus): MessageStatus {
  switch (status) {
    case "pending":
      return { type: "running" };
    case "accepted":
    case "completed":
      return { type: "complete", reason: "stop" };
    case "aborted":
      return { type: "incomplete", reason: "cancelled" };
    case "failed":
      return { type: "incomplete", reason: "error", error: { code: "run_failed" } };
  }
}

/** Map an authoritative HpMessage onto the assistant-ui message shape. */
export function toThreadMessageLike(message: HpMessage): ThreadMessageLike {
  const base = {
    id: message.message_id,
    role: message.role,
    content: message.content ?? "",
    createdAt: new Date(message.created_at),
  };
  // assistant-ui only accepts a streaming `status` on assistant messages —
  // a user message carrying one makes the thread render throw. Omit it there.
  return message.role === "assistant"
    ? { ...base, status: toAssistantStatus(message.status) }
    : base;
}
