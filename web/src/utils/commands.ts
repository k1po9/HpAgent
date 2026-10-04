import { useRef } from "react";
import { HpCommandError } from "../api/types";
import { newIdempotencyKey } from "./idempotency";

export function commandError(error: unknown) {
  return error instanceof HpCommandError
    ? `${error.message}（请求 ${error.error.request_id}）`
    : error instanceof Error
      ? error.message
      : "操作失败，请重试。";
}

// Reuse the command identity after uncertain responses; changed input is a new intent.
export function useCommandKey() {
  const previous = useRef({ intent: "", key: "" });
  return (intent: unknown) => {
    const encoded = JSON.stringify(intent);
    if (previous.current.intent !== encoded)
      previous.current = { intent: encoded, key: newIdempotencyKey() };
    return previous.current.key;
  };
}
