import { useEffect } from "react";
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
          <button
            onClick={() =>
              void useWorks
                .getState()
                .refresh(inspector.objectId)
                .catch(() => {})
            }
          >
            重新验证任务
          </button>
        )}
      </div>
    );
  const p = presentTask(work, now),
    tab = tabs.some((t) => t.id === inspector.tab) ? inspector.tab : "overview";
  const valid = validSnapshot(work);
  function select(id: (typeof tabs)[number]["id"]) {
    useShell
      .getState()
      .navigate({ ...useShell.getState().route, inspector: { ...inspector, tab: id } }, true);
  }
  return (
    <div className="hp-task-inspector">
      <h3>{work.title}</h3>
      <p>
        {bucketLabels[p.bucket]} · {p.label}
      </p>
      {!valid && (
        <p role="alert">
          状态待核实，当前仅可查看与刷新。
          <button
            onClick={() =>
              void useWorks
                .getState()
                .refresh(work.work_id)
                .catch(() => {})
            }
          >
            刷新状态
          </button>
        </p>
      )}
      {error && (
        <p role="alert">
          {error}
          <button
            onClick={() =>
              void useWorks
                .getState()
                .refresh(work.work_id)
                .catch(() => {})
            }
          >
            刷新状态
          </button>
        </p>
      )}
      <div role="tablist" aria-label="任务信息" className="hp-file-tabs">
        {tabs.map((t, i) => (
          <button
            key={t.id}
            role="tab"
            id={`task-tab-${t.id}`}
            aria-selected={tab === t.id}
            aria-controls="task-tab-body"
            tabIndex={tab === t.id ? 0 : -1}
            onClick={() => select(t.id)}
            onKeyDown={(e) => {
              const index =
                e.key === "ArrowRight"
                  ? (i + 1) % tabs.length
                  : e.key === "ArrowLeft"
                    ? (i + tabs.length - 1) % tabs.length
                    : e.key === "Home"
                      ? 0
                      : e.key === "End"
                        ? tabs.length - 1
                        : -1;
              if (index < 0) return;
              e.preventDefault();
              select(tabs[index]!.id);
              document.getElementById(`task-tab-${tabs[index]!.id}`)?.focus();
            }}
          >
            {t.label}
          </button>
        ))}
      </div>
      <section role="tabpanel" id="task-tab-body" aria-labelledby={`task-tab-${tab}`}>
        {tab === "overview" && (
          <>
            <p>{work.requirement.objective}</p>
            <p>{typeLabel(work)}</p>
            <p>{p.attentionReasons.join("；")}</p>
            {p.dataWarnings.map((w) => (
              <p key={w}>{w}</p>
            ))}
            <p>
              计划：
              {({ immediate: "立即", once: "指定时间", daily: "每天" } as Record<string, string>)[
                work.requirement.timing?.kind
              ] ?? "计划待核实"}{" "}
              · {work.requirement.timing?.timezone}
              {work.requirement.timing?.due_at
                ? ` · ${formatTaskTime(work.requirement.timing.due_at, work.requirement.timing.timezone)}`
                : work.requirement.timing?.local_time
                  ? ` · ${work.requirement.timing.local_time}`
                  : ""}
            </p>
            {work.budget && (
              <>
                <p>
                  模型预算已用 {work.budget.used.model_total_tokens ?? 0} /{" "}
                  {work.budget.limits.model_total_tokens} · 已预留{" "}
                  {work.budget.reserved.model_total_tokens ?? 0}
                </p>
                <details>
                  <summary>其他预算维度</summary>
                  {Object.entries(work.budget.limits).map(([k, v]) => (
                    <p key={k}>
                      {k}：已用 {work.budget!.used[k] ?? 0} / {v} · 已预留{" "}
                      {work.budget!.reserved[k] ?? 0}
                    </p>
                  ))}
                </details>
              </>
            )}
            <TaskActions work={work} />
            {work.active_coordinator_run_id && (
              <button
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
              </button>
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
              <button
                key={id}
                onClick={() => useShell.getState().navigate({ screen: "ai", conversationId: id })}
              >
                打开关联对话
              </button>
            ))}
          </>
        )}
        {tab === "outputs" && <TaskOutputs work={work} onSaveFile={onSaveFile} />}
        {tab === "resources" &&
          (valid ? <TaskResources work={work} /> : <p>任务状态待核实，资料管理暂不可用。</p>)}
        {tab === "advanced" && <Advanced work={work} />}
      </section>
    </div>
  );
}
function Advanced({ work }: { work: HpWork }) {
  return (
    <>
      <dl>
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
      </dl>
      <TaskEvents workId={work.work_id} />
      {validSnapshot(work) && <TaskTargets work={work} />}
      <form
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
        <label>
          执行编号
          <input name="run" required />
        </label>
        <button>查询执行与诊断</button>
      </form>
      <details>
        <summary>当前完整要求</summary>
        <pre>{JSON.stringify(work.requirement, null, 2)}</pre>
      </details>
    </>
  );
}
