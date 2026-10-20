import { useConversationUi } from "../conversationUi";
import { useAuth } from "../auth";
import { newIdempotencyKey } from "../../utils/idempotency";
import { HpCommandError, type HpMessage } from "../../api/types";
import type { WorkbenchContext } from "./context";
import type { WorkbenchState } from "./types";
import type { RunMonitor } from "./runMonitor";
import { isTerminalRunStatus } from "./runStatus";
import { replaceTempMessage, messageErrorText } from "./messages";

export function sendActions(
  context: WorkbenchContext,
  monitor: RunMonitor,
): Pick<WorkbenchState, "confirmPendingSend" | "sendMessage"> {
  const { api, set, get, session, fence } = context;
  const { startRunMonitor, refreshActiveRun } = monitor;

  return {
    confirmPendingSend: async () => {
      const id = get().activeConversationId;
      const intent = id ? session.intents.get(id) : null;
      return intent ? get().sendMessage(intent.content, true) : false;
    },
    sendMessage: async (content, confirm = false) => {
      const account = session.accountGeneration;
      const valid = fence();
      const conversationId = get().activeConversationId;
      const trimmed = content.trim();
      const draftKey = `${useAuth.getState().account?.account_id ?? "anonymous"}:${conversationId}`;
      const submittedDraft = useConversationUi.getState().entries[draftKey];
      if (!conversationId || !trimmed) return false;
      // Only a live Run blocks sending: after a terminal Run the composer is
      // re-enabled so a long conversation continues in place (E-07).
      const active = get().activeRun;
      if (
        get().sending ||
        get().stopping ||
        (!confirm && active && !isTerminalRunStatus(active.status))
      ) {
        return false;
      }
      const attachments = get().attachments;
      if (!confirm && attachments.some((item) => item.status !== "ready" || !item.fileId)) {
        set({ error: "请等待附件上传完成，或移除上传失败的附件。" });
        return false;
      }
      const previousIntent = session.intents.get(conversationId);
      if (
        !confirm &&
        previousIntent &&
        (previousIntent.content !== trimmed ||
          previousIntent.strategy !== get().agentStrategy ||
          JSON.stringify(previousIntent.fileIds) !==
            JSON.stringify(attachments.map((item) => item.fileId)))
      ) {
        set({ error: "上次发送结果尚未确认，请先用原内容和附件重新确认。" });
        return false;
      }
      const intent = previousIntent ?? {
        key: newIdempotencyKey(),
        content: trimmed,
        fileIds: attachments.map((item) => item.fileId as string),
        strategy: get().agentStrategy,
      };
      session.intents.set(conversationId, intent);
      set({
        pendingSendIds: [...session.intents.keys()],
        pendingSendContent: Object.fromEntries(
          [...session.intents].map(([id, intent]) => [id, intent.content]),
        ),
      });
      const idempotencyKey = intent.key;
      const tempId = `temp:${idempotencyKey}`;
      const tempMessage: HpMessage = {
        message_id: tempId,
        conversation_id: conversationId,
        role: "user",
        status: "accepted",
        content: trimmed,
        sequence: 0,
        client_request_id: null,
        produced_by_run_id: null,
        created_at: new Date().toISOString(),
        completed_at: null,
      };
      set((s) => ({ sending: true, error: null, messages: [...s.messages, tempMessage] }));
      try {
        const result = await api.sendMessage(conversationId, trimmed, {
          idempotencyKey,
          agentStrategy: intent.strategy,
          fileIds: intent.fileIds,
        });
        if (account === session.accountGeneration) {
          const currentDraft = useConversationUi.getState().entries[draftKey];
          if (
            currentDraft &&
            submittedDraft &&
            currentDraft.revision === submittedDraft.revision &&
            currentDraft.text.trim() === trimmed
          )
            useConversationUi
              .getState()
              .update(draftKey, { text: "", revision: currentDraft.revision + 1 });
          session.intents.delete(conversationId);
          set({
            pendingSendIds: [...session.intents.keys()],
            pendingSendContent: Object.fromEntries(
              [...session.intents].map(([id, intent]) => [id, intent.content]),
            ),
          });
        }
        if (!valid()) {
          // A new selection may have read the conversation before this POST
          // committed. Re-read the selected object rather than applying the
          // old response across its selection generation.
          if (
            account === session.accountGeneration &&
            get().activeConversationId === conversationId
          )
            await get().selectConversation(conversationId, true);
          return false;
        }
        const currentRun = get().activeRun;
        const sameRun = currentRun?.run_id === result.run.run_id;
        const resolvedRun =
          sameRun && currentRun.version >= result.run.version ? currentRun : result.run;
        const existingAssistant = sameRun
          ? get().messages.find((m) => m.message_id === result.assistant_message.message_id)
          : null;
        set((s) => ({
          sending: false,
          messages: replaceTempMessage(
            s.messages,
            tempId,
            result.user_message,
            existingAssistant ?? result.assistant_message,
          ),
          activeRun: resolvedRun,
          activeRunError: null,
          activeRunProgress: sameRun ? s.activeRunProgress : null,
          degraded: sameRun ? s.degraded : false,
          attachments: s.attachments.filter((a) => !a.fileId || !intent.fileIds.includes(a.fileId)),
          pollGeneration: sameRun ? s.pollGeneration : s.pollGeneration + 1,
        }));
        if (!sameRun) startRunMonitor(result.run.run_id);
        return true;
      } catch (err) {
        if (
          account === session.accountGeneration &&
          err instanceof HpCommandError &&
          err.status < 500
        ) {
          session.intents.delete(conversationId);
          set({
            pendingSendIds: [...session.intents.keys()],
            pendingSendContent: Object.fromEntries(
              [...session.intents].map(([id, intent]) => [id, intent.content]),
            ),
          });
        }
        if (!valid()) return false;
        const messages = get().messages.filter((m) => m.message_id !== tempId);
        if (err instanceof HpCommandError && err.code === "conversation_busy") {
          set({ sending: false, messages, activeRunError: err.error.message });
          void refreshActiveRun();
        } else {
          set({
            sending: false,
            messages,
            error: session.intents.has(conversationId)
              ? "发送结果尚未确认，请保留原内容重试确认。"
              : messageErrorText(err),
          });
          if (session.intents.has(conversationId)) void refreshActiveRun();
        }
        return false;
      }
    },
  };
}
