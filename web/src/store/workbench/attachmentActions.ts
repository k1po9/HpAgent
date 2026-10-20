import { newIdempotencyKey } from "../../utils/idempotency";
import type { WorkbenchContext } from "./context";
import type { WorkbenchState, UploadAttachment } from "./types";
import { isTerminalRunStatus } from "./runStatus";
import { messageErrorText } from "./messages";

export function attachmentActions(
  context: WorkbenchContext,
): Pick<
  WorkbenchState,
  "addAttachments" | "selectExistingFile" | "loadFileCandidates" | "removeAttachment"
> {
  const { api, set, get, session, fence } = context;

  return {
    addAttachments: async (selectedFiles) => {
      const account = session.accountGeneration;
      const valid = fence();
      const conversationId = get().activeConversationId;
      if (!conversationId || selectedFiles.length === 0) return;
      const active = get().activeRun;
      if (get().sending || (active && !isTerminalRunStatus(active.status))) return;
      const available = Math.max(0, 10 - get().attachments.length);
      const accepted = selectedFiles.slice(0, available);
      if (accepted.length === 0) {
        set({ error: "每条消息最多添加 10 个附件。" });
        return;
      }
      if (accepted.length < selectedFiles.length) {
        set({ error: "每条消息最多添加 10 个附件，多余文件未加入。" });
      }
      const queued = accepted.map<UploadAttachment>((file) => ({
        localId: newIdempotencyKey(),
        name: file.name,
        size: file.size,
        status: "uploading",
        fileId: null,
        file: null,
        error: null,
      }));
      set((state) => ({ attachments: [...state.attachments, ...queued] }));
      await Promise.all(
        accepted.map(async (browserFile, index) => {
          const attachment = queued[index];
          if (!attachment) return;
          try {
            const created = await api.createUpload(
              conversationId,
              browserFile,
              newIdempotencyKey(),
            );
            if (!valid()) {
              if (account === session.accountGeneration) await api.deleteFile(created.file.file_id);
              return;
            }
            set((state) => ({
              attachments: state.attachments.map((item) =>
                item.localId === attachment.localId
                  ? { ...item, fileId: created.file.file_id }
                  : item,
              ),
            }));
            if (!get().attachments.some((item) => item.localId === attachment.localId)) {
              await api.deleteFile(created.file.file_id);
              return;
            }
            const uploaded = await api.uploadContent(created.content_url, browserFile);
            if (!valid()) {
              if (account === session.accountGeneration) await api.deleteFile(uploaded.file_id);
              return;
            }
            set((state) => ({
              attachments: state.attachments.map((item) =>
                item.localId === attachment.localId
                  ? { ...item, status: "ready", fileId: uploaded.file_id, file: uploaded }
                  : item,
              ),
            }));
            if (!get().attachments.some((item) => item.localId === attachment.localId)) {
              await api.deleteFile(uploaded.file_id);
            }
          } catch (err) {
            if (!valid()) return;
            set((state) => ({
              attachments: state.attachments.map((item) =>
                item.localId === attachment.localId
                  ? { ...item, status: "failed", error: messageErrorText(err) }
                  : item,
              ),
            }));
          }
        }),
      );
    },
    selectExistingFile: (file) => {
      if (get().sending || (get().activeRun && !isTerminalRunStatus(get().activeRun!.status)))
        return;
      if (
        file.status !== "ready" ||
        get().attachments.length >= 10 ||
        get().attachments.some((item) => item.fileId === file.file_id)
      )
        return;
      set((state) => ({
        attachments: [
          ...state.attachments,
          {
            localId: newIdempotencyKey(),
            name: file.file_name,
            size: file.size_bytes ?? 0,
            status: "ready",
            fileId: file.file_id,
            file,
            error: null,
            existing: true,
          },
        ],
      }));
    },
    loadFileCandidates: async (more = false) => {
      const valid = fence();
      const conversationId = get().activeConversationId;
      if (!conversationId || get().loadingFileCandidates || (more && !get().fileCandidatesNext))
        return;
      const generation = ++session.candidateGeneration;
      set({ loadingFileCandidates: true, fileCandidatesError: null });
      try {
        const page = await api.listFileCandidates(
          conversationId,
          more ? get().fileCandidatesNext : null,
        );
        if (!valid() || generation !== session.candidateGeneration) return;
        set((state) => ({
          loadingFileCandidates: false,
          fileCandidates: [
            ...new Map(
              (more ? [...state.fileCandidates, ...page.items] : page.items).map((f) => [
                f.file_id,
                f,
              ]),
            ).values(),
          ],
          fileCandidatesNext: page.next_before,
        }));
      } catch (err) {
        if (!valid() || generation !== session.candidateGeneration) return;
        set({ loadingFileCandidates: false, fileCandidatesError: messageErrorText(err) });
      }
    },
    removeAttachment: async (localId) => {
      const valid = fence();
      const attachment = get().attachments.find((item) => item.localId === localId);
      if (!attachment) return;
      set((state) => ({
        attachments: state.attachments.filter((item) => item.localId !== localId),
      }));
      if (attachment.fileId && !attachment.existing) {
        try {
          await api.deleteFile(attachment.fileId);
        } catch (err) {
          if (!valid()) return;
          set({ error: messageErrorText(err) });
        }
      }
    },
  };
}
