import { Descriptions, Tag } from "@lobehub/ui";
import {
  TaskButton,
  TaskInput,
  TaskDetails,
  TaskDescriptions,
  TaskCode,
  TaskLabel,
  TaskForm,
} from "../ui/TaskControls";
import { TaskInspectorTabs } from "./TaskInspectorTabs";
import { RunLookup } from "../run/RunLookup";
import { useEffect, useState, type ReactNode } from "react";
import type { HpWork } from "../../api/types";
import { useWorks } from "../../store/works";
import { useShell, type Inspector } from "../../store/shell";
import { presentTask, bucketLabels, typeLabel, formatTaskTime } from "./taskPresentation";
import { TaskActions } from "./TaskActions";
import { TaskOutputs } from "./TaskOutputs";
import { TaskResources } from "./TaskResources";
import { TaskTargets } from "./TaskTargets";
import { TaskEvents } from "./TaskEvents";
import { validSnapshot } from "./taskActions";
const tabs = [
  { id: "overview", label: "概览" },
  { id: "outputs", label: "成果与执行" },
  { id: "resources", label: "使用资料" },
  { id: "advanced", label: "高级详情" },
] as const;
/** Preserve visited form/query owners without loading unvisited tabs. */
function TaskPanel({ id, active, children }: { id: string; active: boolean; children: ReactNode }) {
  const [visited, setVisited] = useState(active);
  if (active && !visited) setVisited(true);
  return (
    <section
      role="tabpanel"
      id={`task-panel-${id}`}
      aria-labelledby={`task-tab-${id}`}
      hidden={!active}
      inert={!active}
    >
      {(active || visited) && children}
    </section>
  );
}
export function TaskInspector({
  inspector,
  onSaveFile,
}: {
  inspector: Inspector;
  onSaveFile: (file: { file_id: string; file_name: string }) => void;
}) {
  const now = useWorks((s) => s.now);
  const work = useWorks((s) => s.items.find((w) => w.work_id === inspector.objectId));
  const error = useWorks((s) => s.detailErrors[inspector.objectId]);
  useEffect(() => {
    void useWorks
      .getState()
      .refresh(inspector.objectId)
      .catch(() => {});
  }, [inspector.objectId]);
  if (!work)
    return (
      <div>
        <p role={error ? "alert" : "status"}>{error ?? "正在加载任务…"}</p>
        {error && (
          <TaskButton
            onClick={() =>
              void useWorks
                .getState()
                .refresh(inspector.objectId)
                .catch(() => {})
            }
          >
            重新验证任务
          </TaskButton>
        )}
      </div>
    );
  const p = presentTask(work, now),
    tab = tabs.some((t) => t.id === inspector.tab) ? inspector.tab : "overview";
  const valid = validSnapshot(work);
  return (
    <div
      className="hp-task-inspector hp-lobe-scope"
      data-status={work.status}
      data-bucket={p.bucket}
    >
      <h3>{work.title}</h3>
      <p>
        <span className="hp-sr-only">{bucketLabels[p.bucket]} · </span>
        <Tag color="orange" variant="borderless">
          {p.label}
        </Tag>{" "}
        <Tag variant="borderless">{typeLabel(work)}</Tag>
      </p>
      {!valid && (
        <p role="alert">
          状态待核实，当前仅可查看与刷新。
          <TaskButton
            onClick={() =>
              void useWorks
                .getState()
                .refresh(work.work_id)
                .catch(() => {})
            }
          >
            刷新状态
          </TaskButton>
        </p>
      )}
      {error && (
        <p role="alert">
          {error}
          <TaskButton
            onClick={() =>
              void useWorks
                .getState()
                .refresh(work.work_id)
                .catch(() => {})
            }
          >
            刷新状态
          </TaskButton>
        </p>
      )}
      <TaskInspectorTabs tabs={tabs} selected={tab} />
      <TaskPanel id="overview" active={tab === "overview"}>
        <section className="hp-inspector-card" aria-label="任务概览">
          <p className="hp-task-objective-detail">{work.requirement.objective}</p>

          <p>{p.attentionReasons.join("；")}</p>
          {p.dataWarnings.map((w) => (
            <p key={w}>{w}</p>
          ))}
          <Descriptions
            className="hp-task-summary-meta hp-task-descriptions"
            colon={false}
            items={[
              {
                key: "schedule",
                label: "执行计划",
                children: (
                  <>
                    {(
                      { immediate: "立即", once: "指定时间", daily: "每天" } as Record<
                        string,
                        string
                      >
                    )[work.requirement.timing?.kind] ?? "计划待核实"}{" "}
                    {work.requirement.timing?.due_at
                      ? formatTaskTime(
                          work.requirement.timing.due_at,
                          work.requirement.timing.timezone,
                        )
                      : (work.requirement.timing?.local_time ?? "")}
                  </>
                ),
              },
              {
                key: "zone",
                label: "计划时区",
                children: work.requirement.timing?.timezone ?? "待核实",
              },
              ...(work.budget
                ? [
                    {
                      key: "budget",
                      label: "模型预算",
                      children: `${(work.budget.used.model_total_tokens ?? 0).toLocaleString()} / ${work.budget.limits.model_total_tokens?.toLocaleString() ?? "未知"}`,
                    },
                    {
                      key: "reserved",
                      label: "已预留",
                      children: (work.budget.reserved.model_total_tokens ?? 0).toLocaleString(),
                    },
                  ]
                : []),
            ]}
          />
          {work.budget && (
            <>
              <TaskDetails>
                <summary>其他预算维度</summary>
                {Object.entries(work.budget.limits).map(([k, v]) => (
                  <p key={k}>
                    {k}：已用 {work.budget!.used[k] ?? 0} / {v} · 已预留{" "}
                    {work.budget!.reserved[k] ?? 0}
                  </p>
                ))}
              </TaskDetails>
            </>
          )}
          <TaskActions work={work} />
          {work.active_coordinator_run_id && (
            <TaskButton
              onClick={() =>
                useShell.getState().openInspector(
                  {
                    kind: "run",
                    objectId: work.active_coordinator_run_id!,
                    origin: { workId: work.work_id },
                  },
                  true,
                )
              }
            >
              查看当前执行
            </TaskButton>
          )}
          {work.continuation?.operation_ref && (
            <p>外部操作结果未决，请查看执行详情核查。没有通用人工解除接口。</p>
          )}
          {!p.secondaryActions.includes("advance") &&
            p.primaryAction !== "advance" &&
            work.status === "active" && (
              <p className="hp-muted">
                当前不具备立即推进条件；请先处理待决事项或等待计划与执行资源。
              </p>
            )}
          {work.conversation_ids.map((id) => (
            <TaskButton
              key={id}
              onClick={() => useShell.getState().navigate({ screen: "ai", conversationId: id })}
            >
              打开关联对话
            </TaskButton>
          ))}
          {work.sources
            ?.filter((s) => s.source_message_id)
            .map((source) => (
              <TaskButton
                key={source.conversation_id}
                onClick={() =>
                  useShell.getState().navigate({
                    screen: "ai",
                    conversationId: source.conversation_id,
                    messageId: source.source_message_id!,
                    sourceWorkId: work.work_id,
                  })
                }
              >
                定位来源消息
              </TaskButton>
            ))}
        </section>
      </TaskPanel>
      <TaskPanel id="outputs" active={tab === "outputs"}>
        <TaskOutputs work={work} onSaveFile={onSaveFile} />
      </TaskPanel>
      <TaskPanel id="resources" active={tab === "resources"}>
        {valid ? <TaskResources work={work} /> : <p>任务状态待核实，资料管理暂不可用。</p>}
      </TaskPanel>
      <TaskPanel id="advanced" active={tab === "advanced"}>
        <RunLookup taskUi />
        <Advanced work={work} />
      </TaskPanel>
    </div>
  );
}
function Advanced({ work }: { work: HpWork }) {
  return (
    <>
      <TaskDescriptions>
        <dt>任务编号</dt>
        <dd>{work.work_id}</dd>
        <dt>要求修订号 / 控制版本</dt>
        <dd>
          r{work.current_requirement_revision} / {work.control_epoch}
        </dd>
        <dt>原始状态</dt>
        <dd>
          {work.status} · {work.continuation?.kind} / {work.continuation?.reason}
        </dd>
        <dt>未决外部操作</dt>
        <dd>{work.continuation?.operation_ref ?? "无"}</dd>
      </TaskDescriptions>
      <TaskEvents workId={work.work_id} />
      {validSnapshot(work) && <TaskTargets work={work} />}
      <TaskForm
        onSubmit={(e) => {
          e.preventDefault();
          const value = new FormData(e.currentTarget).get("run");
          if (typeof value === "string" && value.trim())
            useShell
              .getState()
              .openInspector(
                { kind: "run", objectId: value.trim(), origin: { workId: work.work_id } },
                true,
              );
        }}
      >
        <TaskLabel>
          执行编号
          <TaskInput name="run" required />
        </TaskLabel>
        <TaskButton>查询执行与诊断</TaskButton>
      </TaskForm>
      <TaskDetails>
        <summary>当前完整要求</summary>
        <TaskCode>{JSON.stringify(work.requirement, null, 2)}</TaskCode>
      </TaskDetails>
    </>
  );
}
