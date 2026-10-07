import { useWorks } from "../../store/works";
import type { HpWork } from "../../api/types";
import { useShell } from "../../store/shell";
import { presentTask, taskTime, typeLabel } from "./taskPresentation";
import { TaskActions } from "./TaskActions";
export function TaskRow({ work, selected }: { work: HpWork; selected?: boolean }) {
  const now = useWorks((s) => s.now);
  const p = presentTask(work, now);
  const budget = work.budget;
  const used = budget?.used.model_total_tokens ?? 0,
    limit = budget?.limits.model_total_tokens;
  return (
    <article
      aria-label={work.title}
      className={`hp-task-row ${selected ? "hp-task-row--selected" : ""}`}
    >
      <div className="hp-task-row-heading">
        <button
          id={`task-${work.work_id}`}
          className="hp-task-title"
          onClick={() =>
            useShell.getState().openInspector({ kind: "task", objectId: work.work_id })
          }
        >
          {work.title}
        </button>
        <span className="hp-task-status">{p.label}</span>
      </div>
      <p className="hp-task-objective">{work.requirement.objective}</p>
      <p className="hp-muted">
        {typeLabel(work)}
        {taskTime(work) ? ` · ${taskTime(work)}` : ""}
      </p>
      {p.attentionReasons.length > 0 && p.bucket !== "ended" && (
        <p className="hp-task-attention">{p.attentionReasons.join("；")}</p>
      )}
      {p.dataWarnings.map((w) => (
        <p key={w}>{w}</p>
      ))}
      {budget && (
        <p>
          模型预算已用 {used.toLocaleString()} / {limit?.toLocaleString() ?? "未知"}
          {limit && limit > 0 ? `（${Math.round((used / limit) * 100)}%）` : ""} · 已预留{" "}
          {(budget.reserved.model_total_tokens ?? 0).toLocaleString()}
        </p>
      )}
      <TaskActions work={work} compact />
    </article>
  );
}
