import { useState } from "react";
import { useWorkbench } from "../../store/workbench";

export function ConversationHeader() {
  const conversation = useWorkbench((s) => s.activeConversation);
  if (!conversation) return null;
  return <TitleEditor key={conversation.conversation_id} title={conversation.title} />;
}
function TitleEditor({ title }: { title: string }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(title);
  const [busy, setBusy] = useState(false);
  return (
    <header className="hp-conversation-header">
      {editing ? (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (busy) return;
            setBusy(true);
            void useWorkbench
              .getState()
              .renameActiveConversation(value)
              .then((ok) => {
                if (ok) setEditing(false);
              })
              .finally(() => setBusy(false));
          }}
        >
          <input
            aria-label="对话标题"
            autoFocus
            value={value}
            onChange={(e) => setValue(e.target.value)}
          />
          <button disabled={busy}>{busy ? "保存中…" : "保存标题"}</button>
          <button type="button" disabled={busy} onClick={() => setEditing(false)}>
            取消
          </button>
        </form>
      ) : (
        <>
          <h2 title={title}>{title}</h2>
          <button
            onClick={() => {
              setValue(title);
              setEditing(true);
            }}
          >
            修改标题
          </button>
        </>
      )}
    </header>
  );
}
