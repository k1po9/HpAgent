import { useWorks } from "../../store/works";
import { useShell } from "../../store/shell";
import { bucketLabels, taskType, presentTask, type TaskBucket } from "./taskPresentation";
export function TaskSidebar() {
  const now = useWorks((s) => s.now);
  const route = useShell((s) => s.route);
  const items = useWorks((s) => s.items);
  const state = useWorks((s) => s.loadState);
  const filtered = items.filter(
    (w) => !route.type || route.type === "all" || taskType(w) === route.type,
  );
  const projected = filtered.map((w) => presentTask(w, now));
  return (
    <nav className="hp-task-sidebar" aria-label="任务分类">
      <h2>任务中心</h2>
      <p className="hp-muted">
        {state === "complete" ? "本轮已遍历的任务" : `已加载 ${items.length} 项 · 计数待同步`}
      </p>
      {(Object.entries(bucketLabels) as Array<[TaskBucket, string]>).map(([id, label]) => (
        <button
          key={id}
          aria-current={(route.bucket ?? "attention") === id ? "page" : undefined}
          onClick={() => useShell.getState().navigate({ ...route, bucket: id }, true)}
        >
          <span>{label}</span>
          <strong>{projected.filter((w) => w.bucket === id).length}</strong>
          {id === "active" &&
            projected.some((w) => w.bucket === id && w.attentionReasons.length > 0) && (
              <small>含需要确认的操作</small>
            )}
        </button>
      ))}
    </nav>
  );
}
