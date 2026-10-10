import { useState } from "react";
import { ChevronDown } from "lucide-react";
import { useWorkbench } from "../../store/workbench";

export function ConversationHeader({
  fallbackTitle = "AI",
  active = true,
}: {
  fallbackTitle?: string;
  active?: boolean;
}) {
  const conversation = useWorkbench((s) => s.activeConversation);
  if (!conversation)
    return (
      <div className="hp-conversation-header" hidden={!active}>
        <h1 id={active ? "canvas-title" : undefined} tabIndex={-1} title={fallbackTitle}>
          {fallbackTitle}
        </h1>
      </div>
    );
  return (
    <TitleEditor key={conversation.conversation_id} title={conversation.title} active={active} />
  );
}
function TitleEditor({ title, active }: { title: string; active: boolean }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(title);
  const [busy, setBusy] = useState(false);
  return (
    <div
      className={`hp-conversation-header ${editing ? "hp-conversation-header--editing" : ""}`}
      hidden={!active}
    >
      <h1 id={active ? "canvas-title" : undefined} tabIndex={-1} title={title}>
        {title}
      </h1>
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
        <button
          type="button"
          className="hp-conversation-title-edit"
          aria-label="修改标题"
          title="修改标题"
          onClick={() => {
            setValue(title);
            setEditing(true);
          }}
        >
          <ChevronDown size={18} aria-hidden="true" />
        </button>
      )}
    </div>
  );
}
