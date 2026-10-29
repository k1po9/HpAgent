import { useEffect, useId, useRef, useState } from "react";
import { Check, X } from "lucide-react";
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
  const trigger = useRef<HTMLButtonElement>(null);
  const wasEditing = useRef(false);
  const composing = useRef(false);
  const hintId = useId();
  useEffect(() => {
    if (wasEditing.current && !editing && active) trigger.current?.focus();
    wasEditing.current = editing;
  }, [editing, active]);
  const beginEdit = () => {
    setValue(title);
    setEditing(true);
  };
  return (
    <div className="hp-conversation-header" hidden={!active}>
      {editing ? (
        <form
          className="hp-title-inline-edit"
          aria-label="修改对话标题"
          tabIndex={-1}
          id={active ? "canvas-title" : undefined}
          onKeyDown={(event) => {
            if (
              composing.current ||
              event.nativeEvent.isComposing ||
              event.nativeEvent.keyCode === 229
            ) {
              if (event.key === "Enter") event.preventDefault();
              return;
            }
            if (event.key === "Escape") {
              event.preventDefault();
              event.stopPropagation();
              if (!busy) setEditing(false);
            }
          }}
          onSubmit={(event) => {
            event.preventDefault();
            if (busy || !value.trim()) return;
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
            aria-describedby={hintId}
            autoFocus
            value={value}
            disabled={busy}
            onCompositionStart={() => {
              composing.current = true;
            }}
            onCompositionEnd={() => {
              composing.current = false;
            }}
            onChange={(event) => setValue(event.target.value)}
          />
          <button
            type="submit"
            aria-label="保存标题"
            title={busy ? "保存中…" : "保存标题（Enter）"}
            disabled={busy || !value.trim()}
          >
            <Check size={18} aria-hidden="true" />
          </button>
          <button
            type="button"
            aria-label="取消"
            title="取消（Esc）"
            disabled={busy}
            onClick={() => setEditing(false)}
          >
            <X size={18} aria-hidden="true" />
          </button>
        </form>
      ) : (
        <h1 id={active ? "canvas-title" : undefined} tabIndex={-1}>
          <button
            ref={trigger}
            type="button"
            className="hp-title-trigger"
            title={`${title} · 双击修改标题`}
            aria-describedby={hintId}
            onDoubleClick={beginEdit}
            onPointerUp={(event) => {
              if (event.pointerType === "touch") beginEdit();
            }}
            onClick={(event) => {
              // Keyboard/assistive activation has no mouse click count.
              if (event.detail === 0) beginEdit();
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === "F2") {
                event.preventDefault();
                beginEdit();
              }
            }}
          >
            {title}
          </button>
        </h1>
      )}
      <span id={hintId} className="hp-title-edit-hint">
        {editing ? "Enter 保存，Esc 取消。" : "双击或按 Enter、F2 修改标题。"}
      </span>
    </div>
  );
}
