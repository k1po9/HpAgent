import { useRef, useState } from "react";
import type { HpWorkspaceNode } from "../../api/types";
import { workspaceApi, useWorkspace } from "../../store/workspace";
import { useWorkbench } from "../../store/workbench";
import { useShell } from "../../store/shell";
import { Surface } from "../shell/Surface";
import { SubjectPicker } from "./SubjectPicker";
import { grantMissing, useConversationUseIntents, type Subject } from "./workspaceOperations";
import { newIdempotencyKey } from "../../utils/idempotency";
import { commandError } from "../../utils/commands";
export function UseInConversationDialog({
  node,
  onClose,
}: {
  node: HpWorkspaceNode;
  onClose: () => void;
}) {
  const stored = useConversationUseIntents((s) => s.intents[node.node_id]);
  const [subject, setSubject] = useState<Subject | undefined>(stored?.subject);
  const [createNew, setCreateNew] = useState(stored?.createNew ?? false);
  const [recursive, setRecursive] = useState(stored?.recursive ?? false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const key = useRef(stored?.key ?? newIdempotencyKey());
  const created = useRef<Subject | null>(stored?.createNew ? (stored.subject ?? null) : null);
  const generation = useWorkspace((s) => s.generation);
  const [locked, setLocked] = useState(Boolean(stored));
  return (
    <Surface
      title="在对话中使用资料"
      onClose={() => {
        if (!busy) onClose();
      }}
    >
      <p>为目标对话授权发现与读取「{node.name}」。授权后进入对话，不自动发送消息。</p>
      <label>
        <input
          type="checkbox"
          disabled={busy || locked}
          checked={createNew}
          onChange={(e) => setCreateNew(e.target.checked)}
        />
        创建新对话
      </label>
      {!createNew && (
        <SubjectPicker
          conversationsOnly
          disabled={busy || locked}
          value={subject}
          onChange={setSubject}
        />
      )}
      {node.kind === "directory" && (
        <label>
          <input
            type="checkbox"
            disabled={busy || locked}
            checked={recursive}
            onChange={(e) => setRecursive(e.target.checked)}
          />
          包含此目录的所有后代
        </label>
      )}
      {createNew && subject && <p>已创建对话「{subject.title}」，失败重试仅补授权。</p>}
      <button
        disabled={busy || (!createNew && !subject)}
        onClick={() => {
          setBusy(true);
          setLocked(true);
          setError("");
          useConversationUseIntents.setState((s) => ({
            intents: {
              ...s.intents,
              [node.node_id]: { key: key.current, subject, recursive, createNew },
            },
          }));
          void (async () => {
            let target = subject;
            if (createNew) {
              if (!created.current) {
                const { conversation } = await workspaceApi.createConversation(key.current);
                if (generation !== useWorkspace.getState().generation) return;
                created.current = {
                  kind: "conversation",
                  id: conversation.conversation_id,
                  title: conversation.title,
                };
                setSubject(created.current);
                const target = created.current;
                useConversationUseIntents.setState((s) => ({
                  intents: {
                    ...s.intents,
                    [node.node_id]: { key: key.current, subject: target, recursive, createNew },
                  },
                }));
                void useWorkbench.getState().loadConversations();
              }
              target = created.current;
            }
            if (!target) return;
            await grantMissing(
              target,
              node.node_id,
              ["list_metadata", "read_content"],
              node.kind === "directory" && recursive,
            );
            if (generation !== useWorkspace.getState().generation) return;
            useConversationUseIntents.setState((s) => {
              const intents = { ...s.intents };
              delete intents[node.node_id];
              return { intents };
            });
            onClose();
            useShell.getState().navigate({ screen: "ai", conversationId: target.id });
            // Navigate uses the existing attachment guard. Focus only after the target is actually ready.
            const id = target.id;
            const unsubscribe = useWorkbench.subscribe((state) => {
              if (state.activeConversationId === id && !state.loadingConversations) {
                document
                  .querySelector<HTMLTextAreaElement>(
                    ".aui-composer-input, .hp-composer textarea, textarea[placeholder]",
                  )
                  ?.focus();
                unsubscribe();
              }
            });
            setTimeout(unsubscribe, 10000);
          })()
            .catch((e) => {
              if (generation === useWorkspace.getState().generation) setError(commandError(e));
            })
            .finally(() => {
              if (generation === useWorkspace.getState().generation) setBusy(false);
            });
        }}
      >
        {busy ? "授权中…" : "确认读取授权并进入对话"}
      </button>
      {error && <p role="alert">{error}</p>}
    </Surface>
  );
}
