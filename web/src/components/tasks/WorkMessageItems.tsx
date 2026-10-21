import { useWorks } from "../../store/works";
import { useShell } from "../../store/shell";
import { presentTask } from "./taskPresentation";

/** Cards are live query projections, never edits to the assistant transcript. */
export function WorkMessageItems({ messageId }: { messageId: string }) {
  const items = useWorks((s) => s.items);
  const now = useWorks((s) => s.now);
  const error = useWorks((s) => s.error);
  const conversationId = useShell((s) => s.route.conversationId);
  const works = items.filter((w) =>
    w.sources?.some(
      (source) =>
        source.conversation_id === conversationId && source.source_message_id === messageId,
    ),
  );
  if (!works.length) return null;
  return (
    <div aria-label="来源消息的后台任务" className="hp-task-actions">
      {works.map((work) => (
        <button
          key={work.work_id}
          onClick={() =>
            useShell.getState().openInspector({
              kind: "task",
              objectId: work.work_id,
              origin: { conversationId, messageId },
            })
          }
        >
          {work.title} · {error ? "状态待同步" : presentTask(work, now).label}
        </button>
      ))}
    </div>
  );
}
