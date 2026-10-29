import { TaskButton } from "../ui/TaskControls";
import { Block, Tag } from "@lobehub/ui";
import { useWorks } from "../../store/works";
import type { HpWork } from "../../api/types";
import { useShell } from "../../store/shell";
import { presentTask, taskTime, typeLabel } from "./taskPresentation";
import { TaskActions } from "./TaskActions";
import { Bell, Search, FileText } from "lucide-react";
export function TaskRow({ work, selected }: { work: HpWork; selected?: boolean }) {
  const now = useWorks((s) => s.now);
  const p = presentTask(work, now);
  const Icon =
    work.requirement.capability_key === "reminder"
      ? Bell
      : work.requirement.capability_key === "research_report"
        ? Search
        : FileText;
  const budget = work.budget;
  const used = budget?.used.model_total_tokens ?? 0,
    limit = budget?.limits.model_total_tokens;
  return (
    <Block
      as="article"
      variant="outlined"
      aria-label={work.title}
      data-status={work.status}
      data-bucket={p.bucket}
      data-work-id={work.work_id}
      data-selected={Boolean(selected)}
      className={`hp-lobe-scope hp-task-row ${selected ? "hp-task-row--selected" : ""}`}
    >
      <div className="hp-task-card-symbol">
        <Icon size={19} aria-hidden="true" />
      </div>
      <p className="hp-task-card-meta hp-muted">
        {typeLabel(work)}
        {taskTime(work) ? ` · ${taskTime(work)}` : ""}
      </p>
      <div className="hp-task-row-heading">
        <TaskButton
          id={`task-${work.work_id}`}
          className="hp-task-title"
          appearance="text"
          aria-expanded={Boolean(selected)}
          onClick={() =>
            useShell.getState().openInspector({
              kind: "task",
              objectId: work.work_id,
              origin: {
                triggerId: `task-${work.work_id}`,
                workId: work.work_id,
                sourceRequirementRevision: work.current_requirement_revision,
              },
            })
          }
        >
          {work.title}
        </TaskButton>
      </div>
      <p className="hp-task-objective">{work.requirement.objective}</p>
      <Tag className="hp-task-status" size="small" variant="filled">
        {p.label}
      </Tag>
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
    </Block>
  );
}
