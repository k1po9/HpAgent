import { useEffect, useRef, useState } from "react";
import { useWorkbench } from "../../store/workbench";
import { useArtifacts } from "../../store/artifacts";
import { useShell } from "../../store/shell";

export function ArtifactCreateForm() {
  const messages = useWorkbench((s) => s.messages);
  const byMessage = useArtifacts((s) => s.artifactsByMessageId);
  const create = useArtifacts((s) => s.createArtifact);
  const load = useArtifacts((s) => s.loadForMessage);
  const conversationId = useWorkbench((s) => s.activeConversationId);
  const open = (objectId: string, versionId?: string) =>
    useShell.getState().openInspector({
      kind: "artifact",
      objectId,
      versionId,
      origin: {
        conversationId: conversationId ?? undefined,
        messageId: selected,
        triggerId: "artifact-create-trigger",
      },
    });
  const [messageId, setMessageId] = useState("");
  const intent = useArtifacts((s) => s.intents[`message:${messageId}`]);
  const [instruction, setInstruction] = useState("");
  const [busy, setBusy] = useState(false);
  const alive = useRef(false);
  const lock = useRef(false);
  const sourceToken = useRef(0);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const candidates = messages.filter(
    (m) => m.role === "assistant" && m.status === "completed" && Boolean(m.content?.trim()),
  );
  const selected = candidates.some((m) => m.message_id === messageId) ? messageId : "";
  return (
    <>
      <form
        className="hp-operation-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (lock.current || !selected) return;
          lock.current = true;
          setBusy(true);
          const token = useShell.getState().requestToken;
          const selectedToken = sourceToken.current;
          void create(selected, instruction || null)
            .then((result) => {
              if (
                alive.current &&
                selectedToken === sourceToken.current &&
                result.status === "success" &&
                result.artifact &&
                token === useShell.getState().requestToken
              )
                open(result.artifact.artifact_id, result.version?.artifact_version_id);
            })
            .finally(() => {
              lock.current = false;
              if (alive.current) setBusy(false);
            });
        }}
      >
        <p>选择已完成的回复，生成可迭代的 HTML 成果。</p>
        <label>
          来源消息
          <select
            aria-describedby={intent?.result?.error ? "artifact-create-error" : undefined}
            aria-invalid={Boolean(intent?.result?.error)}
            required
            value={selected}
            onChange={(e) => {
              sourceToken.current++;
              setMessageId(e.target.value);
              if (e.target.value) void load(e.target.value);
            }}
          >
            <option value="">选择当前对话已完成的回复</option>
            {candidates.map((m) => (
              <option key={m.message_id} value={m.message_id}>
                {m.sequence} · {m.content?.slice(0, 80)}
              </option>
            ))}
          </select>
        </label>
        <label>
          生成要求
          <textarea value={instruction} onChange={(e) => setInstruction(e.target.value)} />
        </label>
        <button disabled={busy || !selected}>
          {busy ? "创建中…" : intent?.uncertain ? "恢复原 HTML 生成" : "创建成果"}
        </button>
        {(byMessage[selected] ?? []).map((a) => (
          <button
            type="button"
            key={a.artifact.artifact_id}
            onClick={() => void open(a.artifact.artifact_id)}
          >
            {a.artifact.title}
          </button>
        ))}
        {intent?.result?.error && (
          <p id="artifact-create-error" role="alert">
            {intent.result.error}
          </p>
        )}
        {!candidates.length && <p>先在对话页完成一次回复，或从工作列表打开已有成果。</p>}
      </form>
    </>
  );
}
