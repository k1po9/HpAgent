/**
 * External-store runtime adapter (contract §14.1).
 *
 * Bridges the Hp workbench store to assistant-ui's `useExternalStoreRuntime`:
 * authoritative `HpMessage[]` flow in, assistant-ui renders them via
 * `convertMessage`, and the user's composer actions (`onNew` / `onCancel`) map
 * back to the store's send/stop commands.
 *
 * regenerate/branch semantics are intentionally NOT wired (`onReload` and
 * `onEdit` are omitted); failed/cancelled Runs are retried from the run-status
 * area instead (phase-e E-04).
 */
import { useCallback } from "react";
import {
  useExternalStoreRuntime,
  type AppendMessage,
  type ExternalStoreAdapter,
} from "@assistant-ui/react";
import type { HpMessage, HpRun } from "../../api/types";
import { toThreadMessageLike } from "./types";

export interface HpThreadRuntimeOptions {
  messages: HpMessage[];
  activeRun: HpRun | null;
  onSend: (content: string) => void;
  onCancel: () => void;
}

function extractText(content: AppendMessage["content"]): string {
  if (typeof content === "string") {
    return content;
  }
  return content
    .filter((part) => part.type === "text")
    .map((part) => part.text)
    .join("");
}

export function useHpThreadRuntime({
  messages,
  activeRun,
  onSend,
  onCancel,
}: HpThreadRuntimeOptions) {
  const convertMessage = useCallback(
    (message: HpMessage): ReturnType<typeof toThreadMessageLike> => toThreadMessageLike(message),
    [],
  );

  const isRunning = activeRun !== null;

  const onNew = useCallback(
    async (message: AppendMessage) => {
      const text = extractText(message.content).trim();
      if (text) {
        onSend(text);
      }
    },
    [onSend],
  );

  const onCancelRun = useCallback(async () => {
    onCancel();
  }, [onCancel]);

  const adapter: ExternalStoreAdapter<HpMessage> = {
    messages,
    convertMessage,
    isRunning,
    // While a Run is active the conversation is busy; the backend 409 remains
    // the final arbiter for cross-tab races.
    isSendDisabled: isRunning,
    onNew,
    onCancel: onCancelRun,
  };

  return useExternalStoreRuntime<HpMessage>(adapter);
}
