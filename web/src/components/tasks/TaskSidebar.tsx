import { Badge, List } from "@lobehub/ui";
import { Inbox, LoaderCircle, Clock, CircleCheck } from "lucide-react";
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
  const icons = { attention: Inbox, active: LoaderCircle, waiting: Clock, ended: CircleCheck };
  return (
    <nav className="hp-task-sidebar hp-lobe-scope" aria-label="任务分类">
      <span className="hp-sidebar-eyebrow" aria-hidden="true">
        TASK CENTER
      </span>
      <h2>任务中心</h2>
      <p className="hp-muted">
        {state === "complete" ? "本轮已遍历的任务" : `已加载 ${items.length} 项 · 计数待同步`}
      </p>
      <List
        className="hp-task-category-list"
        classNames={{ item: "hp-task-category", description: "hp-task-category-description" }}
        activeKey={route.bucket ?? "attention"}
        selectable
        onClick={({ key }) =>
          useShell.getState().navigate({ ...route, bucket: key as TaskBucket }, true)
        }
        items={(Object.entries(bucketLabels) as Array<[TaskBucket, string]>).map(([id, label]) => ({
          key: id,
          label,
          icon: icons[id],
          extra: (
            <Badge
              className="hp-task-count"
              count={String(projected.filter((w) => w.bucket === id).length)}
              size="small"
            />
          ),
          description:
            id === "active" &&
            projected.some((w) => w.bucket === id && w.attentionReasons.length > 0)
              ? "含需要确认的操作"
              : undefined,
        }))}
      />
    </nav>
  );
}
