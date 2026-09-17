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

/** Terminal Run statuses: the composer un-gates once a Run is finished. */
const TERMINAL_RUN_STATUS = new Set<HpRun["status"]>(["completed", "failed", "cancelled"]);

export interface HpThreadRuntimeOptions {
  messages: HpMessage[];
  activeRun: HpRun | null;
  sendDisabled?: boolean;
  onSend: (content: string) => boolean | Promise<boolean>;
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
  sendDisabled = false,
  onSend,
  onCancel,
}: HpThreadRuntimeOptions) {
  const convertMessage = useCallback(
    (message: HpMessage): ReturnType<typeof toThreadMessageLike> => toThreadMessageLike(message),
    [],
  );

  // Only a live (queued/running/cancelling) Run gates the composer: a terminal
  // Run re-enables sending so a long conversation can continue in place, while
  // the run-status area keeps offering Retry for failed/cancelled Runs.
  const isRunning = activeRun !== null && !TERMINAL_RUN_STATUS.has(activeRun.status);

  const onNew = useCallback(
    async (message: AppendMessage) => {
      const text = extractText(message.content).trim();
      if (text) {
        await onSend(text);
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
    isSendDisabled: isRunning || sendDisabled,
    onNew,
    onCancel: onCancelRun,
  };

  return useExternalStoreRuntime<HpMessage>(adapter);
}
