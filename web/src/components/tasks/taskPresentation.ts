import type { HpWork } from "../../api/types";
import { taskActions, validSnapshot, type TaskAction } from "./taskActions";
export type TaskBucket = "attention" | "active" | "waiting" | "ended";
export const bucketLabels: Record<TaskBucket, string> = {
  attention: "需要我处理",
  active: "进行中",
  waiting: "等待或已计划",
  ended: "已结束",
};
export function taskType(work: HpWork) {
  return work.requirement.capability_key === "reminder"
    ? "reminder"
    : work.requirement.capability_key === "research_report"
      ? "research"
      : "general";
}
export function typeLabel(work: HpWork) {
  return (
    (
      {
        reminder: "提醒",
        research_report: "研究",
        generic_work: "通用任务",
        artifact_build: "HTML 生成/修改",
      } as Record<string, string>
    )[work.requirement.capability_key] ?? "通用任务（兼容类型）"
  );
}
const stamp = (value?: string | null, fallback = 0) =>
  value && Number.isFinite(Date.parse(value)) ? Date.parse(value) : fallback;
export function presentTask(work: HpWork, now: number) {
  const c = work.continuation;
  const current = (work.deliveries ?? []).filter(
    (d) => d.requirement_revision === work.current_requirement_revision,
  );
  const attentionReasons: string[] = [];
  if (current.some((d) => d.state === "uncertain")) attentionReasons.push("发送结果待确认");
  if (c?.reason === "side_effect_uncertain" || c?.operation_ref)
    attentionReasons.push("外部操作结果待确认");
  if (current.some((d) => d.state === "failed")) attentionReasons.push("发送失败");
  if (
    c?.reason === "user_acceptance_required" ||
    (work.requirement.acceptance_criteria?.some((c) =>
      c.evidence_types.includes("user_acceptance"),
    ) &&
      work.artifacts?.some(
        (a) =>
          a.role === "deliverable" &&
          a.status === "completed" &&
          a.source_requirement_revision === work.current_requirement_revision &&
          a.accepted_for_revision !== work.current_requirement_revision,
      ))
  )
    attentionReasons.push("有成果等待确认");
  if (
    c?.reason === "budget_exhausted" ||
    (work.budget &&
      Object.entries(work.budget.limits).some(
        ([k, v]) => v > 0 && (work.budget!.used[k] ?? 0) >= v,
      ))
  )
    attentionReasons.push("额度已用尽");
  if (c?.kind === "awaiting_input" && c.reason !== "user_acceptance_required")
    attentionReasons.push("等待你的补充");
  const systemWait =
    c &&
    (["at_time", "retry_after", "awaiting_delivery"].includes(c.kind) ||
      c.reason === "waiting_capacity");
  const resolvedPause =
    work.status === "paused" &&
    c?.kind === "blocked" &&
    c.reason === "delivery_resolved" &&
    !work.active_coordinator_run_id &&
    !c.operation_ref &&
    !current.some((d) => ["failed", "uncertain"].includes(d.state));
  if (c?.kind === "blocked" && !systemWait && !resolvedPause && !attentionReasons.length)
    attentionReasons.push("执行受阻，需要处理");
  const attentionLabel = current.some((d) => d.state === "uncertain")
    ? "发送结果待确认"
    : c?.reason === "side_effect_uncertain" || c?.operation_ref
      ? "外部操作结果待确认"
      : current.some((d) => d.state === "failed")
        ? "发送失败"
        : c?.reason === "user_acceptance_required"
          ? "有成果等待确认"
          : c?.reason === "budget_exhausted"
            ? "额度已用尽"
            : c?.kind === "awaiting_input"
              ? "等待你的补充"
              : c?.kind === "blocked" && !systemWait && !resolvedPause
                ? "执行受阻，需要处理"
                : null;
  const reasonOrder = [
    "发送结果待确认",
    "外部操作结果待确认",
    "有成果等待确认",
    "额度已用尽",
    "等待你的补充",
    "发送失败",
    "执行受阻，需要处理",
  ];
  attentionReasons.sort((a, b) => reasonOrder.indexOf(a) - reasonOrder.indexOf(b));
  let bucket: TaskBucket = "waiting";
  let label = "等待继续";
  const dataWarnings: string[] = [];
  if (!validSnapshot(work)) dataWarnings.push("状态数据不完整，请刷新核实。");
  if (work.status === "completed" || work.status === "stopped") {
    bucket = "ended";
    label = work.status === "completed" ? "已完成" : "已停止";
  } else if (dataWarnings.length) {
    bucket = "attention";
    label = "状态待核实";
  } else if (work.status === "pausing" || work.status === "stopping") {
    bucket = "active";
    label = work.status === "pausing" ? "正在暂停" : "正在停止";
  } else if (attentionLabel) {
    bucket = "attention";
    label = attentionLabel;
  } else if (work.active_coordinator_run_id) {
    bucket = "active";
    label = "正在执行";
  } else if (work.status === "paused") label = "已暂停";
  else if (c.kind === "at_time")
    label = stamp(c.due_at) <= now ? "计划时间已到，等待执行" : "已计划";
  else if (c.kind === "retry_after") label = "等待自动重试";
  else if (c.kind === "awaiting_delivery") label = "等待发送或渠道回执";
  else if (c.reason === "waiting_capacity") label = "等待执行资源";
  else if (c.kind === "ready") label = "准备继续";
  if (
    c &&
    ![
      "ready",
      "blocked",
      "at_time",
      "retry_after",
      "awaiting_input",
      "awaiting_delivery",
      "none",
    ].includes(c.kind)
  )
    dataWarnings.push("包含兼容的后续状态。");
  const actions = taskActions(work, now);
  let primaryAction: TaskAction = "view";
  if (bucket !== "ended" && !dataWarnings.length) {
    primaryAction = attentionReasons.some((r) =>
      ["发送结果待确认", "发送失败", "有成果等待确认"].includes(r),
    )
      ? "outputs"
      : attentionReasons.includes("额度已用尽")
        ? "budget"
        : attentionReasons.includes("等待你的补充")
          ? "resources"
          : actions.includes("advance")
            ? "advance"
            : work.status === "paused"
              ? "resume"
              : "view";
  }
  const rank = attentionReasons.some((r) => ["发送结果待确认", "外部操作结果待确认"].includes(r))
    ? 0
    : attentionReasons.includes("有成果等待确认")
      ? 1
      : attentionReasons.includes("额度已用尽")
        ? 2
        : 3;
  const waitingRank =
    c?.kind === "at_time" ? 0 : c?.kind === "retry_after" ? 1 : work.status === "paused" ? 2 : 3;
  const sortKey =
    bucket === "waiting"
      ? [waitingRank, waitingRank < 2 ? stamp(c?.due_at, Number.MAX_SAFE_INTEGER) : 0]
      : bucket === "ended"
        ? [
            0,
            -stamp(
              work.status === "completed" ? work.completed_at : work.stopped_at,
              stamp(work.updated_at),
            ),
          ]
        : [
            bucket === "attention"
              ? rank
              : ["pausing", "stopping"].includes(work.status)
                ? attentionReasons.length
                  ? 0
                  : 1
                : 2,
            -stamp(work.updated_at),
          ];
  return {
    bucket,
    label,
    reasonLabel: attentionReasons[0] ?? label,
    attentionReasons,
    primaryAction,
    secondaryActions: actions.filter((a) => a !== primaryAction),
    sortKey,
    evidenceRefs: current
      .filter((d) => ["failed", "uncertain"].includes(d.state))
      .map((d) => d.delivery_id),
    dataWarnings,
  };
}
export function compareTasks(a: HpWork, b: HpWork, now: number) {
  const x = presentTask(a, now).sortKey,
    y = presentTask(b, now).sortKey;
  return x[0]! - y[0]! || x[1]! - y[1]! || a.work_id.localeCompare(b.work_id);
}
export function taskTime(work: HpWork) {
  if (["paused", "pausing", "stopping", "stopped", "completed"].includes(work.status)) return "";
  const time = ["at_time", "retry_after"].includes(work.continuation?.kind)
    ? work.continuation.due_at
    : work.schedule?.desired_enabled
      ? work.schedule.next_due_at
      : null;
  return time ? formatTaskTime(time, work.requirement.timing?.timezone) : "";
}
export function formatTaskTime(time: string, zone = "UTC") {
  try {
    return `${new Intl.DateTimeFormat("zh-CN", { timeZone: zone, dateStyle: "short", timeStyle: "short" }).format(new Date(time))}（${zone}）`;
  } catch {
    return "时间待核实";
  }
}
