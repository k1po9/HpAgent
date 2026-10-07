import type { HpWork, HpTaskRun } from "../../api/types";
export type TaskAction =
  "view" | "pause" | "resume" | "stop" | "edit" | "advance" | "budget" | "outputs" | "resources";
export const actionLabels: Record<TaskAction, string> = {
  view: "查看详情",
  pause: "暂停任务",
  resume: "恢复任务",
  stop: "停止任务",
  edit: "修改要求",
  advance: "继续执行",
  budget: "提高预算",
  outputs: "查看成果与投递",
  resources: "补充资料",
};
export function validSnapshot(work: HpWork) {
  const c = work.continuation;
  return (
    ["active", "pausing", "paused", "stopping", "stopped", "completed"].includes(work.status) &&
    Boolean(c && typeof c.kind === "string" && typeof c.reason === "string") &&
    (!["at_time", "retry_after"].includes(c?.kind) ||
      (Boolean(c?.due_at && /(?:Z|[+-]\d{2}:\d{2})$/.test(c.due_at)) &&
        Number.isFinite(Date.parse(c!.due_at!))))
  );
}
export function taskActions(work: HpWork, now: number): TaskAction[] {
  if (!validSnapshot(work)) return ["view"];
  const actions: TaskAction[] = ["view", "outputs", "resources"];
  if (work.status === "active") actions.push("pause");
  if (work.status === "paused") actions.push("resume");
  if (["active", "pausing", "paused"].includes(work.status)) {
    actions.push("stop");
    if (["reminder", "research_report", "generic_work"].includes(work.requirement.capability_key))
      actions.push("edit");
  }
  if (!["stopped", "completed"].includes(work.status) && work.budget) actions.push("budget");
  const c = work.continuation;
  if (
    work.status === "active" &&
    !work.active_coordinator_run_id &&
    !c.operation_ref &&
    ["ready", "blocked", "at_time", "retry_after"].includes(c.kind) &&
    ![
      "budget_exhausted",
      "waiting_capacity",
      "side_effect_uncertain",
      "user_acceptance_required",
    ].includes(c.reason) &&
    !work.deliveries.some(
      (d) =>
        d.requirement_revision === work.current_requirement_revision &&
        ["failed", "uncertain"].includes(d.state),
    ) &&
    (!c.due_at || Date.parse(c.due_at) <= now)
  )
    actions.push("advance");
  return actions;
}
export function acceptanceEligible(
  work: HpWork,
  artifact: HpWork["artifacts"][number],
  run?: HpTaskRun,
) {
  return (
    validSnapshot(work) &&
    work.status === "active" &&
    !work.active_coordinator_run_id &&
    !work.continuation.operation_ref &&
    artifact.role === "deliverable" &&
    artifact.status === "completed" &&
    artifact.source_requirement_revision === work.current_requirement_revision &&
    artifact.accepted_for_revision !== work.current_requirement_revision &&
    (!work.continuation.receipt_ref ||
      work.continuation.reason !== "user_acceptance_required" ||
      work.continuation.receipt_ref === artifact.artifact_version_id) &&
    Boolean(
      work.requirement.acceptance_criteria?.some((c) =>
        c.evidence_types.includes("user_acceptance"),
      ),
    ) &&
    Boolean(
      run &&
      run.work_id === work.work_id &&
      run.status === "succeeded" &&
      run.requirement_revision === work.current_requirement_revision &&
      run.work_control_epoch === work.control_epoch &&
      run.result_json?.evidence?.some(
        (e) =>
          ["artifact_version", "research_report"].includes(e.type) &&
          e.ref === artifact.artifact_version_id,
      ),
    )
  );
}
export function canRetryDelivery(work: HpWork, delivery: HpWork["deliveries"][number]) {
  return (
    validSnapshot(work) &&
    !["stopped", "completed"].includes(work.status) &&
    ["failed", "uncertain"].includes(delivery.state) &&
    (delivery.purpose === "fact" ||
      (delivery.purpose === "fulfillment" &&
        work.status === "active" &&
        delivery.requirement_revision === work.current_requirement_revision &&
        Boolean(delivery.notification_id) &&
        delivery.notification_id === work.continuation.receipt_ref))
  );
}
