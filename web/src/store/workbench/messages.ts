import { HpCommandError, type HpMessage } from "../../api/types";

export /** Replace the temp user message with authoritative ids; append the assistant msg. */
function replaceTempMessage(
  messages: HpMessage[],
  tempId: string,
  userMessage: HpMessage,
  assistantMessage: HpMessage,
): HpMessage[] {
  const next = messages.filter((m) => m.message_id !== tempId);
  return [
    ...next.filter(
      (m) =>
        m.message_id !== userMessage.message_id && m.message_id !== assistantMessage.message_id,
    ),
    userMessage,
    assistantMessage,
  ];
}

export /** Update the assistant message for a Run in place (by id, then by run). */
function reconcileAssistantMessage(messages: HpMessage[], assistant: HpMessage): HpMessage[] {
  const byId = messages.findIndex((m) => m.message_id === assistant.message_id);
  if (byId >= 0) {
    const next = messages.slice();
    next[byId] = assistant;
    return next;
  }
  const byRun = messages.findIndex(
    (m) => m.role === "assistant" && m.produced_by_run_id === assistant.produced_by_run_id,
  );
  if (byRun >= 0) {
    const next = messages.slice();
    next[byRun] = assistant;
    return next;
  }
  return [...messages, assistant];
}

export /**
 * Append a volatile SSE delta to the pending assistant Message (E-06).
 *
 * Deltas are an in-memory display buffer only: they are never written to the
 * backend, and a terminal snapshot fully replaces them. Matching is by
 * message_id first, then by the Run that produced the pending assistant Message.
 */
function appendDelta(
  messages: HpMessage[],
  runId: string,
  messageId: string,
  delta: string,
): HpMessage[] {
  const idx = messages.findIndex(
    (m) => m.message_id === messageId || (m.role === "assistant" && m.produced_by_run_id === runId),
  );
  if (idx < 0) return messages;
  const next = messages.slice();
  const current = next[idx];
  if (!current) return messages;
  next[idx] = { ...current, content: `${current.content ?? ""}${delta}` };
  return next;
}

export function messageErrorText(err: unknown): string {
  if (err instanceof HpCommandError) {
    return err.error.message;
  }
  if (err instanceof Error) {
    return err.message;
  }
  return "发生未知错误，请重试。";
}
